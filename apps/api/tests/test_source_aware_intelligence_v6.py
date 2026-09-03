from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.analysis.semantic_runtime import SemanticHintRuntime, _validated_hints
from app.adapters.context.encrypted_index import EncryptedContextIndex
from app.adapters.context.problem_context import resolve_problem_context
from app.adapters.context.service import ContextConnectorService
from app.adapters.transcript.live_registry import LiveMeetingRegistry
from app.adapters.transcript.live_session_store import LiveSessionStore
from app.api.main import CAPABILITY_TOKEN_HEADER, create_app
from app.domain.context import (
    ConnectedProblemContextRequest,
    EntityGlossary,
    EntityGlossaryEntry,
    EntityGlossaryKind,
)
from app.domain.contracts import Lang, Speaker, TranscriptEvent
from app.domain.semantic_candidates import (
    CandidateKind,
    FactCandidate,
    PainCandidate,
    SemanticHintFact,
    SemanticObservationBatch,
    SemanticProblemHint,
)
from app.services.analyzer import analyze
from app.services.meeting_engine import MeetingEngine

SESSION_ID = "meeting-intel-abcdef0123456789abcdef0123456789"
TOKEN = "v6-source-aware-capability-token-000000000000"


class XorProtector:
    def protect(self, value: bytes) -> bytes:
        return bytes(byte ^ 0xA5 for byte in value)

    def unprotect(self, value: bytes) -> bytes:
        return self.protect(value)


class MemorySecretStore:
    def put(self, target: str, secret: str) -> None:
        del target, secret

    def get(self, target: str) -> str | None:
        del target
        return None

    def delete(self, target: str) -> bool:
        del target
        return False


def event(seq: int, text: str, lang: Lang = Lang.en) -> TranscriptEvent:
    return TranscriptEvent(
        event_id=f"event-{seq}",
        meeting_id=SESSION_ID,
        seq=seq,
        speaker=Speaker(id="speaker"),
        text=text,
        lang=lang,
        ts_start=float(seq),
        ts_end=float(seq + 1),
        source="test",
    )


def payload(seq: int, text: str) -> dict[str, object]:
    return {
        "text": text,
        "source": "Audio",
        "sequence_id": seq,
        "is_partial": False,
        "confidence": 0.9,
        "audio_start_time": float(seq),
        "audio_end_time": float(seq + 1),
        "duration": 1.0,
    }


def glossary(*entries: EntityGlossaryEntry) -> list[EntityGlossaryEntry]:
    return list(entries)


def system(entry_id: str, name: str, *aliases: str) -> EntityGlossaryEntry:
    return EntityGlossaryEntry(
        entry_id=entry_id,
        kind=EntityGlossaryKind.system,
        canonical_name=name,
        aliases=list(aliases),
    )


def test_glossary_uses_longest_alias_and_keeps_identity_opaque() -> None:
    entries = glossary(
        system("nova", "Nova", "NV"),
        system("nova-erp", "Nova ERP", "NV ERP"),
        system("atlas", "Atlas Hub", "AH"),
        EntityGlossaryEntry(
            entry_id="shipment",
            kind=EntityGlossaryKind.business_object,
            canonical_name="Shipment",
            aliases=["shipment record"],
        ),
    )
    result = analyze(
        event(0, "NV ERP and AH shipment records do not match."),
        glossary=entries,
    )
    pain = next(item for item in result.detected_pains if item.template_id == "data_mismatch")

    assert pain.subject == "Atlas Hub <-> Nova ERP Shipment mismatch"
    assert "Nova" not in pain.instance_key
    assert "Atlas" not in pain.instance_key
    assert pain.identity_status == "anchored"


