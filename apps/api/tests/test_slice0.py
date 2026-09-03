"""Slice 0 verification: contracts round-trip, layer purity, schema drift, WS wire.

Executable DoD checks for Slice 0 (docs/DEVELOPMENT_PLAN.md). No intelligence is
exercised — only the contract shapes and the transport.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.live_contracts import (
    MAX_LIVE_EVIDENCE_EVENT_IDS,
    LiveIntelligenceSnapshot,
)
from app.api.main import app
from app.domain.contracts import (
    FactKind,
    GapStatus,
    InformationGap,
    Lang,
    MeetingFact,
    MeetingState,
    PainPoint,
    QuestionSuggestion,
    Speaker,
    SuggestionRole,
    TranscriptEvent,
    empty_meeting_state,
)

API_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_ROOT = API_ROOT / "app"
REPO_ROOT = API_ROOT.parent.parent
SCHEMA_PATH = REPO_ROOT / "contracts" / "meeting_state.schema.json"
CONTEXT_SCHEMA_PATH = REPO_ROOT / "contracts" / "context.schema.json"
GOVERNANCE_SCHEMA_PATH = REPO_ROOT / "contracts" / "governance.schema.json"
EVIDENCE_SCHEMA_PATH = REPO_ROOT / "contracts" / "evidence.schema.json"
ASSURANCE_SCHEMA_PATH = REPO_ROOT / "contracts" / "assurance.schema.json"
SOLUTION_THREAD_SCHEMA_PATH = REPO_ROOT / "contracts" / "solution_thread.schema.json"
IMPROVEMENT_SCHEMA_PATH = REPO_ROOT / "contracts" / "improvements.schema.json"
CONSISTENCY_SCHEMA_PATH = REPO_ROOT / "contracts" / "consistency.schema.json"


# --- contract round-trip ---------------------------------------------------


def _sample_state() -> MeetingState:
    ev = "e1"
    fact = MeetingFact(
        fact_id="f1",
        meeting_id="m1",
        pain_id="p1",
        slot="frequency",
        value="ほぼ毎朝",
        kind=FactKind.evidence,
        evidence_event_ids=[ev],
        created_seq=2,
    )
    pain = PainPoint(
        pain_id="p1",
        meeting_id="m1",
        template_id="data_mismatch",
        title="inventory mismatch",
        kind=FactKind.evidence,
        evidence_event_ids=[ev],
        created_seq=1,
    )
    gap = InformationGap(
        gap_id="p1::source_of_truth",
        meeting_id="m1",
        pain_id="p1",
        slot="source_of_truth",
        status=GapStatus.open,
        base_priority=0,
    )
    sug = QuestionSuggestion(
        suggestion_id="s1",
        gap_id="p1::source_of_truth",
        role=SuggestionRole.ask_now,
        text="現在、在庫の正はどちらですか？",
        reason="authoritative source unclear",
        priority=0,
    )
    return MeetingState(
        meeting_id="m1",
        version=3,
        pain_points=[pain],
        facts=[fact],
        gaps=[gap],
        suggestions=[sug],
        last_event_seq=2,
    )


def test_contracts_round_trip_via_json() -> None:
    state = _sample_state()
    assert MeetingState.model_validate_json(state.model_dump_json()) == state


def test_live_projection_bounds_repeated_evidence_without_mutating_domain_state() -> None:
    state = _sample_state()
    event_ids = [f"e{index}" for index in range(20)]
    state = state.model_copy(
        update={
            "pain_points": [
                state.pain_points[0].model_copy(update={"evidence_event_ids": list(event_ids)})
            ],
            "facts": [state.facts[0].model_copy(update={"evidence_event_ids": list(event_ids)})],
        }
    )

    snapshot = LiveIntelligenceSnapshot.from_state(state)
    expected = [event_ids[0], *event_ids[-(MAX_LIVE_EVIDENCE_EVENT_IDS - 1) :]]
    assert snapshot.pain_points[0].evidence_event_ids == expected
    assert snapshot.facts[0].evidence_event_ids == expected
    assert state.pain_points[0].evidence_event_ids == event_ids
    assert state.facts[0].evidence_event_ids == event_ids


def test_transcript_event_round_trip() -> None:
    ev = TranscriptEvent(
        event_id="e1",
        meeting_id="m1",
        seq=0,
        speaker=Speaker(id="sp1", role="EC Manager"),
        text="在庫が合わない",
        lang=Lang.ja,
        ts_start=0.0,
        ts_end=1.5,
        source="jsonl_replay",
    )
    assert TranscriptEvent.model_validate_json(ev.model_dump_json()) == ev


def test_empty_state_defaults() -> None:
    state = empty_meeting_state("m1")
    assert state.version == 0
    assert state.last_event_seq == -1
    assert state.pain_points == []
    assert state.facts == []
    assert state.gaps == []
    assert state.suggestions == []


def test_fact_requires_evidence_event_ids() -> None:
    # Invariant I1: a fact may not exist without the evidence it derives from.
    with pytest.raises(ValidationError):
        MeetingFact(
            fact_id="f1",
            meeting_id="m1",
            pain_id="p1",
            slot="frequency",
            value="x",
            kind=FactKind.evidence,
            evidence_event_ids=[],
            created_seq=1,
        )


def test_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Speaker.model_validate({"id": "sp1", "unexpected": True})


# --- layer purity (import-boundary contract) -------------------------------

_PURE_LAYERS = ("domain", "services")
_BANNED_IMPORT_PREFIXES = ("app.adapters",)
_BANNED_VENDOR_ROOTS = {
    "fastapi",
    "starlette",
    "uvicorn",
    "httpx",
    "httpx2",
    "requests",
    "ollama",
    "whisper",
    "whispercpp",
    "vexa",
}


def _imported_modules(py_file: pathlib.Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_pure_layers_do_not_import_adapters_or_vendors() -> None:
    offenders: list[str] = []
    for layer in _PURE_LAYERS:
        layer_dir = APP_ROOT / layer
        if not layer_dir.exists():
            continue
        for py_file in layer_dir.rglob("*.py"):
            for module in _imported_modules(py_file):
                root = module.split(".")[0]
                if module.startswith(_BANNED_IMPORT_PREFIXES) or root in _BANNED_VENDOR_ROOTS:
                    offenders.append(f"{py_file.relative_to(APP_ROOT)} -> {module}")
    assert not offenders, "pure layers import forbidden modules: " + "; ".join(offenders)


# --- schema drift (schema-diff, api side) ----------------------------------


def _load_export_schema_module():
    spec = importlib.util.spec_from_file_location(
        "export_schema", API_ROOT / "scripts" / "export_schema.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["export_schema"] = module
    spec.loader.exec_module(module)
    return module


def test_committed_schema_matches_models() -> None:
    assert SCHEMA_PATH.exists(), (
        "contracts/meeting_state.schema.json missing — run `python scripts/export_schema.py`"
    )
    export_schema = _load_export_schema_module()
    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed == export_schema.build_schema(), (
        "schema drift — regenerate with `python scripts/export_schema.py`"
    )
    assert CONTEXT_SCHEMA_PATH.exists(), (
        "contracts/context.schema.json missing — run `python scripts/export_schema.py`"
    )
    committed_context = json.loads(CONTEXT_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert committed_context == export_schema.build_context_schema(), (
        "context schema drift — regenerate with `python scripts/export_schema.py`"
    )
    expected = {
        GOVERNANCE_SCHEMA_PATH: export_schema.build_governance_schema(),
        EVIDENCE_SCHEMA_PATH: export_schema._build_contract_schema(
            export_schema._EVIDENCE_MODELS, "MeetingIntelligenceEvidenceContracts"
        ),
        ASSURANCE_SCHEMA_PATH: export_schema._build_contract_schema(
            export_schema._ASSURANCE_MODELS, "MeetingIntelligenceAssuranceContracts"
        ),
        SOLUTION_THREAD_SCHEMA_PATH: export_schema._build_contract_schema(
            export_schema._SOLUTION_THREAD_MODELS,
            "MeetingIntelligenceSolutionThreadContracts",
        ),
        export_schema.INTEGRATION_SCHEMA_PATH: export_schema._build_contract_schema(
            export_schema._INTEGRATION_MODELS,
            "MeetingIntelligenceIntegrationContracts",
        ),
        IMPROVEMENT_SCHEMA_PATH: export_schema._build_contract_schema(
            export_schema._IMPROVEMENT_MODELS,
            "MeetingIntelligenceImprovementContracts",
        ),
        CONSISTENCY_SCHEMA_PATH: export_schema._build_contract_schema(
            export_schema._CONSISTENCY_MODELS,
            "MeetingIntelligenceConsistencyContracts",
        ),
    }
    for path, generated in expected.items():
        assert path.exists(), f"{path.name} missing — run `python scripts/export_schema.py`"
        assert json.loads(path.read_text(encoding="utf-8")) == generated, (
            f"{path.name} schema drift — regenerate with `python scripts/export_schema.py`"
        )


# --- transport (WS emits one empty versioned snapshot) ---------------------


def test_live_ws_emits_empty_compact_versioned_snapshot() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/meeting/m1") as ws:
        payload = ws.receive_json()
    snapshot = LiveIntelligenceSnapshot.model_validate(payload)
    assert snapshot.meeting_id == "m1"
    assert snapshot.version == 0
    assert snapshot.pain_points == []
    assert snapshot.suggestions == []
    assert "transcript" not in payload


def test_health_ok() -> None:
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}


def test_compatibility_health_is_versioned_without_expanding_basic_health() -> None:
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/compatibility").json() == {
        "status": "ok",
        "product": "meeting-intelligence-copilot",
        "api_version": 13,
        "backend_version": "0.6.1",
        "capability_auth": False,
    }
