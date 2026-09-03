"""Slice 2 verification: data-driven templates, JA/EN analysis, generic pipeline,
demo registry, and multi-fixture WebSocket streaming."""

from __future__ import annotations

import asyncio
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.adapters.transcript.replay import ReplayTranscriptSource
from app.api.demo_registry import demo_ids, resolve_demo
from app.api.main import create_app
from app.domain.contracts import (
    GapStatus,
    InformationGap,
    Lang,
    MeetingState,
    PainPoint,
    Speaker,
    TranscriptEvent,
)
from app.domain.selector import select_suggestions
from app.domain.templates import SUPPORTED_CATEGORIES, TEMPLATES
from app.services.analyzer import analyze
from app.services.meeting_engine import MeetingEngine

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "evals" / "fixtures"

_EXPECTED_CATEGORIES = {
    "data_mismatch",
    "manual_work",
    "integration_failure",
    "vague_requirement",
    "process_delay",
    "unclear_ownership",
    "nonfunctional_requirement",
    "architecture_constraint",
    "security_control_gap",
    "dependency_blocker",
}


# --- helpers ---------------------------------------------------------------


def _replay(demo_id: str) -> dict[int, MeetingState]:
    demo = resolve_demo(demo_id)
    assert demo is not None

    async def collect() -> dict[int, MeetingState]:
        engine = MeetingEngine(demo_id)
        out: dict[int, MeetingState] = {}
        src = ReplayTranscriptSource(demo.fixture_path, demo_id, lang=demo.lang, speed_factor=0.0)
        async for ev in src.events():
            engine.apply(ev)
            out[ev.seq] = engine.state
        return out

    return asyncio.run(collect())


def _facts(state: MeetingState) -> dict[str, str]:
    return {f.slot: f.value for f in state.facts if f.status == "active"}


def _ask_now(state: MeetingState) -> str | None:
    slot_of = {g.gap_id: g.slot for g in state.gaps}
    for s in state.suggestions:
        if s.role == "ask_now" and s.status == "active":
            return slot_of.get(s.gap_id)
    return None


def _active_sugg_slots(state: MeetingState) -> list[str]:
    slot_of = {g.gap_id: g.slot for g in state.gaps}
    return [slot_of[s.gap_id] for s in state.suggestions if s.status == "active"]


def _ev(text: str, lang: Lang, seq: int = 0) -> TranscriptEvent:
    return TranscriptEvent(
        event_id=f"e{seq}",
        meeting_id="t",
        seq=seq,
        speaker=Speaker(id="sp"),
        text=text,
        lang=lang,
        ts_start=0.0,
        ts_end=0.0,
        source="test",
    )


# Fixtures whose primary ASK NOW target is answered during the meeting. The exact
# promoted slot per checkpoint is pinned in the golden files; here we assert the
# stale target retracts and ASK NOW moves to a different (promoted) gap.
_ANSWERING = {
    "inventory-mismatch-ja": "source_of_truth",
    "data-mismatch-en": "source_of_truth",
    "vague-requirement-ja": "expected_behavior",
    "vague-requirement-en": "expected_behavior",
    "vendor-integration-failure-ja": "business_impact",
    "manual-work-en": "business_impact",
    "manual-work-ja": "business_impact",
    "manual-work-volume-en": "business_impact",
    "owner-answer-ja": "owner",
}


# --- template registry -----------------------------------------------------


def test_registry_contains_all_solution_categories() -> None:
    assert set(SUPPORTED_CATEGORIES) == _EXPECTED_CATEGORIES
    assert set(TEMPLATES) == _EXPECTED_CATEGORIES


def test_each_template_has_ordered_unique_slots() -> None:
    for cat, tpl in TEMPLATES.items():
        assert isinstance(tpl.slots, tuple) and tpl.slots, f"{cat} has no slots"
        assert len(tpl.slots) == len(set(tpl.slots)), f"{cat} has duplicate slots"


def test_documented_priority_orders() -> None:
    assert TEMPLATES["data_mismatch"].slots[:3] == ("source_of_truth", "business_impact", "owner")
    assert TEMPLATES["manual_work"].slots[:2] == ("business_impact", "frequency")
    assert TEMPLATES["integration_failure"].slots[:2] == ("business_impact", "failure_point")


def test_ja_question_wording_exists_for_ja_slots() -> None:
    for cat in ("data_mismatch", "vague_requirement", "integration_failure"):
        for slot in TEMPLATES[cat].slots:
            assert TEMPLATES[cat].question("ja", slot) is not None, f"{cat}.{slot} missing JA"


def test_en_question_wording_exists_for_en_slots() -> None:
    for slot in TEMPLATES["manual_work"].slots:
        assert TEMPLATES["manual_work"].question("en", slot) is not None, f"manual_work.{slot} EN"


# --- generic selector ------------------------------------------------------


