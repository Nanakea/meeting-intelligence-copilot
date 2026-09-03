"""Offline semantic-candidate annotation validation and baseline comparison.

This harness performs no model or network calls. It validates annotation-only
expectations against the Phase 4A candidate boundary, then measures exact
candidate coverage from the existing deterministic analyzer without reducing
the result into MeetingState.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.contracts import Lang, Speaker, TranscriptEvent
from app.domain.semantic_candidates import (
    CandidateKind,
    FactCandidate,
    PainCandidate,
    SemanticCandidateValidationError,
    SemanticObservationBatch,
    validate_semantic_observations,
)
from app.domain.templates import TEMPLATES
from app.services.analyzer import analyze


class _EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectedFactAnnotation(_EvalModel):
    template_id: str
    slot: str
    value: str
    kind: CandidateKind
    evidence_event_ids: list[str] = Field(min_length=1)


class SemanticCaseAnnotation(_EvalModel):
    id: str
    lang: Lang
    event_id: str
    text: str
    active_template_ids: list[str] = Field(default_factory=list)
    related_demo_id: str | None = None
    expected_pain_template: str | None
    expected_facts: list[ExpectedFactAnnotation] = Field(default_factory=list)


class SemanticAnnotationSuite(_EvalModel):
    name: str
    status: Literal["annotation-only"]
    purpose: str
    cases: list[SemanticCaseAnnotation]


class CoverageStatus(str, Enum):
    covered = "covered"
    partial = "partial"
    semantic_required = "semantic_required"
    negative_guard = "negative_guard"
    negative_guard_failed = "negative_guard_failed"


@dataclass(frozen=True)
class SemanticCaseEvalResult:
    case_id: str
    lang: str
    status: CoverageStatus
    expected: tuple[str, ...]
    deterministic: tuple[str, ...]
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]


@dataclass(frozen=True)
class SemanticEvalReport:
    suite_name: str
    results: tuple[SemanticCaseEvalResult, ...]

    @property
    def expected_candidates(self) -> int:
        return sum(len(result.expected) for result in self.results)

    @property
    def matched_candidates(self) -> int:
        return sum(len(result.matched) for result in self.results)


class SemanticEvalInputError(ValueError):
    """The annotation suite cannot be safely evaluated."""


def load_semantic_annotations(path: Path) -> SemanticAnnotationSuite:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SemanticAnnotationSuite.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise SemanticEvalInputError(f"invalid semantic annotation file {path}: {exc}") from exc


def evaluate_semantic_annotations(path: Path) -> SemanticEvalReport:
    suite = load_semantic_annotations(path)
    results = tuple(_evaluate_case(case, seq) for seq, case in enumerate(suite.cases))
    return SemanticEvalReport(suite_name=suite.name, results=results)


def _evaluate_case(case: SemanticCaseAnnotation, seq: int) -> SemanticCaseEvalResult:
    event = TranscriptEvent(
        event_id=case.event_id,
        meeting_id="semantic-eval",
        seq=seq,
        speaker=Speaker(id="annotation"),
        text=case.text,
        lang=case.lang,
        ts_start=float(seq),
        ts_end=float(seq + 1),
        source="semantic-annotation-eval",
    )
    batch = _expected_batch(case)
    try:
        validate_semantic_observations(batch, [event])
    except SemanticCandidateValidationError as exc:
        raise SemanticEvalInputError(f"invalid expectations for case {case.id!r}: {exc}") from exc

    deterministic_result = analyze(event)
    expected = set(_batch_labels(batch))
    deterministic = set(_analysis_labels(deterministic_result))
    matched = expected & deterministic
    missing = expected - deterministic
    unexpected = deterministic - expected

    if not expected:
        status = (
            CoverageStatus.negative_guard
            if not deterministic
            else CoverageStatus.negative_guard_failed
        )
    elif not missing:
        status = CoverageStatus.covered
    elif matched:
        status = CoverageStatus.partial
    else:
        status = CoverageStatus.semantic_required

    return SemanticCaseEvalResult(
        case_id=case.id,
        lang=case.lang.value,
        status=status,
        expected=tuple(sorted(expected)),
        deterministic=tuple(sorted(deterministic)),
        matched=tuple(sorted(matched)),
        missing=tuple(sorted(missing)),
        unexpected=tuple(sorted(unexpected)),
    )


def _expected_batch(case: SemanticCaseAnnotation) -> SemanticObservationBatch:
    pains: list[PainCandidate] = []
    if case.expected_pain_template is not None:
        category = case.expected_pain_template
        template = TEMPLATES.get(category)
        title = template.title_for(case.lang.value) if template is not None else category
        pains.append(
            PainCandidate(
                category=category,
                title=title,
                kind=CandidateKind.evidence,
                evidence_event_ids=[case.event_id],
                confidence=1.0,
                reason="Expected pain from annotation-only semantic eval.",
            )
        )
    facts = [
        FactCandidate(
            slot=fact.slot,
            value=fact.value,
            kind=fact.kind,
            evidence_event_ids=fact.evidence_event_ids,
            pain_category_hint=fact.template_id,
            confidence=1.0,
            reason="Expected fact from annotation-only semantic eval.",
        )
        for fact in case.expected_facts
    ]
    return SemanticObservationBatch(pains=pains, facts=facts)


def _batch_labels(batch: SemanticObservationBatch) -> list[str]:
    labels = [
        _pain_label(pain.category, pain.kind.value, pain.evidence_event_ids) for pain in batch.pains
    ]
    labels.extend(
        _fact_label(
            fact.pain_category_hint or "<missing-category>",
            fact.slot,
            fact.value,
            fact.kind.value,
            fact.evidence_event_ids,
        )
        for fact in batch.facts
    )
    return labels


def _analysis_labels(result) -> list[str]:
    labels: list[str] = []
    if result.detected_pain is not None:
        labels.append(
            _pain_label(
                result.detected_pain.template_id,
                CandidateKind.evidence.value,
                [result.detected_pain.evidence_event_id],
            )
        )
    labels.extend(
        _fact_label(
            fact.template_id,
            fact.slot,
            fact.value,
            fact.kind,
            [fact.evidence_event_id],
        )
        for fact in result.facts
    )
    return labels


def _pain_label(category: str, kind: str, evidence_ids: list[str]) -> str:
    evidence = ",".join(evidence_ids)
    return f"pain:{category}|kind={kind}|evidence={evidence}"


def _fact_label(
    category: str,
    slot: str,
    value: str,
    kind: str,
    evidence_ids: list[str],
) -> str:
    evidence = ",".join(evidence_ids)
    return f"fact:{category}.{slot}={value}|kind={kind}|evidence={evidence}"


def render_semantic_eval_markdown(report: SemanticEvalReport) -> str:
    statuses = {status: 0 for status in CoverageStatus}
    for result in report.results:
        statuses[result.status] += 1

    lines = [
        "# Semantic Candidate Eval Report",
        "",
        "Status: Phase 5 provider-neutral comparison. No model, network, cloud service, or",
        "MeetingState mutation participates in the automated evaluation.",
        "",
        "## Summary",
        "",
        f"- Annotation cases: {len(report.results)}",
        f"- Expected positive candidates: {report.expected_candidates}",
        f"- Exact deterministic candidate matches: {report.matched_candidates}",
        f"- Fully covered positive cases: {statuses[CoverageStatus.covered]}",
        f"- Partially covered positive cases: {statuses[CoverageStatus.partial]}",
        "- Positive cases requiring semantic extraction: "
        f"{statuses[CoverageStatus.semantic_required]}",
        f"- Negative guards passed: {statuses[CoverageStatus.negative_guard]}",
        f"- Negative guards failed: {statuses[CoverageStatus.negative_guard_failed]}",
        "",
        "Exact matching includes category, slot, normalized value, provenance kind, and",
        "evidence IDs.",
        "A deterministic value with a different provenance kind is intentionally not counted as an",
        "exact match.",
        "",
        "## Coverage matrix",
        "",
        "| Case | Lang | Classification | Exact candidates | Deterministic assessment |",
        "|---|---|---|---:|---|",
    ]
    for result in report.results:
        if result.status is CoverageStatus.covered:
            assessment = "All annotated candidates already covered"
        elif result.status is CoverageStatus.partial:
            assessment = "Some exact coverage; semantic extraction still needed"
        elif result.status is CoverageStatus.semantic_required:
            assessment = "No exact candidate coverage"
        elif result.status is CoverageStatus.negative_guard:
            assessment = "No candidate emitted (guard preserved)"
        else:
            assessment = "Unexpected deterministic candidate emitted"
        lines.append(
            f"| `{result.case_id}` | {result.lang} | `{result.status.value}` | "
            f"{len(result.matched)}/{len(result.expected)} | {assessment} |"
        )

    lines.extend(
        [
            "",
            "## Missing exact candidates",
            "",
        ]
    )
    for result in report.results:
        if result.missing:
            lines.append(f"### `{result.case_id}`")
            lines.append("")
            lines.extend(f"- `{candidate}`" for candidate in result.missing)
            lines.append("")

    lines.extend(
        [
            "## Decision coverage matrix",
            "",
            "| Bucket | Evidence in this suite | Decision implication |",
            "|---|---|---|",
            (
                "| Deterministic covered | JA data-mismatch pain; JA/EN integration "
                "pain/failure point; both negative guards | Preserve as baseline and fallback |"
            ),
            (
                "| Semantic expected to help | 13 missing exact candidates across JA/EN "
                "paraphrase, impact, ownership, and English integration cases | Evaluate "
                "locally before any composition |"
            ),
            (
                "| Unsafe or ambiguous | Source/target provenance, contextual owner "
                "attachment, and the Salesforce scope in `en-sem-04` | Require "
                "evidence/provenance review; never auto-promote |"
            ),
            (
                "| Negative guard | JA owner question and EN vague future ownership intent "
                "emit nothing | Zero false positives remains a promotion gate |"
            ),
            "",
            "## Failure taxonomy",
            "",
            "| Failure class | Current evidence | Required gate |",
            "|---|---|---|",
            (
                "| Paraphrase | Workday frequency and shipping-cutoff impact are missed | "
                "Normalized-value precision/recall |"
            ),
            (
                "| STT variation | Not isolated by this clean-text annotation suite | Add "
                "bounded real-STT variants before enablement |"
            ),
            (
                "| Slot ambiguity | Impact and failure wording may map to neighboring slots | "
                "Exact slot precision with evidence review |"
            ),
            (
                "| Scope ambiguity | `en-sem-04` mentions SAP and Salesforce in one flow | "
                "Do not infer target scope without explicit support |"
            ),
            (
                "| Ownership ambiguity | Split roles require active-template context and role "
                "preservation | Reject vague/future owners; preserve role text |"
            ),
            (
                "| Category confusion | EN spreadsheet handoff may indicate manual work "
                "without keyword anchors | Compare category precision against negative meetings |"
            ),
            "",
            "## Live provider status",
            "",
            "A local Ollama CLI was detected on the evaluation workstation, but no Ollama",
            "service was running and no model call was made. Live local-model quality",
            "therefore remains pending; mocked tests establish transport and fail-closed",
            "safety only.",
            "",
            "## Recommendation: do not enable yet",
            "",
            "Keep the semantic provider uncomposed and safe-off. The current evidence proves the",
            "candidate boundary, deterministic baseline, and adapter failure behavior, but it does",
            "not establish semantic precision. A future local-only eval should score exact",
            "candidates, provenance, evidence IDs, category confusion, and both negative",
            "guards. Only then consider a narrow opt-in for selected slots; broad runtime",
            "enablement is not justified.",
            "",
            "## Interpretation",
            "",
            "The deterministic analyzer preserves both negative guards but has limited",
            "paraphrase and English integration/ownership coverage. Partial matches",
            "demonstrate that the comparison measures the existing baseline rather than",
            "assuming every annotation needs a model. The annotation suite remains separate",
            "from deterministic state goldens; this report does not justify enabling a",
            "semantic provider.",
            "",
        ]
    )
    return "\n".join(lines)
