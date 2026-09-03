"""Pure deterministic reconciliation across NetSuite, WMS, and Amazon FBA."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from app.domain.context import FulfillmentRecordMapping
from app.domain.fulfillment_reconciliation import (
    FulfillmentGapKind,
    FulfillmentReconciliationFinding,
    FulfillmentReconciliationRequest,
    FulfillmentReconciliationRun,
    FulfillmentReconciliationWarning,
    FulfillmentStage,
)

_MAX_FINDINGS = 1_000


@dataclass(frozen=True)
class FulfillmentSource:
    connector_id: str
    stage: FulfillmentStage
    source_label: str
    mappings: list[FulfillmentRecordMapping]
    records: dict[str, list[dict[str, object]]]


@dataclass
class _Aggregate:
    quantity: Decimal = Decimal(0)
    record_count: int = 0
    updated_at: datetime | None = None


def _hash(*values: object) -> str:
    return hashlib.sha256("\0".join(str(value) for value in values).encode()).hexdigest()


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or abs(parsed) > Decimal("1e18"):
        return None
    return parsed


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _reference(value: object) -> str:
    return " ".join(str(value or "").replace("\0", "").split())[:256]


def _number(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000001")))


def _warning_models(
    warnings: Counter[tuple[str | None, str | None, str]],
) -> list[FulfillmentReconciliationWarning]:
    return [
        FulfillmentReconciliationWarning(
            connector_id=connector_id,
            flow_id=flow_id,
            code=code,  # type: ignore[arg-type]
            count=count,
        )
        for (connector_id, flow_id, code), count in sorted(
            warnings.items(), key=lambda item: json.dumps(item[0], default=str)
        )
    ]


def reconcile_fulfillment_records(
    request: FulfillmentReconciliationRequest,
    sources: list[FulfillmentSource],
    *,
    unavailable_connector_ids: list[str] | None = None,
) -> FulfillmentReconciliationRun:
    unavailable = sorted(set(unavailable_connector_ids or []))
    warnings: Counter[tuple[str | None, str | None, str]] = Counter(
        (connector_id, None, "connector_unavailable") for connector_id in unavailable
    )
    selected_flows = set(request.flow_ids)
    aggregates: dict[
        tuple[FulfillmentStage, str],
        dict[tuple[str, str, tuple[str, ...]], _Aggregate],
    ] = defaultdict(dict)
    labels = {source.stage: source.source_label for source in sources}
    inspected = 0

    for source in sources:
        if not source.mappings:
            warnings[(source.connector_id, None, "mapping_not_configured")] += 1
        for mapping in source.mappings:
            if selected_flows and mapping.flow_id not in selected_flows:
                continue
            entity_records = next(
                (
                    records
                    for entity, records in source.records.items()
                    if entity.casefold() == mapping.entity_name.casefold()
                ),
                [],
            )
            target = aggregates[(source.stage, mapping.flow_id)]
            for record in entity_records:
                inspected += 1
                sku = _reference(record.get(mapping.sku_field)).upper()
                quantity = _decimal(record.get(mapping.quantity_field))
                reference = (
                    _reference(record.get(mapping.reference_field)).upper()
                    if mapping.reference_field
                    else ""
                )
                dimensions = tuple(
                    _reference(record.get(field)).upper()
                    for field in mapping.dimension_fields
                )
                if not sku or quantity is None or quantity < 0:
                    warnings[(source.connector_id, mapping.flow_id, "invalid_record")] += 1
                    continue
                key = (sku, reference, dimensions)
                aggregate = target.setdefault(key, _Aggregate())
                aggregate.quantity += quantity
                aggregate.record_count += 1
                timestamp = (
                    _timestamp(record.get(mapping.timestamp_field))
                    if mapping.timestamp_field
                    else None
                )
                if timestamp and (
                    aggregate.updated_at is None or timestamp > aggregate.updated_at
                ):
                    aggregate.updated_at = timestamp

    transitions = [
        (FulfillmentStage.netsuite, FulfillmentStage.wms, "netsuite_to_wms"),
        (FulfillmentStage.wms, FulfillmentStage.amazon_fba, "wms_to_amazon_fba"),
    ]
    flow_ids = sorted(
        selected_flows
        or {
            flow_id
            for _, flow_id in aggregates
        }
    )
    findings: list[FulfillmentReconciliationFinding] = []
    absolute_tolerance = Decimal(str(request.absolute_quantity_tolerance))
    relative_tolerance = Decimal(str(request.relative_quantity_tolerance_percent))

    def add_finding(
        *,
        kind: FulfillmentGapKind,
        flow_id: str,
        transition: str,
        key: tuple[str, str, tuple[str, ...]],
        upstream: _Aggregate | None,
        downstream: _Aggregate | None,
        upstream_stage: FulfillmentStage,
        downstream_stage: FulfillmentStage,
    ) -> None:
        if len(findings) >= _MAX_FINDINGS:
            return
        sku, reference, dimensions = key
        upstream_quantity = upstream.quantity if upstream else None
        downstream_quantity = downstream.quantity if downstream else None
        difference = (
            abs(upstream_quantity - downstream_quantity)
            if upstream_quantity is not None and downstream_quantity is not None
            else None
        )
        percent = None
        if difference is not None:
            denominator = max(
                abs(upstream_quantity), abs(downstream_quantity), Decimal("0.000001")
            )
            percent = difference / denominator * Decimal(100)
        if kind is FulfillmentGapKind.missing_downstream_record:
            explanation = (
                f"{labels[upstream_stage]} contains {sku}"
                f"{f' for {reference}' if reference else ''}, but no matching record was "
                f"found in {labels[downstream_stage]}."
            )
            recommended = (
                "Verify export/queue status, identifier mapping, and downstream "
                "rejection logs."
            )
            severity = "high"
        elif kind is FulfillmentGapKind.missing_upstream_record:
            explanation = (
                f"{labels[downstream_stage]} contains {sku}"
                f"{f' for {reference}' if reference else ''}, but no matching upstream record "
                f"was found in {labels[upstream_stage]}."
            )
            recommended = (
                "Verify manual creation, delayed upstream posting, and duplicate "
                "identifier aliases."
            )
            severity = "medium"
        elif kind is FulfillmentGapKind.duplicate_handoff_key:
            explanation = (
                f"The same {flow_id} handoff key appears more than once in one or both systems; "
                "quantities were aggregated only for comparison."
            )
            recommended = (
                "Review duplicate transaction lines and confirm whether aggregation "
                "is intended."
            )
            severity = "medium"
        else:
            assert difference is not None and percent is not None
            explanation = (
                f"{flow_id} quantity for {sku} differs between {labels[upstream_stage]} "
                f"({_number(upstream_quantity)}) and {labels[downstream_stage]} "
                f"({_number(downstream_quantity)}), a {_number(percent)}% difference."
            )
            recommended = (
                "Check units of measure, partial receipts/shipments, cancellations, returns, "
                "status filters, and source cut-off times."
            )
            severity = "high" if percent >= 25 else "medium" if percent >= 10 else "low"
        findings.append(
            FulfillmentReconciliationFinding(
                finding_id=_hash(kind.value, flow_id, transition, sku, reference, *dimensions),
                kind=kind,
                flow_id=flow_id,
                transition=transition,  # type: ignore[arg-type]
                severity=severity,
                sku_reference=sku,
                handoff_reference=reference or None,
                dimensions=list(dimensions),
                upstream_source_label=labels[upstream_stage],
                downstream_source_label=labels[downstream_stage],
                upstream_quantity=(
                    _number(upstream_quantity) if upstream_quantity is not None else None
                ),
                downstream_quantity=(
                    _number(downstream_quantity) if downstream_quantity is not None else None
                ),
                absolute_difference=_number(difference) if difference is not None else None,
                difference_percent=_number(percent) if percent is not None else None,
                upstream_updated_at=upstream.updated_at if upstream else None,
                downstream_updated_at=downstream.updated_at if downstream else None,
                explanation=explanation,
                recommended_check=recommended,
            )
        )

    for upstream_stage, downstream_stage, transition in transitions:
        if upstream_stage not in labels or downstream_stage not in labels:
            continue
        for flow_id in flow_ids:
            upstream_values = aggregates.get((upstream_stage, flow_id))
            downstream_values = aggregates.get((downstream_stage, flow_id))
            if upstream_values is None or downstream_values is None:
                warnings[(None, flow_id, "flow_not_shared")] += 1
                continue
            all_keys = sorted(set(upstream_values) | set(downstream_values))
            for key in all_keys:
                upstream = upstream_values.get(key)
                downstream = downstream_values.get(key)
                if upstream is None:
                    add_finding(
                        kind=FulfillmentGapKind.missing_upstream_record,
                        flow_id=flow_id,
                        transition=transition,
                        key=key,
                        upstream=None,
                        downstream=downstream,
                        upstream_stage=upstream_stage,
                        downstream_stage=downstream_stage,
                    )
                    continue
                if downstream is None:
                    add_finding(
                        kind=FulfillmentGapKind.missing_downstream_record,
                        flow_id=flow_id,
                        transition=transition,
                        key=key,
                        upstream=upstream,
                        downstream=None,
                        upstream_stage=upstream_stage,
                        downstream_stage=downstream_stage,
                    )
                    continue
                if upstream.record_count > 1 or downstream.record_count > 1:
                    add_finding(
                        kind=FulfillmentGapKind.duplicate_handoff_key,
                        flow_id=flow_id,
                        transition=transition,
                        key=key,
                        upstream=upstream,
                        downstream=downstream,
                        upstream_stage=upstream_stage,
                        downstream_stage=downstream_stage,
                    )
                difference = abs(upstream.quantity - downstream.quantity)
                denominator = max(
                    abs(upstream.quantity), abs(downstream.quantity), Decimal("0.000001")
                )
                percent = difference / denominator * Decimal(100)
                if difference > absolute_tolerance and percent > relative_tolerance:
                    add_finding(
                        kind=FulfillmentGapKind.quantity_mismatch,
                        flow_id=flow_id,
                        transition=transition,
                        key=key,
                        upstream=upstream,
                        downstream=downstream,
                        upstream_stage=upstream_stage,
                        downstream_stage=downstream_stage,
                    )
    if len(findings) >= _MAX_FINDINGS:
        warnings[(None, None, "finding_limit_reached")] += 1
    status = "unavailable" if len(unavailable) == 3 else "partial" if unavailable else "current"
    return FulfillmentReconciliationRun(
        run_id=_hash(
            "fulfillment-reconciliation",
            request.model_dump_json(),
            *[finding.finding_id for finding in findings],
            *unavailable,
        ),
        status=status,
        findings=findings,
        warnings=_warning_models(warnings),
        inspected_records=inspected,
    )