@pytest.mark.parametrize("cat", ["data_mismatch", "integration_failure", "vague_requirement"])
def test_selector_generic_across_categories(cat: str) -> None:
    tpl = TEMPLATES[cat]
    pain = PainPoint(
        pain_id=f"m::{cat}",
        meeting_id="m",
        template_id=cat,
        title=tpl.title_for("ja"),
        kind="evidence",
        evidence_event_ids=["e0"],
        created_seq=0,
    )
    gaps = [
        InformationGap(
            gap_id=f"m::{cat}::{slot}",
            meeting_id="m",
            pain_id=pain.pain_id,
            slot=slot,
            status=GapStatus.open,
            base_priority=i,
        )
        for i, slot in enumerate(tpl.slots)
    ]
    sugg = select_suggestions([pain], gaps, lang="ja")
    ask = [s for s in sugg if s.role == "ask_now"]
    assert len(ask) == 1
    assert ask[0].gap_id.endswith(f"::{tpl.slots[0]}")  # top priority slot
    assert len([s for s in sugg if s.role == "follow_up"]) <= 2


# --- language dispatch -----------------------------------------------------


def test_analyzer_dispatches_by_explicit_language() -> None:
    # JA data_mismatch text under EN lang must NOT trigger the JA rule.
    assert analyze(_ev("倉庫側とEC側で在庫が合わない", Lang.en)).detected_pain is None
    # EN manual text under JA lang must NOT trigger the EN rule.
    assert analyze(_ev("copies the rows into another spreadsheet", Lang.ja)).detected_pain is None
    # Correct dispatch works.
    detected = analyze(_ev("倉庫側とEC側で在庫が合わない", Lang.ja)).detected_pain
    assert detected is not None and detected.template_id == "data_mismatch"


@pytest.mark.parametrize(
    "text",
    [
        "The inventory report does not match the ERP report.",
        "Our stock count does not match SAP.",
    ],
)
def test_en_data_mismatch_accepts_uncontracted_third_person_negation(text: str) -> None:
    detected = analyze(_ev(text, Lang.en)).detected_pain

    assert detected is not None
    assert detected.template_id == "data_mismatch"


def test_en_matching_systems_do_not_fabricate_data_mismatch() -> None:
    assert (
        analyze(_ev("The inventory report does match the ERP report.", Lang.en)).detected_pain
        is None
    )


def test_unclear_ownership_impact_does_not_open_process_delay() -> None:
    result = analyze(
        _ev("Until that is resolved, discount approvals stall and customers wait.", Lang.en)
    )
    assert result.detected_pain is None
    assert any(
        fact.template_id == "unclear_ownership" and fact.slot == "business_impact"
        for fact in result.facts
    )


@pytest.mark.parametrize(
    "text",
    [
        "値引き承認は誰が責任を持つか決まっていないため、担当が曖昧です。",
        "この申請には明確な担当者がいないので、どの部署が持つか決まっていません。",
        "値引き承認は誰が担当するか決まっていません。",
    ],
)
def test_unclear_ownership_ja_detects_generalized_owner_ambiguity(text: str) -> None:
    result = analyze(_ev(text, Lang.ja))
    assert result.detected_pain is not None
    assert result.detected_pain.template_id == "unclear_ownership"


@pytest.mark.parametrize(
    "text",
    [
        "Nobody knows who owns pricing exceptions.",
        "Pricing exceptions have no clear owner.",
        "The approval process has no clear owner.",
    ],
)
def test_unclear_ownership_en_detects_ownership_grammar(text: str) -> None:
    result = analyze(_ev(text, Lang.en))
    assert result.detected_pain is not None
    assert result.detected_pain.template_id == "unclear_ownership"


@pytest.mark.parametrize(
    "text",
    [
        "この申請の担当は営業部です。",
        "誰が担当するか決まっていますか？",
        "もし誰が担当するか決まっていない場合は、管理部に相談します。",
        "「明確な担当者がいない」と顧客が言っていました。",
    ],
)
def test_unclear_ownership_ja_guards_non_pain_contexts(text: str) -> None:
    assert analyze(_ev(text, Lang.ja)).detected_pain is None


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("Do the warehouse and EC inventory counts not match?", Lang.en),
        ("Does the EC to SAP integration fail?", Lang.en),
        ("Does the EC to SAP integration fail", Lang.en),
        ("Do analysts manually copy rows into a spreadsheet", Lang.en),
        (
            "How often do analysts manually copy rows into a spreadsheet every morning",
            Lang.en,
        ),
        ("We do not copy rows manually into a spreadsheet.", Lang.en),
        ("The integration no longer fails.", Lang.en),
        ("Ownership is now clear.", Lang.en),
        ("倉庫側とEC側で在庫が合わないのですか？", Lang.ja),
        ("この作業は手作業ではありません。", Lang.ja),
        ("承認プロセスに遅延はありません。", Lang.ja),
        ("担当は明確です。", Lang.ja),
    ],
)
def test_pain_detection_guards_questions_and_resolved_negations(text: str, lang: Lang) -> None:
    result = analyze(_ev(text, lang))
    assert result.detected_pain is None
    assert result.facts == []