def test_six_hundred_bilingual_identity_trajectories_have_no_cross_instance_keys() -> None:
    anchored_keys: set[str] = set()
    provisional = 0
    for index in range(600):
        left = f"SystemA{index}"
        right = f"SystemB{index}"
        entries = glossary(system(f"a-{index}", left), system(f"b-{index}", right))
        if index < 180:  # Thirty percent hard negatives lack the second anchor.
            text = f"{left} inventory is available."
        elif index % 2:
            text = f"{left} and {right} inventory do not match."
        else:
            text = f"{left}側と{right}側で在庫が合わない。"
        result = analyze(
            event(index, text, Lang.ja if index % 2 == 0 else Lang.en),
            glossary=entries,
        )
        pains = [item for item in result.detected_pains if item.template_id == "data_mismatch"]
        if index < 180:
            assert pains == []
            provisional += 1
            continue
        assert len(pains) == 1
        assert pains[0].instance_key not in anchored_keys
        anchored_keys.add(pains[0].instance_key)

    assert provisional == 180
    assert len(anchored_keys) == 420


def test_glossary_is_revisioned_and_encrypted_at_rest(tmp_path: Path) -> None:
    index = EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector())
    service = ContextConnectorService(
        index=index,
        secret_store=MemorySecretStore(),
        principal_id="test-user",
    )
    requested = EntityGlossary(
        entries=[system("private-system", "CompanySecretERP", "CSE")]
    )

    updated = asyncio.run(service.update_entity_glossary(requested))

    assert updated.revision == 1
    assert service.entity_glossary().entries[0].canonical_name == "CompanySecretERP"
    assert b"CompanySecretERP" not in (tmp_path / "context.sqlite3").read_bytes()
    with pytest.raises(RuntimeError, match="stale_glossary"):
        asyncio.run(service.update_entity_glossary(requested))
    asyncio.run(service.close())


class RecordingAnalyzer:
    def __init__(self) -> None:
        self.transcript_lengths: list[int] = []

    def observe(self, events: list[TranscriptEvent]) -> SemanticObservationBatch:
        self.transcript_lengths.append(len(events))
        time.sleep(0.04)
        last = events[-1]
        return SemanticObservationBatch(
            pains=[
                PainCandidate(
                    category="integration_failure",
                    title="Possible integration failure",
                    kind=CandidateKind.evidence,
                    evidence_event_ids=[last.event_id],
                    confidence=0.97,
                    reason="test",
                )
            ]
        )


def test_semantic_runtime_coalesces_busy_updates_to_the_latest_state() -> None:
    async def scenario() -> None:
        analyzer = RecordingAnalyzer()
        runtime = SemanticHintRuntime(analyzer)
        first = MeetingEngine(SESSION_ID)
        first.apply(event(0, "A synchronization defect affects orders."))
        second = MeetingEngine.from_state(first.state)
        second.apply(event(1, "The interface stops during order transfer."))
        published: list[str] = []

        runtime.schedule(first.state, published.append)
        await asyncio.sleep(0)
        runtime.schedule(second.state, published.append)
        await asyncio.sleep(0.15)

        assert analyzer.transcript_lengths == [1, 2]
        assert published == [SESSION_ID, SESSION_ID]
        assert runtime.hints(SESSION_ID)[0].state_version == second.state.version
        await runtime.close()

    asyncio.run(scenario())


def test_confirmed_semantic_hint_replays_without_model(tmp_path: Path) -> None:
    store = LiveSessionStore(cache_dir=tmp_path)
    registry = LiveMeetingRegistry(session_store=store)
    result = registry.ingest_with_ack(
        SESSION_ID,
        "en",
        payload(0, "A synchronization defect affects orders."),
    )
    assert result.state is not None
    evidence_id = result.state.transcript[0].event_id
    hint = SemanticProblemHint(
        hint_id="hint-0123456789abcdef01234567",
        session_id=SESSION_ID,
        category="integration_failure",
        subject="Integration failure",
        language="en",
        evidence_event_ids=[evidence_id],
        facts=[
            SemanticHintFact(
                slot="failure_point",
                value="synchronization defect",
                kind=CandidateKind.evidence,
                evidence_event_ids=[evidence_id],
            )
        ],
        confidence=0.97,
        state_version=result.state.version,
    )

    confirmed = registry.confirm_semantic_hint(SESSION_ID, hint)
    dismissed_id = "hint-aaaaaaaaaaaaaaaaaaaaaaaa"
    registry.record_semantic_hint_decision(SESSION_ID, dismissed_id, "dismiss")
    assert confirmed.confirmed_semantic_hint_ids == [hint.hint_id]
    assert any(fact.extraction_origin == "semantic_confirmed" for fact in confirmed.facts)
    registry.close()

    recovered_store = LiveSessionStore(cache_dir=tmp_path)
    recovered_registry = LiveMeetingRegistry(session_store=recovered_store)
    recovered = recovered_registry.current_state(SESSION_ID)
    assert recovered is not None
    assert recovered.confirmed_semantic_hint_ids == [hint.hint_id]
    assert any(fact.extraction_origin == "semantic_confirmed" for fact in recovered.facts)
    assert recovered_registry.dismissed_semantic_hint_ids(SESSION_ID) == {dismissed_id}
    recovered_registry.close()


