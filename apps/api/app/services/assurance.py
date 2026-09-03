"""Pure deterministic document-assurance evaluation and review transitions."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from app.domain.assurance import (
    AssuranceFinding,
    AssuranceFindingStatus,
    AssuranceRule,
    AssuranceRulePack,
    AssuranceRuleType,
    AssuranceSeverity,
    FindingReview,
)
from app.domain.evidence import ArtifactSection, DocumentArtifact, DocumentKind, DocumentRevision

_PLACEHOLDER_RE = re.compile(
    r"(?:\bTBD\b|\bTODO\b|\bFIXME\b|to be (?:decided|defined|confirmed)|未定|要確認|後日)",
    re.IGNORECASE,
)
_MEASURABLE_RE = re.compile(
    r"(?:\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds?|秒|分|%|rps|tps|users?|件|GB|MB)|"
    r"(?:less|under|within|at least|以下|以内|以上)\s*\d+)",
    re.IGNORECASE,
)
_OWNER_RE = re.compile(
    r"(?:owner|owned by|responsible|accountable|担当|責任者|オーナー)\s*[:：]?\s*\S+",
    re.IGNORECASE,
)
_VAGUE_RE = re.compile(
    r"(?:\b(?:fast|easy|user[- ]friendly|appropriate|as needed|and so on)\b|"
    r"なるべく|できるだけ|適切(?:に|な)?|必要に応じて|など)",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_ANCHOR_RE = re.compile(r"\[[^\]]+\]\(#([^)]+)\)")
_DEFINITION_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_.-]{1,31})\s*(?:=|:|：|means|とは)\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_KEYED_REQUIREMENT_RE = re.compile(
    r"\b((?:REQ|NFR)[-_ ]?\d{1,8})\b[^.。\n]*(must not|shall not|must|shall|"
    r"してはならない|しなければならない)",
    re.IGNORECASE,
)


def _markdown_anchor(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9\u3040-\u30ff\u3400-\u9fff -]", "", value.casefold())
    return re.sub(r"\s+", "-", normalized.strip())


def _has_broken_internal_reference(content: str) -> bool:
    headings = {_markdown_anchor(value) for value in _HEADING_RE.findall(content)}
    return any(reference.casefold() not in headings for reference in _ANCHOR_RE.findall(content))


def _has_inconsistent_definition(content: str) -> bool:
    definitions: dict[str, str] = {}
    for key, value in _DEFINITION_RE.findall(content):
        normalized_key = key.casefold()
        normalized_value = " ".join(value.casefold().split())
        existing = definitions.get(normalized_key)
        if existing is not None and existing != normalized_value:
            return True
        definitions[normalized_key] = normalized_value
    return False


def _has_conflicting_keyed_requirement(content: str) -> bool:
    polarity: dict[str, set[bool]] = {}
    for key, modal in _KEYED_REQUIREMENT_RE.findall(content):
        negative = "not" in modal.casefold() or modal == "してはならない"
        polarity.setdefault(key.replace(" ", "-").casefold(), set()).add(negative)
    return any(len(values) > 1 for values in polarity.values())


def _pack_hash(rules: list[AssuranceRule]) -> str:
    payload = json.dumps(
        [rule.model_dump(mode="json") for rule in rules],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def builtin_rule_pack() -> AssuranceRulePack:
    all_documents = list(DocumentKind)
    rules = [
        AssuranceRule(
            rule_id="builtin.unresolved-placeholder",
            rule_type=AssuranceRuleType.forbidden_placeholder,
            title="Unresolved placeholder",
            description="Resolve placeholders before review or release.",
            applies_to=all_documents,
            severity=AssuranceSeverity.warning,
        ),
        AssuranceRule(
            rule_id="builtin.requirement-acceptance",
            rule_type=AssuranceRuleType.acceptance_criteria,
            title="Acceptance criteria missing",
            description="Requirements need explicit, verifiable acceptance criteria.",
            applies_to=[DocumentKind.requirement],
            severity=AssuranceSeverity.high,
            terms=["acceptance criteria", "given when then", "受入条件", "完了条件"],
        ),
        AssuranceRule(
            rule_id="builtin.requirement-owner",
            rule_type=AssuranceRuleType.required_owner,
            title="Requirement owner missing",
            description="Identify the accountable role or team for the requirement.",
            applies_to=[DocumentKind.requirement],
            severity=AssuranceSeverity.warning,
        ),
        AssuranceRule(
            rule_id="builtin.nfr-measurable",
            rule_type=AssuranceRuleType.measurable_target,
            title="NFR is not measurable",
            description="Add a measurable target, load or scope, and measurement method.",
            applies_to=[DocumentKind.nfr_security],
            severity=AssuranceSeverity.high,
        ),
        AssuranceRule(
            rule_id="builtin.cutover-rollback",
            rule_type=AssuranceRuleType.rollback_required,
            title="Rollback plan missing",
            description="Cutover plans require rollback triggers, steps, and ownership.",
            applies_to=[DocumentKind.cutover],
            severity=AssuranceSeverity.critical,
            terms=["rollback", "backout", "切り戻し", "ロールバック"],
        ),
        AssuranceRule(
            rule_id="builtin.test-traceability",
            rule_type=AssuranceRuleType.traceability,
            title="Test traceability missing",
            description="Reference the requirement or design identifiers validated by the test.",
            applies_to=[DocumentKind.test],
            severity=AssuranceSeverity.high,
            terms=["REQ-", "NFR-", "ADR-", "要件ID", "要求ID"],
        ),
        AssuranceRule(
            rule_id="builtin.interface-source-target",
            rule_type=AssuranceRuleType.required_section,
            title="Interface source or target missing",
            description="Interface designs must identify both source and target systems.",
            applies_to=[DocumentKind.interface, DocumentKind.data_mapping],
            severity=AssuranceSeverity.high,
            terms=["source", "target", "送信元", "送信先", "ソース", "ターゲット"],
            parameters={"minimum_term_matches": 2},
        ),
        AssuranceRule(
            rule_id="builtin.requirement-vague-language",
            rule_type=AssuranceRuleType.vague_language,
            title="Requirement uses vague language",
            description="Replace vague wording with measurable, testable behavior.",
            applies_to=[DocumentKind.requirement, DocumentKind.nfr_security],
            severity=AssuranceSeverity.warning,
        ),
        AssuranceRule(
            rule_id="builtin.broken-internal-reference",
            rule_type=AssuranceRuleType.broken_reference,
            title="Internal document reference is broken",
            description="Update the link to a heading present in this document revision.",
            applies_to=all_documents,
            severity=AssuranceSeverity.warning,
        ),
        AssuranceRule(
            rule_id="builtin.terminology-definition-conflict",
            rule_type=AssuranceRuleType.terminology_consistency,
            title="Term has conflicting definitions",
            description="Use one definition for the same explicitly defined term.",
            applies_to=all_documents,
            severity=AssuranceSeverity.high,
        ),
        AssuranceRule(
            rule_id="builtin.keyed-requirement-conflict",
            rule_type=AssuranceRuleType.conflicting_statement,
            title="Requirement contains conflicting obligations",
            description="Resolve the positive and negative obligation for the same requirement ID.",
            applies_to=[DocumentKind.requirement, DocumentKind.nfr_security],
            severity=AssuranceSeverity.high,
        ),
        AssuranceRule(
            rule_id="builtin.operations-support-owner",
            rule_type=AssuranceRuleType.required_owner,
            title="Operations owner missing",
            description="Operations and support documents require an accountable role or team.",
            applies_to=[DocumentKind.operations],
            severity=AssuranceSeverity.high,
        ),
    ]
    return AssuranceRulePack(
        pack_id="meeting-intelligence-builtins",
        version="1.0.0",
        issuer="meeting-intelligence-copilot",
        rules=rules,
        manifest_sha256=_pack_hash(rules),
        trusted=True,
    )


def _finding(
    *,
    run_id: str,
    rule: AssuranceRule,
    artifact: DocumentArtifact,
    revision: DocumentRevision,
    section: ArtifactSection | None,
) -> AssuranceFinding:
    locator = section.locator if section is not None else None
    locator_key = locator.model_dump_json() if locator is not None else "document"
    fingerprint = hashlib.sha256(
        f"{rule.rule_id}\0{artifact.artifact_id}\0{locator_key}".encode()
    ).hexdigest()
    finding_id = hashlib.sha256(
        f"{fingerprint}\0{revision.revision_id}".encode()
    ).hexdigest()
    governance_kind = {
        AssuranceRuleType.traceability: "dependency",
        AssuranceRuleType.erp_metadata: "architecture_impact",
        AssuranceRuleType.architecture_standard: "constraint",
        AssuranceRuleType.rollback_required: "risk",
    }.get(rule.rule_type, "action")
    return AssuranceFinding(
        finding_id=finding_id,
        fingerprint=fingerprint,
        run_id=run_id,
        rule_id=rule.rule_id,
        artifact_id=artifact.artifact_id,
        revision_id=revision.revision_id,
        source_reference=artifact.source_reference,
        document_kind=artifact.document_kind,
        title=rule.title,
        description=rule.description,
        severity=rule.severity,
        locator=locator or {},
        suggested_governance_kind=governance_kind,
    )


def evaluate_artifact(
    *,
    run_id: str,
    artifact: DocumentArtifact,
    revision: DocumentRevision,
    sections: list[ArtifactSection],
    rules: list[AssuranceRule],
) -> list[AssuranceFinding]:
    content = "\n".join(section.content for section in sections)
    normalized = content.casefold()
    findings: list[AssuranceFinding] = []
    for rule in rules:
        if not rule.enabled or artifact.document_kind not in rule.applies_to:
            continue
        if rule.rule_type is AssuranceRuleType.forbidden_placeholder:
            for section in sections:
                if _PLACEHOLDER_RE.search(section.content):
                    findings.append(
                        _finding(
                            run_id=run_id,
                            rule=rule,
                            artifact=artifact,
                            revision=revision,
                            section=section,
                        )
                    )
        elif rule.rule_type is AssuranceRuleType.measurable_target:
            if not _MEASURABLE_RE.search(content):
                findings.append(
                    _finding(
                        run_id=run_id,
                        rule=rule,
                        artifact=artifact,
                        revision=revision,
                        section=None,
                    )
                )
        elif rule.rule_type is AssuranceRuleType.required_owner:
            if not _OWNER_RE.search(content):
                findings.append(
                    _finding(
                        run_id=run_id,
                        rule=rule,
                        artifact=artifact,
                        revision=revision,
                        section=None,
                    )
                )
        elif rule.rule_type in {
            AssuranceRuleType.acceptance_criteria,
            AssuranceRuleType.rollback_required,
            AssuranceRuleType.traceability,
            AssuranceRuleType.required_reference,
        }:
            if not any(term.casefold() in normalized for term in rule.terms):
                findings.append(
                    _finding(
                        run_id=run_id,
                        rule=rule,
                        artifact=artifact,
                        revision=revision,
                        section=None,
                    )
                )
        elif rule.rule_type is AssuranceRuleType.required_section:
            matches = sum(term.casefold() in normalized for term in rule.terms)
            minimum = int(rule.parameters.get("minimum_term_matches", 1))
            if matches < minimum:
                findings.append(
                    _finding(
                        run_id=run_id,
                        rule=rule,
                        artifact=artifact,
                        revision=revision,
                        section=None,
                    )
                )
        elif rule.rule_type is AssuranceRuleType.vague_language:
            for section in sections:
                if _VAGUE_RE.search(section.content):
                    findings.append(
                        _finding(
                            run_id=run_id,
                            rule=rule,
                            artifact=artifact,
                            revision=revision,
                            section=section,
                        )
                    )
        elif rule.rule_type is AssuranceRuleType.broken_reference:
            if _has_broken_internal_reference(content):
                findings.append(
                    _finding(
                        run_id=run_id,
                        rule=rule,
                        artifact=artifact,
                        revision=revision,
                        section=None,
                    )
                )
        elif rule.rule_type is AssuranceRuleType.terminology_consistency:
            if _has_inconsistent_definition(content):
                findings.append(
                    _finding(
                        run_id=run_id,
                        rule=rule,
                        artifact=artifact,
                        revision=revision,
                        section=None,
                    )
                )
        elif rule.rule_type is AssuranceRuleType.conflicting_statement:
            if _has_conflicting_keyed_requirement(content):
                findings.append(
                    _finding(
                        run_id=run_id,
                        rule=rule,
                        artifact=artifact,
                        revision=revision,
                        section=None,
                    )
                )
    return findings


def review_finding(
    finding: AssuranceFinding, review: FindingReview
) -> AssuranceFinding:
    if review.finding_id != finding.finding_id:
        raise ValueError("finding review identity mismatch")
    if review.expected_revision != finding.revision:
        raise RuntimeError("stale_finding")
    status = {
        "confirm": AssuranceFindingStatus.confirmed,
        "dismiss": AssuranceFindingStatus.dismissed,
        "resolve": AssuranceFindingStatus.resolved,
        "reopen": AssuranceFindingStatus.open,
        "propose_governance": AssuranceFindingStatus.confirmed,
    }[review.action]
    return finding.model_copy(
        update={"status": status, "revision": finding.revision + 1, "updated_at": datetime.now(UTC)}
    )