def test_en_temporal_when_clause_is_not_misclassified_as_a_question() -> None:
    result = analyze(_ev("When inventory drifts, shipping gets delayed.", Lang.en))
    assert {(fact.template_id, fact.slot, fact.value) for fact in result.facts} == {
        ("data_mismatch", "business_impact", "shipping delay")
    }


def test_resolved_marker_for_one_topic_does_not_hide_another_topic() -> None:
    result = analyze(
        _ev(
            "Warehouse and EC inventory counts now match, but the approval process "
            "currently takes 8 business days instead of 2.",
            Lang.en,
        )
    )

    assert result.detected_pain is not None
    assert result.detected_pain.template_id == "process_delay"
    assert {(fact.slot, fact.value) for fact in result.facts} >= {
        ("current_lead_time", "8 business days"),
        ("normal_lead_time", "2 business days"),
    }


def test_ja_resolved_marker_for_one_topic_does_not_hide_another_topic() -> None:
    result = analyze(
        _ev(
            "倉庫側とEC側の在庫は今は合っていますが、承認プロセスは"
            "現在8営業日かかり、本来2営業日です。",
            Lang.ja,
        )
    )

    assert result.detected_pain is not None
    assert result.detected_pain.template_id == "process_delay"


def test_en_integration_failure_requires_a_failure_shaped_clause() -> None:
    result = analyze(_ev("The SAP integration keeps failing during invoice posting.", Lang.en))
    assert result.detected_pain is not None
    assert result.detected_pain.template_id == "integration_failure"


@pytest.mark.parametrize(
    "text",
    [
        "EC SAP integration error",
        "The EC and SAP integration architecture review is tomorrow.",
        "SAP, Salesforce, and AWS are the systems mentioned in the roadmap.",
    ],
)
def test_en_integration_fragments_and_system_mentions_do_not_create_pain(text: str) -> None:
    assert analyze(_ev(text, Lang.en)).detected_pain is None


def test_unclear_ownership_ja_extracts_governance_facts() -> None:
    result = analyze(
        _ev(
            "判断は毎週の価格会議で決めます。営業、法務、財務が関係者です。"
            "未解決の場合はCOOにエスカレーションします。",
            Lang.ja,
        )
    )
    slots = {fact.slot: fact.value for fact in result.facts}
    assert slots["decision_process"] == "価格会議"
    assert slots["stakeholders"] == "営業、法務、財務"
    assert slots["escalation_path"] == "COO"


def test_new_realistic_demo_ids_are_registered() -> None:
    assert {
        "data-mismatch-en",
        "manual-work-ja",
        "manual-work-volume-en",
        "integration-transfer-details-en",
        "process-delay-en",
        "process-delay-ja",
        "unclear-ownership-en",
        "unclear-ownership-ja",
        "vague-requirement-en",
    } <= set(demo_ids())


# --- detection + extraction per category -----------------------------------


def test_vague_requirement_detection_and_extraction() -> None:
    snaps = _replay("vague-requirement-ja")
    assert snaps[0].pain_points[0].template_id == "vague_requirement"
    assert _facts(snaps[2]).get("current_problem") == "検索結果が多く時間がかかる"
    assert _facts(snaps[4]).get("expected_behavior") == "検索結果を絞り込みすぐ見つけられる状態"


def test_vague_requirement_en_detection_and_extraction() -> None:
    snaps = _replay("vague-requirement-en")
    assert snaps[0].pain_points[0].template_id == "vague_requirement"
    assert (
        _facts(snaps[2]).get("current_problem")
        == "too many results make the right item hard to find"
    )
    assert (
        _facts(snaps[4]).get("expected_behavior")
        == "filter results and find the right item right away"
    )
    assert (
        _facts(snaps[5]).get("acceptance_criteria")
        == "new reps can find the item in under 10 seconds"
    )
    assert _facts(snaps[6]).get("business_impact") == "slows quote preparation"


def test_integration_failure_detection_and_extraction() -> None:
    snaps = _replay("vendor-integration-failure-ja")
    assert snaps[0].pain_points[0].template_id == "integration_failure"
    f3 = _facts(snaps[3])
    assert f3.get("error_signal") == "エラーなし"
    assert f3.get("retry_behavior") == "手動で登録"
    assert _facts(snaps[5]).get("business_impact") == "出荷遅延・配送影響"
    # source_system / target_system must NOT be inferred.
    assert "source_system" not in _facts(snaps[5])
    assert "target_system" not in _facts(snaps[5])


