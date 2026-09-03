"""Pure deterministic v9 data-quality, routing, and action transitions."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime

from app.domain.integrations import (
    DataQualityFinding,
    DataQualityRule,
    DataQualityRuleKind,
    DeliveryStatus,
    ExternalActionDraft,
    ExternalActionEvent,
    ExternalActionKind,
    PartnerClassification,
)


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _finding(
    *,
    run_id: str,
    connector_id: str,
    rule: DataQualityRule,
    source_reference: str,
    detail: str,
) -> DataQualityFinding:
    fingerprint = hashlib.sha256(
        f"{connector_id}\0{rule.rule_id}\0{source_reference}\0{detail}".encode()
    ).hexdigest()
    return DataQualityFinding(
        finding_id=hashlib.sha256(f"{run_id}\0{fingerprint}".encode()).hexdigest(),
        fingerprint=fingerprint,
        run_id=run_id,
        connector_id=connector_id,
        rule_id=rule.rule_id,
        severity=rule.severity,
        title=f"{rule.kind.value.replace('_', ' ').title()} detected",
        description=detail,
        source_reference=source_reference[:256],
    )


def evaluate_quality_records(
    *,
    run_id: str,
    connector_id: str,
    records_by_type: dict[str, list[dict[str, object]]],
    rules: list[DataQualityRule],
) -> list[DataQualityFinding]:
    findings: list[DataQualityFinding] = []
    for rule in rules:
        records = records_by_type.get(rule.record_type, [])
        if not records:
            continue
        key_field = str(rule.parameters.get("key_field", rule.fields[0] if rule.fields else "id"))
        if rule.kind is DataQualityRuleKind.duplicate_key:
            seen: set[str] = set()
            for record in records:
                key = str(record.get(key_field, "")).strip()
                if not key or key not in seen:
                    seen.add(key)
                    continue
                findings.append(
                    _finding(
                        run_id=run_id,
                        connector_id=connector_id,
                        rule=rule,
                        source_reference=key,
                        detail=f"Duplicate business key in {rule.record_type}.",
                    )
                )
            continue
        key_values = {
            str(record.get(key_field, "")).strip()
            for record in records
            if str(record.get(key_field, "")).strip()
        }
        for record in records:
            reference = str(record.get(key_field, "unknown")).strip() or "unknown"
            detail: str | None = None
            if rule.kind is DataQualityRuleKind.required_value:
                missing = [field for field in rule.fields if _missing(record.get(field))]
                if missing:
                    detail = f"Required fields are empty: {', '.join(missing)}."
            elif rule.kind is DataQualityRuleKind.code_list and rule.fields:
                allowed = {str(value) for value in rule.parameters.get("allowed_values", [])}
                value = str(record.get(rule.fields[0], ""))
                if value and allowed and value not in allowed:
                    detail = f"{rule.fields[0]} contains a value outside the approved code list."
            elif rule.kind is DataQualityRuleKind.broken_reference and rule.fields:
                value = str(record.get(rule.fields[0], "")).strip()
                if value and value not in key_values:
                    detail = f"{rule.fields[0]} references an unavailable business key."
            elif rule.kind is DataQualityRuleKind.type_mismatch and rule.fields:
                value = record.get(rule.fields[0])
                expected = str(rule.parameters.get("expected_type", "string"))
                valid = (
                    (expected == "string" and isinstance(value, str))
                    or (
                        expected == "number"
                        and isinstance(value, (int, float))
                        and not isinstance(value, bool)
                    )
                    or (expected == "boolean" and isinstance(value, bool))
                )
                if value is not None and not valid:
                    detail = f"{rule.fields[0]} does not match the configured {expected} type."
            elif rule.kind in {
                DataQualityRuleKind.currency_mismatch,
                DataQualityRuleKind.subsidiary_mismatch,
            } and len(rule.fields) >= 2:
                left = str(record.get(rule.fields[0], "")).strip()
                right = str(record.get(rule.fields[1], "")).strip()
                if left and right and left.casefold() != right.casefold():
                    detail = f"{rule.fields[0]} and {rule.fields[1]} are inconsistent."
            elif rule.kind is DataQualityRuleKind.threshold and rule.fields:
                try:
                    numeric = float(record.get(rule.fields[0]))
                    minimum = float(rule.parameters.get("minimum", -math.inf))
                    maximum = float(rule.parameters.get("maximum", math.inf))
                    if numeric < minimum or numeric > maximum:
                        detail = f"{rule.fields[0]} is outside the approved threshold."
                except (TypeError, ValueError):
                    detail = f"{rule.fields[0]} is not a valid numeric value."
            if detail:
                findings.append(
                    _finding(
                        run_id=run_id,
                        connector_id=connector_id,
                        rule=rule,
                        source_reference=reference,
                        detail=detail,
                    )
                )
    return findings


def classify_partner(
    *,
    artifact_id: str,
    content: str,
    partners: list[tuple[str, str, list[str]]],
) -> PartnerClassification | None:
    folded = content.casefold()
    matches: list[tuple[str, str, list[str]]] = []
    for reference, label, aliases in partners:
        values = [reference, label, *aliases]
        matched = [value for value in values if value and value.casefold() in folded]
        if reference.casefold() in folded or len(matched) >= 2:
            matches.append((reference, label, matched))
    if not matches:
        return None
    matches.sort(key=lambda value: (-len(value[2]), value[0].casefold()))
    top = matches[0]
    ambiguous = len(matches) > 1 and len(matches[1][2]) == len(top[2])
    fingerprint = hashlib.sha256(
        f"{artifact_id}\0{top[0]}\0{','.join(sorted(top[2]))}".encode()
    ).hexdigest()
    return PartnerClassification(
        classification_id=fingerprint,
        artifact_id=artifact_id,
        partner_reference=top[0],
        partner_label=top[1],
        confidence=0 if ambiguous else min(100, 70 + len(top[2]) * 10),
        matched_values=top[2],
        ambiguous=ambiguous,
    )


def build_external_action(
    *,
    finding: DataQualityFinding,
    connector_id: str,
    destination_ref: str,
    destination_label: str,
    kind: ExternalActionKind,
) -> ExternalActionDraft:
    fingerprint = hashlib.sha256(
        f"{finding.fingerprint}\0{connector_id}\0{destination_ref}\0{kind.value}".encode()
    ).hexdigest()
    preview = (
        f"[{finding.severity.upper()}] {finding.title}\n"
        f"Reference: {finding.source_reference}\n"
        f"Rule: {finding.rule_id}\nReview required."
    )
    return ExternalActionDraft(
        action_id=hashlib.sha256(f"action\0{fingerprint}".encode()).hexdigest(),
        fingerprint=fingerprint,
        kind=kind,
        connector_id=connector_id,
        destination_ref=destination_ref,
        destination_label=destination_label,
        finding_id=finding.finding_id,
        finding_revision=finding.revision,
        severity=finding.severity,
        source_reference=finding.source_reference,
        rendered_preview=preview,
    )


def transition_action(
    action: ExternalActionDraft,
    to_status: DeliveryStatus,
    *,
    approval_origin: str | None = None,
    detail_code: str | None = None,
) -> tuple[ExternalActionDraft, ExternalActionEvent]:
    allowed = {
        DeliveryStatus.pending_confirmation: {
            DeliveryStatus.approved,
            DeliveryStatus.cancelled,
        },
        DeliveryStatus.approved: {DeliveryStatus.executing},
        DeliveryStatus.executing: {
            DeliveryStatus.succeeded,
            DeliveryStatus.failed,
            DeliveryStatus.delivery_unknown,
        },
    }
    if to_status not in allowed.get(action.status, set()):
        raise ValueError("invalid external action transition")
    revision = action.revision + 1
    updated = action.model_copy(
        update={
            "status": to_status,
            "approval_origin": approval_origin or action.approval_origin,
            "revision": revision,
            "updated_at": datetime.now(UTC),
        }
    )
    event = ExternalActionEvent(
        event_id=hashlib.sha256(
            f"{action.action_id}\0{revision}\0{to_status.value}".encode()
        ).hexdigest(),
        action_id=action.action_id,
        from_status=action.status,
        to_status=to_status,
        revision=revision,
        approval_origin=updated.approval_origin,
        detail_code=detail_code,
    )
    return updated, event
