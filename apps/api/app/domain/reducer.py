"""Deterministic MeetingState reduction (pure — no I/O, no vendors).

Applies an AnalysisResult for one event to the (pains, facts) of a meeting, then
recomputes gaps from template slots. Facts carry both axes: ``kind`` (provenance)
and ``status`` (lifecycle); this reducer never destroys or bypasses those fields.

Gap occupancy (DOMAIN_MODEL §4.2): a slot is answered iff it has >=1 active fact.
Reopening is therefore a pure consequence of fact status (a slot whose only fact
is superseded/contradicted becomes open again) — the analyzer/selector never
reopen a gap."""

from __future__ import annotations

from app.domain.analysis import AnalysisResult
from app.domain.contracts import (
    ExtractionOrigin,
    FactKind,
    FactStatus,
    GapStatus,
    InformationGap,
    MeetingFact,
    PainIdentityStatus,
    PainPoint,
    PainStatus,
    ProblemIdentityDecision,
)
from app.domain.templates import TEMPLATES


def apply_analysis(
    meeting_id: str,
    seq: int,
    event_id: str,
    pains: list[PainPoint],
    facts: list[MeetingFact],
    result: AnalysisResult,
    *,
    allow_identity_promotion: bool = True,
) -> tuple[list[PainPoint], list[MeetingFact]]:
    pains = list(pains)
    facts = list(facts)
    resolved_instances: dict[tuple[str, str], str] = {}
    ambiguous_instances = (
        ambiguous_identity_promotion_keys(pains, result, seq)
        if allow_identity_promotion
        else set()
    )

    # --- problem instances (one per template_id + opaque instance_key) ---
    for dp in result.detected_pains:
        existing = next(
            (
                p
                for p in pains
                if p.template_id == dp.template_id
                and p.merged_into_pain_id is None
                and (
                    p.instance_key == dp.instance_key
                    or dp.instance_key in p.identity_aliases
                )
            ),
            None,
        )
        if (
            allow_identity_promotion
            and existing is None
            and dp.identity_status == "anchored"
        ):
            promotable = [
                pain
                for pain in pains
                if pain.template_id == dp.template_id
                and pain.status is PainStatus.open
                and pain.identity_status.value == "provisional"
                and pain.merged_into_pain_id is None
                and 0 < seq - pain.created_seq <= 4
            ]
            anchored_conflict = any(
                pain.template_id == dp.template_id
                and pain.identity_status.value == "anchored"
                and pain.merged_into_pain_id is None
                for pain in pains
            )
            if len(promotable) == 1 and not anchored_conflict:
                provisional = promotable[0]
                promoted = provisional.model_copy(
                    update={
                        "instance_key": dp.instance_key,
                        "identity_aliases": _dedupe(
                            [*provisional.identity_aliases, provisional.instance_key]
                        ),
                        "identity_revision": provisional.identity_revision + 1,
                        "identity_status": PainIdentityStatus(dp.identity_status),
                        "subject": dp.subject or provisional.subject,
                        "evidence_event_ids": _dedupe(
                            [*provisional.evidence_event_ids, dp.evidence_event_id]
                        ),
                    }
                )
                pains = [promoted if pain is provisional else pain for pain in pains]
                existing = promoted
        if existing is None:
            created = PainPoint(
                pain_id=f"{meeting_id}::{dp.template_id}::{dp.instance_key}",
                meeting_id=meeting_id,
                template_id=dp.template_id,
                title=dp.title,
                instance_key=dp.instance_key,
                identity_status=dp.identity_status,
                subject=dp.subject,
                status=PainStatus.open,
                kind=FactKind.evidence,
                evidence_event_ids=[dp.evidence_event_id],
                created_seq=seq,
            )
            pains.append(created)
            resolved_instances[(dp.template_id, dp.instance_key)] = created.instance_key
        elif dp.evidence_event_id not in existing.evidence_event_ids:
            merged = existing.model_copy(
                update={
                    "evidence_event_ids": [
                        *existing.evidence_event_ids,
                        dp.evidence_event_id,
                    ],
                    "subject": existing.subject or dp.subject,
                }
            )
            pains = [merged if pain is existing else pain for pain in pains]
            resolved_instances[(dp.template_id, dp.instance_key)] = existing.instance_key
        else:
            resolved_instances[(dp.template_id, dp.instance_key)] = existing.instance_key

    unanchored_owner_templates = {
        fact.template_id
        for fact in result.facts
        if fact.slot == "owner" and fact.instance_key is None
    }
    owner_candidates = [
        pain
        for pain in pains
        if pain.status == PainStatus.open
        and pain.template_id in unanchored_owner_templates
    ]
    owner_target = owner_candidates[0] if len(owner_candidates) == 1 else None

    # --- facts ---
    for pf in result.facts:
        if (pf.template_id, pf.instance_key) in ambiguous_instances:
            continue
        if pf.slot == "owner" and pf.instance_key is None:
            if owner_target is None or owner_target.template_id != pf.template_id:
                continue
        candidates = [
            p
            for p in pains
            if p.template_id == pf.template_id
            and p.status == PainStatus.open
            and p.merged_into_pain_id is None
        ]
        resolved_key = (
            resolved_instances.get((pf.template_id, pf.instance_key), pf.instance_key)
            if pf.instance_key is not None
            else None
        )
        pain = (
            next(
                (
                    p
                    for p in candidates
                    if p.instance_key == resolved_key or resolved_key in p.identity_aliases
                ),
                None,
            )
            if resolved_key is not None
            else candidates[0] if len(candidates) == 1 else None
        )
        if pain is None:
            continue  # no unambiguous problem context; do not invent or duplicate facts
        lineage_ids = _pain_lineage_ids(pains, pain.pain_id)
        active = next(
            (
                f
                for f in facts
                if f.pain_id in lineage_ids
                and f.slot == pf.slot
                and f.status == FactStatus.active
            ),
            None,
        )
        if active is None:
            facts.append(_new_fact(meeting_id, pain.pain_id, seq, event_id, pf))
        elif active.value == pf.value:
            merged = active.model_copy(
                update={
                    "evidence_event_ids": _dedupe(active.evidence_event_ids + [event_id])
                }
            )
            facts = [merged if f is active else f for f in facts]
        elif pf.relation == "contradict" and not (
            active.kind is FactKind.inference and pf.kind == FactKind.evidence.value
        ):
            new = _new_fact(
                meeting_id,
                pain.pain_id,
                seq,
                event_id,
                pf,
                status=FactStatus.contradicted,
                contradicts=[active.fact_id],
            )
            contradicted = active.model_copy(
                update={
                    "status": FactStatus.contradicted,
                    "contradicts": _dedupe([*active.contradicts, new.fact_id]),
                }
            )
            facts = [contradicted if f is active else f for f in facts] + [new]
        else:
            # A newer explicit value for the same slot supersedes the previous one.
            new = _new_fact(meeting_id, pain.pain_id, seq, event_id, pf, supersedes=active.fact_id)
            superseded = active.model_copy(
                update={"status": FactStatus.superseded, "superseded_by": new.fact_id}
            )
            facts = [superseded if f is active else f for f in facts] + [new]

    return pains, facts