@pytest.mark.parametrize(
    ("text", "source", "target", "failure_point"),
    [
        ("EC側のCSVをSAPに取り込むところで止まります。", "EC", "SAP", "CSV取り込み"),
        (
            "受注システムの注文データを倉庫システムに送信する途中で失敗しています。",
            "受注システム",
            "倉庫システム",
            "注文データ送信",
        ),
    ],
)
def test_ja_directional_integration_failure_extracts_explicit_transfer_details(
    text: str,
    source: str,
    target: str,
    failure_point: str,
) -> None:
    result = analyze(_ev(text, Lang.ja))
    facts = {fact.slot: fact for fact in result.facts}

    assert result.detected_pain is not None
    assert result.detected_pain.template_id == "integration_failure"
    assert facts["source_system"].value == source
    assert facts["target_system"].value == target
    assert facts["failure_point"].value == failure_point
    assert all(fact.evidence_event_id == "e0" for fact in facts.values())


@pytest.mark.parametrize(
    "text",
    [
        "ECとSAPの連携について確認します。",
        "SAPとSalesforceの連携でも同様の問題が起きています。",
        "EC側のCSVをSAPに取り込んでいます。",
        "CSVの取り込みで止まります。",
    ],
)
def test_ja_integration_mentions_without_directional_failure_do_not_fabricate_details(
    text: str,
) -> None:
    detail_slots = {"source_system", "target_system", "failure_point"}
    facts = analyze(_ev(text, Lang.ja)).facts
    assert not any(fact.slot in detail_slots for fact in facts)


def test_ja_directional_integration_question_does_not_self_answer_details() -> None:
    result = analyze(_ev("EC側のCSVをSAPに取り込むところで止まりますか？", Lang.ja))
    detail_slots = {"source_system", "target_system", "failure_point"}
    assert not any(fact.slot in detail_slots for fact in result.facts)


# --- real-STT-driven JA data_mismatch robustness ----------------------------
# Regression coverage for generalized fixes made after a real Meetily +
# Whisper(small) + VOICEVOX Japanese ingress run exposed same-reading kanji
# ("homophone") substitutions. Each case pairs the EXACT observed STT wording
# with a paraphrase to confirm the fix is a semantic rule class, not a
# fixture-specific keyword match.


def test_ja_pain_detected_despite_zaiko_homophone_substitution() -> None:
    # Real Whisper(small) output: "在庫" (zaiko/inventory) was mis-heard as the
    # same-reading "最高" (saikou/best). Expected semantic result: the mismatch
    # pain point is still detected from the surviving two-sided + "合わない" structure.
    real_stt = "EC側と倉庫側で最高の数が合わない状態がほぼ毎朝発生しています"
    detected = analyze(_ev(real_stt, Lang.ja)).detected_pain
    assert detected is not None and detected.template_id == "data_mismatch"

    # Paraphrase: a different two-system mismatch statement, unrelated wording.
    paraphrase = "本社側と支店側で数字が合わない状態です"
    detected2 = analyze(_ev(paraphrase, Lang.ja)).detected_pain
    assert detected2 is not None and detected2.template_id == "data_mismatch"


def test_ja_two_sided_mention_alone_does_not_fabricate_pain() -> None:
    # Guard against over-broadening: two "側" mentions without a mismatch verb
    # must NOT trigger the data_mismatch pain point.
    text = "EC側と倉庫側でミーティングをしました"
    assert analyze(_ev(text, Lang.ja)).detected_pain is None


def test_ja_source_of_truth_survives_sei_homophone_substitution() -> None:
    # Real Whisper(small) output: "正" (sei/authoritative) was mis-heard as the
    # same-reading "制". Expected semantic result: source_of_truth=倉庫側 is still
    # extracted from the "...を(正|制)としています" declaration construction.
    real_stt = "倉庫システム側を制としています"
    facts = analyze(_ev(real_stt, Lang.ja)).facts
    assert any(f.slot == "source_of_truth" and f.value == "倉庫側" for f in facts)

    # Paraphrase: same construction, EC side, correct spelling (regression for the
    # original intended word, and the EC branch of the same rule).
    paraphrase = "ECシステム側を正としています"
    facts2 = analyze(_ev(paraphrase, Lang.ja)).facts
    assert any(f.slot == "source_of_truth" and f.value == "EC側" for f in facts2)


def test_ja_business_impact_extraction_from_real_stt_wording() -> None:
    # Real Whisper(small) output (note "大体", not the source script's "だいたい" --
    # a kanji/kana variant of the same word; the fix anchors on "遅れ" instead).
    real_stt = "この確認作業のせいで、朝の出荷作業が大体30分ほど遅れています。"
    facts = analyze(_ev(real_stt, Lang.ja)).facts
    assert any(f.slot == "business_impact" for f in facts)

    # Paraphrase: different wording, same "impact" semantic (影響 + 出て pattern).
    paraphrase = "その影響で出荷作業に遅れが出ています。"
    facts2 = analyze(_ev(paraphrase, Lang.ja)).facts
    assert any(f.slot == "business_impact" for f in facts2)