def test_identity_decisions_are_append_only_and_recoverable(tmp_path: Path) -> None:
    store = LiveSessionStore(cache_dir=tmp_path)
    registry = LiveMeetingRegistry(session_store=store)
    registry.ingest_with_ack(SESSION_ID, "en", payload(0, "SAP and EC inventory do not match."))
    result = registry.ingest_with_ack(
        SESSION_ID,
        "en",
        payload(1, "NetSuite and warehouse side inventory do not match."),
    )
    assert result.state is not None
    pain_ids = [pain.pain_id for pain in result.state.pain_points]

    decided = registry.decide_identity(SESSION_ID, "keep_separate", pain_ids)
    assert len(decided.identity_decisions) == 1
    registry.close()

    recovered_store = LiveSessionStore(cache_dir=tmp_path)
    recovered_registry = LiveMeetingRegistry(session_store=recovered_store)
    recovered = recovered_registry.current_state(SESSION_ID)
    assert recovered is not None
    assert recovered.identity_decisions == decided.identity_decisions
    recovered_registry.close()


def test_merge_retains_lineage_while_keep_separate_prevents_automatic_review() -> None:
    engine = MeetingEngine(SESSION_ID)
    engine.apply(event(0, "SAP and EC inventory do not match."))
    engine.apply(event(1, "NetSuite and warehouse side inventory do not match."))
    first, second = engine.state.pain_points

    engine.decide_identity("keep_separate", [first.pain_id, second.pain_id])
    assert engine.state.identity_decisions[-1].action == "keep_separate"
    engine.decide_identity("merge", [first.pain_id, second.pain_id])

    survivor = next(pain for pain in engine.state.pain_points if pain.merged_into_pain_id is None)
    merged = next(pain for pain in engine.state.pain_points if pain.merged_into_pain_id is not None)
    assert survivor.pain_id == first.pain_id
    assert merged.merged_into_pain_id == survivor.pain_id
    assert set(survivor.evidence_event_ids) == {"event-0", "event-1"}
    assert len(engine.state.identity_decisions) == 2


def test_ambiguous_provisional_promotion_never_guesses() -> None:
    engine = MeetingEngine(SESSION_ID)
    engine.apply(event(0, "Inventory figures do not match."))
    engine.apply(event(1, "Inventory totals still do not match."))
    provisional_ids = [pain.pain_id for pain in engine.state.pain_points]
    prior_fact_ids = [fact.fact_id for fact in engine.state.facts]
    assert len(provisional_ids) == 2

    engine.apply(event(2, "SAP and EC inventory do not match."))

    assert len(engine.state.pain_points) == 3
    assert all(
        pain.identity_status.value == "provisional"
        for pain in engine.state.pain_points
        if pain.pain_id in provisional_ids
    )
    assert engine.state.ambiguous_routing_count >= 1
    assert [fact.fact_id for fact in engine.state.facts] == prior_fact_ids
    assert all("event-2" not in fact.evidence_event_ids for fact in engine.state.facts)