def count_ambiguous_identity_promotions(
    pains: list[PainPoint], result: AnalysisResult, seq: int
) -> int:
    """Count anchored candidates that cannot select one provisional instance."""

    return len(ambiguous_identity_promotion_keys(pains, result, seq))


def ambiguous_identity_promotion_keys(
    pains: list[PainPoint], result: AnalysisResult, seq: int
) -> set[tuple[str, str]]:
    """Return anchored instances whose promotion target is not deterministic."""

    ambiguous: set[tuple[str, str]] = set()
    for detected in result.detected_pains:
        if detected.identity_status != "anchored":
            continue
        if any(
            pain.template_id == detected.template_id
            and (
                pain.instance_key == detected.instance_key
                or detected.instance_key in pain.identity_aliases
            )
            for pain in pains
        ):
            continue
        promotable = [
            pain
            for pain in pains
            if pain.template_id == detected.template_id
            and pain.status is PainStatus.open
            and pain.identity_status is PainIdentityStatus.provisional
            and pain.merged_into_pain_id is None
            and 0 < seq - pain.created_seq <= 4
        ]
        if len(promotable) > 1:
            ambiguous.add((detected.template_id, detected.instance_key))
    return ambiguous


def count_ambiguous_fact_routes(
    pains: list[PainPoint], result: AnalysisResult
) -> int:
    """Count proposed facts deliberately withheld from multiple candidate pains."""

    ambiguous = sum(
        1
        for fact in result.facts
        if fact.instance_key is None and fact.slot != "owner"
        and sum(
            pain.template_id == fact.template_id and pain.status == PainStatus.open
            for pain in pains
        )
        > 1
    )
    owner_templates = {
        fact.template_id
        for fact in result.facts
        if fact.instance_key is None and fact.slot == "owner"
    }
    owner_candidates = {
        pain.pain_id
        for pain in pains
        if pain.status == PainStatus.open and pain.template_id in owner_templates
    }
    return ambiguous + int(len(owner_candidates) > 1)