def test_ja_generic_time_consuming_statement_does_not_fabricate_business_impact() -> None:
    # Guard against over-broadening (independent review finding): a generic
    # "this takes time" statement with no delay/shipping-impact anchor must NOT
    # fabricate the specific "出荷作業の遅延" value. Only "遅れ" (or "影響"+"出て")
    # are real evidence-backed anchors; "時間がかかる" alone was never observed.
    text = "この確認作業は時間がかかります。"
    facts = analyze(_ev(text, Lang.ja)).facts
    assert not any(f.slot == "business_impact" for f in facts)


# --- generalized Japanese owner extraction -------------------------------


def _owner_facts(text: str) -> dict[str, str]:
    return {
        fact.template_id: fact.value
        for fact in analyze(_ev(text, Lang.ja)).facts
        if fact.slot == "owner"
    }


@pytest.mark.parametrize(
    ("text", "category", "expected"),
    [
        ("対応は田中さんが担当しています", "data_mismatch", "田中さん"),
        ("在庫差異の確認は倉庫チームが見ています", "data_mismatch", "倉庫チーム"),
        ("API連携の一次対応はEC運用チームで対応しています", "integration_failure", "EC運用チーム"),
        ("CSVの転記作業は山田さんが担当しています", "manual_work", "山田さん"),
    ],
)
def test_ja_owner_extraction_supports_people_and_teams(
    text: str,
    category: str,
    expected: str,
) -> None:
    assert _owner_facts(text)[category] == expected


def test_ja_owner_extraction_supports_split_role_ownership() -> None:
    owners = _owner_facts("一次対応は山田さん、最終確認は情シスです")
    assert owners["data_mismatch"] == "一次対応: 山田さん / 最終確認: 情シス"
    expected_categories = {
        category for category, template in TEMPLATES.items() if "owner" in template.slots
    }
    assert set(owners) == expected_categories


@pytest.mark.parametrize(
    "text",
    [
        "この差異の対応は、どなたが担当されていますか？",
        "担当は田中さんでしょうか？",
        "倉庫チームに確認します",
        "担当者はまだ決まっていません",
        "担当は明確です",
        "担当は曖昧です",
    ],
)
def test_ja_owner_extraction_rejects_questions_and_non_ownership_mentions(text: str) -> None:
    assert not _owner_facts(text)


def test_ja_owner_answer_retracts_owner_question_and_preserves_evidence() -> None:
    engine = MeetingEngine("owner-answer")
    events = [
        _ev("倉庫側とEC側で在庫が合わないことがあります", Lang.ja, 0),
        _ev("現在は倉庫側の在庫を正として扱っています", Lang.ja, 1),
        _ev("その影響で出荷作業に遅れが出ています", Lang.ja, 2),
        _ev("この差異の対応は、どなたが担当されていますか？", Lang.ja, 3),
        _ev("対応は田中さんが担当しています", Lang.ja, 4),
    ]
    for event in events:
        assert engine.apply(event)
        if event.seq == 2:
            assert _ask_now(engine.state) == "owner"
        if event.seq == 3:
            assert "owner" not in _facts(engine.state)
            assert _ask_now(engine.state) == "owner"

    owner_facts = [
        fact for fact in engine.state.facts if fact.slot == "owner" and fact.status == "active"
    ]
    assert len(owner_facts) == 1
    assert owner_facts[0].value == "田中さん"
    assert owner_facts[0].evidence_event_ids == ["e4"]
    assert _ask_now(engine.state) == "affected_scope"
    assert "owner" not in _active_sugg_slots(engine.state)


def test_ja_owner_answer_can_fill_existing_manual_work_pain() -> None:
    engine = MeetingEngine("manual-work-ja-owner")
    assert engine.apply(
        _ev("Every morning one person copies rows into a spreadsheet manually", Lang.en, 0)
    )
    assert engine.apply(_ev("CSVの転記作業は山田さんが担当しています", Lang.ja, 1))
    active_owner = [
        fact for fact in engine.state.facts if fact.slot == "owner" and fact.status == "active"
    ]
    assert len(active_owner) == 1
    assert active_owner[0].value == "山田さん"
    assert active_owner[0].evidence_event_ids == ["e1"]


def test_data_mismatch_en_detection_and_extraction() -> None:
    snaps = _replay("data-mismatch-en")
    assert snaps[0].pain_points[0].template_id == "data_mismatch"
    assert _facts(snaps[1]).get("correction_process") == "manual correction"
    assert _facts(snaps[1]).get("frequency") == "every morning"
    assert _facts(snaps[2]).get("source_of_truth") is None
    assert _facts(snaps[3]).get("source_of_truth") == "warehouse side"
    assert _facts(snaps[4]).get("business_impact") == "shipping delay"


