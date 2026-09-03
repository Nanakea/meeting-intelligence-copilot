from __future__ import annotations

from app.domain.contracts import Lang, Speaker, TranscriptEvent
from app.services.analyzer import analyze
from app.services.issue_drafts import build_issue_draft
from app.services.meeting_engine import MeetingEngine


def event(seq: int, text: str, *, lang: Lang = Lang.en) -> TranscriptEvent:
    return TranscriptEvent(
        event_id=f"event-{seq}",
        meeting_id="meeting-intel-0123456789abcdef0123456789abcdef",
        seq=seq,
        speaker=Speaker(id="speaker"),
        text=text,
        lang=lang,
        ts_start=float(seq),
        ts_end=float(seq + 1),
        source="test",
    )


def test_same_category_anchors_create_distinct_deterministic_instances() -> None:
    first = event(0, "SAP and EC inventory do not match.")
    second = event(1, "NetSuite and warehouse side inventory do not match.")
    replay_a = MeetingEngine(first.meeting_id)
    replay_b = MeetingEngine(first.meeting_id)
    for engine in (replay_a, replay_b):
        assert engine.apply(first)
        assert engine.apply(second)

    pains = replay_a.state.pain_points
    assert len(pains) == 2
    assert len({pain.instance_key for pain in pains}) == 2
    assert all("::data_mismatch::pi-" in pain.pain_id for pain in pains)
    assert [pain.pain_id for pain in pains] == [
        pain.pain_id for pain in replay_b.state.pain_points
    ]


def test_repeated_exact_anchor_merges_evidence_without_duplicate_pain() -> None:
    engine = MeetingEngine("meeting-intel-0123456789abcdef0123456789abcdef")
    assert engine.apply(event(0, "SAP and EC inventory do not match."))
    assert engine.apply(event(1, "SAP and EC inventory do not match."))

    assert len(engine.state.pain_points) == 1
    assert engine.state.pain_points[0].evidence_event_ids == ["event-0", "event-1"]


def test_provisional_instance_promotes_without_changing_public_pain_id() -> None:
    engine = MeetingEngine("meeting-intel-0123456789abcdef0123456789abcdef")
    assert engine.apply(event(0, "The API integration keeps failing."))
    provisional_id = engine.state.pain_points[0].pain_id

    assert engine.apply(
        event(1, "The order transfer from SAP into Salesforce fails.")
    )

    assert len(engine.state.pain_points) == 1
    promoted = engine.state.pain_points[0]
    assert promoted.pain_id == provisional_id
    assert promoted.identity_status == "anchored"
    assert promoted.identity_revision == 1
    assert promoted.identity_aliases
    assert promoted.subject == "SAP -> Salesforce order integration"
    assert all(fact.pain_id == promoted.pain_id for fact in engine.state.facts)


def test_provisional_instance_retains_facts_older_than_the_backfill_window() -> None:
    engine = MeetingEngine("meeting-intel-0123456789abcdef0123456789abcdef")
    statements = [
        "Legal approval currently takes 8 business days instead of 2.",
        "Which step is the bottleneck?",
        "The bottleneck is legal review, where requests wait for compliance.",
        "The compliance operations team owns the legal review.",
        "This delay postpones customer onboarding and contract activation.",
    ]
    for seq, statement in enumerate(statements):
        assert engine.apply(event(seq, statement))

    active = {
        fact.slot: fact.value
        for fact in engine.state.facts
        if fact.status.value == "active"
    }
    assert active["current_lead_time"] == "8 business days"
    assert active["normal_lead_time"] == "2 business days"


def test_ambiguous_unanchored_fact_is_not_attached_to_either_instance() -> None:
    engine = MeetingEngine("meeting-intel-0123456789abcdef0123456789abcdef")
    assert engine.apply(event(0, "SAP and EC inventory do not match."))
    assert engine.apply(event(1, "NetSuite and warehouse side inventory do not match."))
    assert engine.apply(event(2, "SAP is the source of truth for inventory."))

    assert not any(fact.slot == "source_of_truth" for fact in engine.state.facts)
    assert engine.state.ambiguous_routing_count >= 1
    assert engine.state.transcript[-1].event_id == "event-2"


def test_one_event_can_propose_multiple_problem_categories() -> None:
    result = analyze(
        event(
            0,
            "SAP and EC inventory do not match, so we manually copy the order file "
            "into a spreadsheet.",
        )
    )

    assert {pain.template_id for pain in result.detected_pains} >= {
        "data_mismatch",
        "manual_work",
    }


def test_process_delay_identity_includes_the_affected_object() -> None:
    engine = MeetingEngine("meeting-intel-0123456789abcdef0123456789abcdef")
    assert engine.apply(
        event(
            0,
            "The invoice approval process currently takes 8 business days instead of 2.",
        )
    )
    assert engine.apply(
        event(
            1,
            "The vendor approval process currently takes 6 business days instead of 1.",
        )
    )

    pains = [pain for pain in engine.state.pain_points if pain.template_id == "process_delay"]
    assert len(pains) == 2
    assert len({pain.instance_key for pain in pains}) == 2