def _new_fact(
    meeting_id: str,
    pain_id: str,
    seq: int,
    event_id: str,
    pf,
    supersedes: str | None = None,
    status: FactStatus = FactStatus.active,
    contradicts: list[str] | None = None,
) -> MeetingFact:
    return MeetingFact(
        fact_id=f"{pain_id}::{pf.slot}::{seq}",
        meeting_id=meeting_id,
        pain_id=pain_id,
        slot=pf.slot,
        value=pf.value,
        kind=FactKind(pf.kind),
        extraction_origin=ExtractionOrigin(pf.extraction_origin),
        status=status,
        evidence_event_ids=[event_id],
        supersedes=supersedes,
        contradicts=contradicts or [],
        created_seq=seq,
    )


def compute_gaps(
    meeting_id: str,
    pains: list[PainPoint],
    facts: list[MeetingFact],
    prior_gaps: list[InformationGap] | None = None,
) -> list[InformationGap]:
    prior_by_id = {gap.gap_id: gap for gap in (prior_gaps or [])}

    gaps: list[InformationGap] = []
    for pain in pains:
        if pain.status is not PainStatus.open or pain.merged_into_pain_id is not None:
            continue
        template = TEMPLATES.get(pain.template_id)
        if template is None:
            continue
        for slot in template.slots:
            lineage_ids = _pain_lineage_ids(pains, pain.pain_id)
            active = next(
                (
                    f
                    for f in facts
                    if f.pain_id in lineage_ids
                    and f.slot == slot
                    and f.status == FactStatus.active
                ),
                None,
            )
            gap_id = f"{pain.pain_id}::{slot}"
            prior = prior_by_id.get(gap_id)
            reopen_count = prior.reopen_count if prior is not None else 0
            if (
                active is None
                and prior is not None
                and prior.status is GapStatus.answered
            ):
                reopen_count += 1
            gaps.append(
                InformationGap(
                    gap_id=gap_id,
                    meeting_id=meeting_id,
                    pain_id=pain.pain_id,
                    slot=slot,
                    status=GapStatus.answered if active else GapStatus.open,
                    resolved_by_fact_id=active.fact_id if active else None,
                    base_priority=template.priority_of(slot),
                    reopen_count=reopen_count,
                )
            )
    return gaps


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _pain_lineage_ids(pains: list[PainPoint], pain_id: str) -> set[str]:
    return {
        pain.pain_id
        for pain in pains
        if pain.pain_id == pain_id or pain.merged_into_pain_id == pain_id
    }


def apply_identity_decision(
    pains: list[PainPoint],
    decisions: list[ProblemIdentityDecision],
    decision: ProblemIdentityDecision,
) -> tuple[list[PainPoint], list[ProblemIdentityDecision]]:
    """Apply a reviewed same-category identity decision without deleting history."""

    if decision.decision_id in {item.decision_id for item in decisions}:
        return list(pains), list(decisions)
    selected = [pain for pain in pains if pain.pain_id in decision.pain_ids]
    if len(selected) != 2 or selected[0].template_id != selected[1].template_id:
        raise ValueError("identity decisions require two same-category problems")
    if any(pain.merged_into_pain_id is not None for pain in selected):
        raise ValueError("merged problems cannot be decided again")
    if decision.action == "keep_separate":
        return list(pains), [*decisions, decision]
    if decision.action != "merge":
        raise ValueError("unsupported identity decision")

    survivor = min(selected, key=lambda pain: (pain.created_seq, pain.pain_id))
    if decision.survivor_pain_id is not None:
        survivor = next(
            (pain for pain in selected if pain.pain_id == decision.survivor_pain_id),
            None,
        )
        if survivor is None:
            raise ValueError("identity decision survivor is not selected")
    merged = next(pain for pain in selected if pain is not survivor)
    promoted = survivor.model_copy(
        update={
            "identity_aliases": _dedupe(
                [
                    *survivor.identity_aliases,
                    merged.instance_key,
                    *merged.identity_aliases,
                ]
            ),
            "identity_revision": survivor.identity_revision + 1,
            "evidence_event_ids": _dedupe(
                [*survivor.evidence_event_ids, *merged.evidence_event_ids]
            ),
            "subject": survivor.subject or merged.subject,
        }
    )
    retired = merged.model_copy(
        update={
            "status": PainStatus.mitigated,
            "merged_into_pain_id": promoted.pain_id,
            "identity_revision": merged.identity_revision + 1,
        }
    )
    updated = [
        promoted if pain is survivor else retired if pain is merged else pain
        for pain in pains
    ]
    normalized_decision = decision.model_copy(
        update={"survivor_pain_id": promoted.pain_id}
    )
    return updated, [*decisions, normalized_decision]