def test_manual_work_en_detection_and_extraction() -> None:
    snaps = _replay("manual-work-en")
    assert snaps[0].pain_points[0].template_id == "manual_work"
    # frequency is explicit at event 0 ("Every morning ..."), refined later.
    assert _facts(snaps[0]).get("frequency") == "every morning"
    assert _facts(snaps[2]).get("business_impact") == "delays morning shipping report by ~1h"
    assert _facts(snaps[3]).get("frequency") == "every business day"
    # volume / time_cost must NOT be inferred ("one person" != volume=1).
    assert "volume" not in _facts(snaps[3])
    assert "time_cost" not in _facts(snaps[3])
    assert "volume" not in _facts(snaps[0])


def test_new_pain_backfills_adjacent_split_frequency_with_original_evidence() -> None:
    engine = MeetingEngine("split-manual-work")
    frequency = _ev("Every business day.", Lang.en, 0).model_copy(
        update={"ts_start": 10.0, "ts_end": 11.0}
    )
    pain = _ev(
        "Order operations copies the order file into a spreadsheet by hand.",
        Lang.en,
        1,
    ).model_copy(update={"ts_start": 12.0, "ts_end": 14.0})

    assert engine.apply(frequency)
    assert engine.state.pain_points == []
    engine = MeetingEngine.from_state(engine.state)
    assert engine.apply(pain)

    active_frequency = [
        fact for fact in engine.state.facts if fact.slot == "frequency" and fact.status == "active"
    ]
    assert len(active_frequency) == 1
    assert active_frequency[0].value == "every business day"
    assert active_frequency[0].created_seq == 0
    assert active_frequency[0].evidence_event_ids == ["e0"]
    assert "::manual_work::pi-" in active_frequency[0].pain_id
    assert _ask_now(engine.state) == "business_impact"


def test_new_pain_does_not_backfill_expired_context() -> None:
    engine = MeetingEngine("expired-manual-work")
    frequency = _ev("Every business day.", Lang.en, 0).model_copy(
        update={"ts_start": 0.0, "ts_end": 1.0}
    )
    pain = _ev(
        "Order operations copies the order file into a spreadsheet by hand.",
        Lang.en,
        1,
    ).model_copy(update={"ts_start": 32.0, "ts_end": 34.0})

    assert engine.apply(frequency)
    assert engine.apply(pain)

    assert "frequency" not in _facts(engine.state)
    assert "frequency" in {gap.slot for gap in engine.state.gaps if gap.status == "open"}


def test_manual_work_ja_detection_and_extraction() -> None:
    snaps = _replay("manual-work-ja")
    assert snaps[0].pain_points[0].template_id == "manual_work"
    assert _facts(snaps[0]).get("frequency") == "毎朝"
    assert _facts(snaps[1]).get("business_impact") == "出荷レポートが遅れる"
    assert _facts(snaps[2]).get("volume") == "200件"
    assert _facts(snaps[3]).get("time_cost") == "30分"
    assert _facts(snaps[4]).get("owner") == "山田さん"
    assert _facts(snaps[5]).get("reason_manual") == "連携未対応"


def _freq_facts(state: MeetingState):
    return [f for f in state.facts if f.slot == "frequency"]


def test_manual_work_frequency_refinement() -> None:
    snaps = _replay("manual-work-en")
    first_event = "manual-work-en-0000"

    # Event 0: "Every morning ..." → explicit frequency = every morning, evidence-cited.
    f0 = _freq_facts(snaps[0])
    assert len(f0) == 1 and f0[0].value == "every morning" and f0[0].status == "active"
    assert f0[0].evidence_event_ids == [first_event]

    # PM "How often is that process required?" (seq1) must not create/mutate the fact.
    assert _facts(snaps[1]).get("frequency") == "every morning"

    # After business_impact resolves (seq2), volume is promoted (frequency already answered).
    assert _ask_now(snaps[2]) == "volume"

    # "Every business day." (seq3) supersedes; both facts retained with evidence.
    final = snaps[3]
    active = [f for f in _freq_facts(final) if f.status == "active"]
    superseded = [f for f in _freq_facts(final) if f.status == "superseded"]
    assert len(active) == 1 and active[0].value == "every business day"
    assert active[0].evidence_event_ids == ["manual-work-en-0003"]
    assert len(superseded) == 1 and superseded[0].value == "every morning"
    assert superseded[0].evidence_event_ids == [first_event]  # not deleted, evidence kept
    assert all(f.kind == "evidence" for f in _freq_facts(final))  # no inference

    # Frequency gap answered throughout; frequency never suggested; volume undisturbed.
    for s in snaps.values():
        assert "frequency" not in {g.slot for g in s.gaps if g.status == "open"}
        assert "frequency" not in _active_sugg_slots(s)
    assert _ask_now(final) == "volume"


def test_process_delay_ja_detection_and_extraction() -> None:
    snaps = _replay("process-delay-ja")
    assert snaps[0].pain_points[0].template_id == "process_delay"
    assert _facts(snaps[0]).get("current_lead_time") == "8営業日"
    assert _facts(snaps[0]).get("normal_lead_time") == "2営業日"
    assert _facts(snaps[2]).get("bottleneck") == "法務確認"
    assert _facts(snaps[3]).get("business_impact") == "契約開始が遅れる"
    assert _facts(snaps[4]).get("owner") == "コンプライアンス運用チーム"
    assert _facts(snaps[5]).get("process_step") == "法務確認"