def test_manual_action_paraphrases_share_the_same_instance() -> None:
    first = analyze(event(0, "We manually copy the order file into a spreadsheet."))
    second = analyze(event(1, "We copy the order file by hand into a spreadsheet."))

    first_pain = next(pain for pain in first.detected_pains if pain.template_id == "manual_work")
    second_pain = next(pain for pain in second.detected_pains if pain.template_id == "manual_work")
    assert first_pain.instance_key == second_pain.instance_key


def test_owner_fact_with_category_hint_does_not_cross_topics() -> None:
    engine = MeetingEngine("meeting-intel-0123456789abcdef0123456789abcdef")
    assert engine.apply(event(0, "SAP and EC inventory do not match."))
    assert engine.apply(event(1, "We manually copy the order file into a spreadsheet."))
    assert engine.apply(
        event(2, "CSV転記作業は山田さんが担当しています。", lang=Lang.ja)
    )

    owners = [fact for fact in engine.state.facts if fact.slot == "owner"]
    assert len(owners) == 1
    manual_pain = next(
        pain for pain in engine.state.pain_points if pain.template_id == "manual_work"
    )
    assert owners[0].pain_id == manual_pain.pain_id


def test_generic_owner_fact_is_withheld_across_multiple_topics() -> None:
    engine = MeetingEngine("meeting-intel-0123456789abcdef0123456789abcdef")
    assert engine.apply(event(0, "SAP and EC inventory do not match."))
    assert engine.apply(event(1, "We manually copy the order file into a spreadsheet."))
    assert engine.apply(event(2, "この件は佐藤さんが担当しています。", lang=Lang.ja))

    assert not any(fact.slot == "owner" for fact in engine.state.facts)
    assert engine.state.ambiguous_routing_count >= 1


def test_new_instance_backfill_does_not_copy_a_prior_instances_owner() -> None:
    engine = MeetingEngine("meeting-intel-0123456789abcdef0123456789abcdef")
    assert engine.apply(event(0, "SAP and EC inventory do not match."))
    first_pain_id = engine.state.pain_points[0].pain_id
    assert engine.apply(event(1, "Yamada is the final owner."))
    assert engine.apply(event(2, "NetSuite and warehouse side inventory do not match."))

    owners = [fact for fact in engine.state.facts if fact.slot == "owner"]
    assert len(owners) == 1
    assert owners[0].pain_id == first_pain_id


def test_contradiction_and_reopen_are_isolated_per_instance() -> None:
    engine = MeetingEngine("meeting-intel-0123456789abcdef0123456789abcdef")
    assert engine.apply(
        event(
            0,
            "SAP and EC inventory do not match. SAP is the source of truth for inventory.",
        )
    )
    assert engine.apply(
        event(
            1,
            "NetSuite and warehouse side inventory do not match. "
            "NetSuite is the source of truth for inventory.",
        )
    )
    first, second = engine.state.pain_points
    assert engine.apply(
        event(
            2,
            "SAP and EC inventory do not match. EC is the source of truth for inventory.",
        )
    )

    first_sources = [
        fact
        for fact in engine.state.facts
        if fact.pain_id == first.pain_id and fact.slot == "source_of_truth"
    ]
    second_sources = [
        fact
        for fact in engine.state.facts
        if fact.pain_id == second.pain_id and fact.slot == "source_of_truth"
    ]
    assert {fact.status.value for fact in first_sources} == {"contradicted"}
    assert all(fact.contradicts for fact in first_sources)
    assert len(second_sources) == 1
    assert second_sources[0].status.value == "active"

    first_gap = next(
        gap
        for gap in engine.state.gaps
        if gap.pain_id == first.pain_id and gap.slot == "source_of_truth"
    )
    second_gap = next(
        gap
        for gap in engine.state.gaps
        if gap.pain_id == second.pain_id and gap.slot == "source_of_truth"
    )
    assert first_gap.status.value == "open"
    assert first_gap.reopen_count == 1
    assert second_gap.status.value == "answered"
    assert second_gap.reopen_count == 0


def test_structured_drafts_are_isolated_by_problem_instance() -> None:
    engine = MeetingEngine("meeting-intel-0123456789abcdef0123456789abcdef")
    assert engine.apply(event(0, "SAP and EC inventory do not match."))
    assert engine.apply(event(1, "NetSuite and warehouse side inventory do not match."))

    first, second = engine.state.pain_points
    first_draft = build_issue_draft(engine.state, first.pain_id)
    second_draft = build_issue_draft(engine.state, second.pain_id)

    assert first_draft is not None and second_draft is not None
    assert first_draft.pain_id != second_draft.pain_id
    assert "SAP" in first_draft.title
    assert "NetSuite" in second_draft.title
    assert first_draft.problem == []
    assert second_draft.problem == []
    assert not set(first_draft.facts).intersection(second_draft.facts)