def test_problem_context_is_bound_to_authoritative_problem_and_open_slot() -> None:
    engine = MeetingEngine(SESSION_ID)
    engine.apply(event(0, "SAP and EC inventory do not match."))
    pain = engine.state.pain_points[0]
    request = ConnectedProblemContextRequest(
        session_id=SESSION_ID,
        pain_id=pain.pain_id,
        state_version=engine.state.version,
        limit=5,
    )

    resolution = resolve_problem_context(engine.state, request, principal_id="test-user")

    assert resolution is not None
    assert "SAP" in resolution.query.text
    assert "EC" in resolution.query.text
    assert resolution.query.target_slot == "source_of_truth"
    assert resolution.query.principal_id == "test-user"
    assert resolve_problem_context(
        engine.state,
        request.model_copy(update={"state_version": engine.state.version - 1}),
        principal_id="test-user",
    ) is None


def test_four_hundred_bilingual_semantic_cases_repeat_deterministically_three_times() -> None:
    cases: list[tuple[str, Lang, bool]] = []
    negative_texts = [
        "Could the order interface be failing?",
        "If the order interface failed, we would investigate.",
        '"The order interface failed during transfer."',
        "There is no issue with the order interface.",
        "注文インターフェースで障害が発生していますか？",
        "もし注文連携が失敗した場合は調査します。",
    ]
    for index in range(400):
        negative = index < 120
        lang = Lang.ja if index % 2 == 0 else Lang.en
        text = (
            negative_texts[index % len(negative_texts)]
            if negative
            else (
                "注文インターフェースで転送障害が発生しました。"
                if lang is Lang.ja
                else "The order interface failed during transfer."
            )
        )
        cases.append((text, lang, not negative))

    for _run in range(3):
        accepted = 0
        for index, (text, lang, expected) in enumerate(cases):
            transcript_event = event(index, text, lang)
            engine = MeetingEngine(SESSION_ID)
            engine.apply(transcript_event)
            batch = SemanticObservationBatch(
                pains=[
                    PainCandidate(
                        category="integration_failure",
                        title="Candidate",
                        kind=CandidateKind.evidence,
                        evidence_event_ids=[transcript_event.event_id],
                        confidence=0.96,
                        reason="mocked bilingual evaluation",
                    )
                ]
            )
            hints = _validated_hints(engine.state, batch)
            assert bool(hints) is expected
            accepted += bool(hints)
        assert accepted == 280


def test_semantic_fact_requires_literal_same_language_transcript_span() -> None:
    transcript_event = event(0, "The order interface failed during transfer.")
    engine = MeetingEngine(SESSION_ID)
    assert engine.apply(transcript_event)

    def observations(value: str, evidence: TranscriptEvent) -> SemanticObservationBatch:
        return SemanticObservationBatch(
            pains=[
                PainCandidate(
                    category="integration_failure",
                    title="Candidate",
                    kind=CandidateKind.evidence,
                    evidence_event_ids=[transcript_event.event_id],
                    confidence=0.96,
                    reason="test",
                )
            ],
            facts=[
                FactCandidate(
                    slot="failure_point",
                    value=value,
                    kind=CandidateKind.evidence,
                    evidence_event_ids=[evidence.event_id],
                    pain_category_hint="integration_failure",
                    confidence=0.96,
                    reason="test",
                )
            ],
        )

    exact = _validated_hints(
        engine.state,
        observations("failed during transfer", transcript_event),
    )
    normalized_paraphrase = _validated_hints(
        engine.state,
        observations("Failed During Transfer", transcript_event),
    )
    japanese_evidence = event(1, "転送中に失敗しました。", Lang.ja)
    mixed_state = engine.state.model_copy(
        update={"transcript": [*engine.state.transcript, japanese_evidence]}
    )
    mixed_language = _validated_hints(
        mixed_state,
        observations("転送中に失敗", japanese_evidence),
    )

    assert exact[0].facts[0].value == "failed during transfer"
    assert normalized_paraphrase[0].facts == []
    assert mixed_language[0].facts == []