def test_process_delay_ja_normalizes_calendar_day_shorthand() -> None:
    result = analyze(_ev("承認工程は今8日かかり、通常は2日で完了します。", Lang.ja))
    assert result.detected_pain is not None
    assert result.detected_pain.template_id == "process_delay"
    assert {(fact.slot, fact.value) for fact in result.facts} >= {
        ("current_lead_time", "8営業日"),
        ("normal_lead_time", "2営業日"),
    }


@pytest.mark.parametrize(
    ("lang", "pain_text", "unrelated_text", "expected_current", "expected_normal"),
    [
        (
            Lang.ja,
            "承認工程は今8日かかり、通常は2日で完了します。",
            "保存期間は今30日で、通常は7日です。",
            "8営業日",
            "2営業日",
        ),
        (
            Lang.en,
            "The approval currently takes 8 business days instead of 2.",
            "Data retention currently takes 30 business days instead of 7.",
            "8 business days",
            "2 business days",
        ),
    ],
)
def test_unrelated_day_comparison_does_not_overwrite_process_delay_facts(
    lang: Lang,
    pain_text: str,
    unrelated_text: str,
    expected_current: str,
    expected_normal: str,
) -> None:
    engine = MeetingEngine(f"lead-time-scope-{lang.value}")
    assert engine.apply(_ev(pain_text, lang, 0))
    assert engine.apply(_ev(unrelated_text, lang, 1))

    lead_times = {
        fact.slot: fact.value
        for fact in engine.state.facts
        if fact.slot in {"current_lead_time", "normal_lead_time"} and fact.status == "active"
    }
    assert lead_times == {
        "current_lead_time": expected_current,
        "normal_lead_time": expected_normal,
    }
    assert all(
        fact.status != "superseded"
        for fact in engine.state.facts
        if fact.slot in {"current_lead_time", "normal_lead_time"}
    )


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        (
            "If the warehouse and EC inventory ever go out of sync, shipping would be delayed.",
            Lang.en,
        ),
        ('"Make the search screen easier to use" would be a nice example.', Lang.en),
        (
            "For example, the CSV import from EC into SAP fails before invoice posting.",
            Lang.en,
        ),
        ("もしCSVを手作業で転記したら大変です。", Lang.ja),
        ("「承認に8営業日かかる」と言われた件を確認します。", Lang.ja),
    ],
)
def test_hypothetical_and_quoted_examples_do_not_trigger_parity_rules(
    text: str,
    lang: Lang,
) -> None:
    result = analyze(_ev(text, lang))
    assert result.detected_pain is None


def test_ja_contradiction_marker_prefers_month_end_over_negated_daily_frequency() -> None:
    engine = MeetingEngine("ja-frequency-refine")
    assert engine.apply(_ev("倉庫側とEC側で在庫が合わないことがあります。", Lang.ja, 0))
    assert engine.apply(_ev("最近はほぼ毎朝、担当者が手動で直しています。", Lang.ja, 1))
    assert engine.apply(_ev("正確には毎日ではなく、月末だけです。", Lang.ja, 2))

    active = [
        fact for fact in engine.state.facts if fact.slot == "frequency" and fact.status == "active"
    ]
    superseded = [
        fact
        for fact in engine.state.facts
        if fact.slot == "frequency" and fact.status == "superseded"
    ]

    assert [fact.value for fact in active] == ["月末"]
    assert [fact.value for fact in superseded] == ["ほぼ毎朝"]


def test_small_talk_produces_no_pain() -> None:
    snaps = _replay("small-talk-no-pain-ja")
    final = snaps[max(snaps)]
    assert final.pain_points == []
    assert final.facts == []
    assert final.gaps == []
    assert [s for s in final.suggestions if s.status == "active"] == []


# --- provenance + evidence -------------------------------------------------


@pytest.mark.parametrize("demo_id", list(demo_ids()))
def test_evidence_ids_supported_and_facts_are_evidence(demo_id: str) -> None:
    snaps = _replay(demo_id)
    final = snaps[max(snaps)]
    emitted = {ev.event_id for ev in final.transcript}
    for f in final.facts:
        assert f.kind == "evidence"  # no inferred facts in deterministic mode
        assert f.evidence_event_ids and set(f.evidence_event_ids) <= emitted
    for p in final.pain_points:
        assert p.evidence_event_ids and set(p.evidence_event_ids) <= emitted


# --- question guard --------------------------------------------------------


def test_question_utterances_do_not_self_answer() -> None:
    # vague: seq1/seq3 are questions; their slots must stay open at that point.
    v = _replay("vague-requirement-ja")
    assert "current_problem" not in _facts(v[1])
    assert "expected_behavior" not in _facts(v[3])
    # vendor: seq1 (error question), seq4 (impact question).
    n = _replay("vendor-integration-failure-ja")
    assert "error_signal" not in _facts(n[1])
    assert "business_impact" not in _facts(n[4])
    # inventory: the PM's seq5 question literally contains 正/倉庫/EC — the highest-
    # risk self-answering case — and must NOT fill source_of_truth (answered at seq6).
    inv = _replay("inventory-mismatch-ja")
    assert "source_of_truth" not in _facts(inv[5])


# --- retraction + promotion + caps -----------------------------------------


@pytest.mark.parametrize("demo_id,answered", list(_ANSWERING.items()))
def test_retraction_and_promotion(demo_id: str, answered: str) -> None:
    snaps = _replay(demo_id)
    final = snaps[max(snaps)]
    assert answered not in _active_sugg_slots(final)  # stale suggestion retracted
    ask = _ask_now(final)
    assert ask is not None and ask != answered  # promoted to a different open gap


@pytest.mark.parametrize("demo_id", list(demo_ids()))
def test_caps_and_no_duplicate_suggestions(demo_id: str) -> None:
    for state in _replay(demo_id).values():
        ask = [s for s in state.suggestions if s.role == "ask_now" and s.status == "active"]
        follow = [s for s in state.suggestions if s.role == "follow_up" and s.status == "active"]
        assert len(ask) <= 1
        assert len(follow) <= 2
        active = [s.gap_id for s in state.suggestions if s.status == "active"]
        assert len(active) == len(set(active))  # no duplicate pain+gap


# --- demo registry safety --------------------------------------------------


def test_unknown_demo_id_fails_cleanly() -> None:
    assert resolve_demo("does-not-exist") is None
    assert resolve_demo("") is None


def test_demo_alias_resolves_to_inventory() -> None:
    src = resolve_demo("demo")
    assert src is not None and src.demo_id == "inventory-mismatch-ja"


def test_registry_rejects_arbitrary_paths() -> None:
    for evil in ("../../etc/passwd", "..\\..\\secret", "/etc/passwd", "evals/fixtures/x.jsonl"):
        assert resolve_demo(evil) is None


# --- WebSocket integration -------------------------------------------------


def _stream(demo_id: str) -> list[dict]:
    demo = resolve_demo(demo_id)
    assert demo is not None
    lines = demo.fixture_path.read_text(encoding="utf-8").splitlines()
    line_count = sum(1 for ln in lines if ln.strip())
    client = TestClient(create_app(replay_speed=0.0))
    messages: list[dict] = []
    with client.websocket_connect(f"/ws/meeting/{demo_id}") as ws:
        for _ in range(line_count + 1):  # v0 + one per accepted event
            messages.append(ws.receive_json())
    return messages


@pytest.mark.parametrize("demo_id", list(demo_ids()))
def test_ws_streams_strictly_increasing_versions(demo_id: str) -> None:
    versions = [m["version"] for m in _stream(demo_id)]
    assert versions[0] == 0
    assert all(b > a for a, b in zip(versions, versions[1:]))


def test_ws_final_matches_golden_finals() -> None:
    inv = MeetingState.model_validate(_stream("inventory-mismatch-ja")[-1])
    assert _facts(inv).get("source_of_truth") == "倉庫側"
    man = MeetingState.model_validate(_stream("manual-work-en")[-1])
    assert _facts(man).get("frequency") == "every business day"


def test_ws_small_talk_never_emits_active_suggestion() -> None:
    for msg in _stream("small-talk-no-pain-ja"):
        state = MeetingState.model_validate(msg)
        assert [s for s in state.suggestions if s.status == "active"] == []


def test_ws_live_session_id_stays_open_and_streams_ingested_updates() -> None:
    # A non-fixture opaque session_id is a real live session (Phase 2), not an error.
    # It must stay open (unlike the old "unknown demo -> close" contract) and
    # stream real state once the production ingestion endpoint feeds an event.
    app = create_app(replay_speed=0.0)
    client = TestClient(app)
    session_id = "meeting-intel-0123456789abcdef0123456789abcdef"
    with client.websocket_connect(f"/ws/meeting/{session_id}") as ws:
        first = ws.receive_json()
        assert first["version"] == 0 and first["meeting_id"] == session_id

        resp = client.post(
            f"/ingest/meetily/{session_id}",
            json={
                "lang": "en",
                "payload": {
                    "text": "Every morning one person copies the rows into a spreadsheet.",
                    "source": "Audio",
                    "sequence_id": 0,
                    "is_partial": False,
                    "confidence": 0.9,
                    "audio_start_time": 0.0,
                    "audio_end_time": 3.0,
                },
            },
        )
        assert resp.status_code == 200 and resp.json()["status"] == "applied"

        updated = ws.receive_json()
        assert updated["version"] == 1
        assert "transcript" not in updated
        assert updated["pain_points"][0]["template_id"] == "manual_work"