def test_authenticated_v6_glossary_problem_context_and_identity_endpoints(
    tmp_path: Path,
) -> None:
    context_service = ContextConnectorService(
        index=EncryptedContextIndex(tmp_path / "context.sqlite3", XorProtector()),
        secret_store=MemorySecretStore(),
        principal_id="test-user",
    )
    registry = LiveMeetingRegistry()
    app = create_app(
        capability_token=TOKEN,
        live_registry_instance=registry,
        context_service_instance=context_service,
    )
    headers = {CAPABILITY_TOKEN_HEADER: TOKEN}

    with TestClient(app) as client:
        assert client.get("/configuration/entity-glossary").status_code == 401
        saved = client.put(
            "/configuration/entity-glossary",
            headers=headers,
            json={
                "revision": 0,
                "entries": [
                    {
                        "entry_id": "orion",
                        "kind": "system",
                        "canonical_name": "Orion ERP",
                        "aliases": ["OE"],
                        "language": None,
                        "enabled": True,
                    }
                ],
            },
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        assert client.put(
            "/configuration/entity-glossary",
            headers=headers,
            json={"revision": 0, "entries": []},
        ).status_code == 409

        for seq, text in enumerate(
            [
                "SAP and EC inventory do not match.",
                "NetSuite and warehouse side inventory do not match.",
            ]
        ):
            response = client.post(
                f"/ingest/live/{SESSION_ID}",
                headers=headers,
                json={"adapter": "meetily", "lang": "en", "payload": payload(seq, text)},
            )
            assert response.status_code == 200
        state = registry.current_state(SESSION_ID)
        assert state is not None
        first, second = state.pain_points

        stale = client.post(
            "/problems/identity-decisions",
            headers=headers,
            json={
                "session_id": SESSION_ID,
                "state_version": state.version - 1,
                "action": "keep_separate",
                "pain_ids": [first.pain_id, second.pain_id],
            },
        )
        assert stale.status_code == 409
        decided = client.post(
            "/problems/identity-decisions",
            headers=headers,
            json={
                "session_id": SESSION_ID,
                "state_version": state.version,
                "action": "keep_separate",
                "pain_ids": [first.pain_id, second.pain_id],
            },
        )
        assert decided.status_code == 200

        current = registry.current_state(SESSION_ID)
        assert current is not None
        context = client.post(
            "/context/problem-context",
            headers=headers,
            json={
                "session_id": SESSION_ID,
                "pain_id": first.pain_id,
                "state_version": current.version,
                "limit": 5,
            },
        )
        assert context.status_code == 200
        assert context.json()["status"] == "current"
        assert context.json()["pain_id"] == first.pain_id


def test_semantic_hint_endpoint_requires_explicit_confirmation(tmp_path: Path) -> None:
    runtime = SemanticHintRuntime(RecordingAnalyzer())
    registry = LiveMeetingRegistry(
        session_store=LiveSessionStore(cache_dir=tmp_path / "sessions")
    )
    app = create_app(
        capability_token=TOKEN,
        live_registry_instance=registry,
        semantic_runtime_instance=runtime,
    )
    headers = {CAPABILITY_TOKEN_HEADER: TOKEN}

    with TestClient(app) as client:
        response = client.post(
            f"/ingest/live/{SESSION_ID}",
            headers=headers,
            json={
                "adapter": "meetily",
                "lang": "en",
                "payload": payload(0, "A synchronization defect affects orders."),
            },
        )
        assert response.status_code == 200
        for _ in range(50):
            hints = runtime.hints(SESSION_ID)
            if hints:
                break
            time.sleep(0.01)
        assert len(hints) == 1
        before = registry.current_state(SESSION_ID)
        assert before is not None
        assert before.confirmed_semantic_hint_ids == []

        confirmed = client.post(
            f"/problems/hints/{hints[0].hint_id}/decisions",
            headers=headers,
            json={
                "session_id": SESSION_ID,
                "state_version": before.version,
                "action": "confirm",
            },
        )
        assert confirmed.status_code == 200
        after = registry.current_state(SESSION_ID)
        assert after is not None
        assert after.confirmed_semantic_hint_ids == [hints[0].hint_id]
        assert hints[0].hint_id not in {
            hint["hint_id"] for hint in confirmed.json()["semantic_hints"]
        }
