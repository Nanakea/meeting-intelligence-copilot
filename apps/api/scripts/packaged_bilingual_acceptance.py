"""Exercise JA/EN/KO question-copilot behavior through the packaged backend."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT = (
    REPO_ROOT
    / "dist"
    / "backend-sidecar"
    / "meeting-intelligence-backend-x86_64-pc-windows-msvc.exe"
)
TOKEN_HEADER = "X-Meeting-Intelligence-Token"
WS_PROTOCOL = "meeting-intelligence-v1"
ORIGIN = "http://tauri.localhost"
FIXTURES = {
    "en": REPO_ROOT / "evals" / "fixtures" / "manual-work-en.jsonl",
    "ja": REPO_ROOT / "evals" / "fixtures" / "owner-answer-ja.jsonl",
    "ko": REPO_ROOT / "evals" / "fixtures" / "data-mismatch-ko.jsonl",
}
GOLDENS = {
    "en": REPO_ROOT / "evals" / "golden" / "manual-work-en.json",
    "ja": REPO_ROOT / "evals" / "golden" / "owner-answer-ja.json",
    "ko": REPO_ROOT / "evals" / "golden" / "data-mismatch-ko.json",
}


def http_json(
    base_url: str,
    path: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={
            TOKEN_HEADER: token,
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def active_facts(snapshot: dict[str, Any]) -> dict[str, str]:
    return {
        fact["slot"]: fact["value"]
        for fact in snapshot["facts"]
        if fact["status"] == "active"
    }


def slots_by_status(snapshot: dict[str, Any], status: str) -> set[str]:
    return {gap["slot"] for gap in snapshot["gaps"] if gap["status"] == status}


def active_suggestion_slots(snapshot: dict[str, Any]) -> set[str]:
    slots = {gap["gap_id"]: gap["slot"] for gap in snapshot["gaps"]}
    return {
        slots[suggestion["gap_id"]]
        for suggestion in snapshot["suggestions"]
        if suggestion["status"] == "active"
    }


def ask_now_slot(snapshot: dict[str, Any]) -> str | None:
    slots = {gap["gap_id"]: gap["slot"] for gap in snapshot["gaps"]}
    for suggestion in snapshot["suggestions"]:
        if suggestion["role"] == "ask_now" and suggestion["status"] == "active":
            return slots[suggestion["gap_id"]]
    return None


def assert_snapshot_invariants(
    snapshot: dict[str, Any], emitted_event_ids: set[str]
) -> None:
    active = [item for item in snapshot["suggestions"] if item["status"] == "active"]
    assert len([item for item in active if item["role"] == "ask_now"]) <= 1
    assert len([item for item in active if item["role"] == "follow_up"]) <= 2
    assert len({item["gap_id"] for item in active}) == len(active)
    assert "transcript" not in snapshot
    for item in [*snapshot["pain_points"], *snapshot["facts"]]:
        evidence = set(item["evidence_event_ids"])
        assert evidence and evidence <= emitted_event_ids
    assert "\ufffd" not in json.dumps(snapshot, ensure_ascii=False)


def assert_checkpoint(
    snapshot: dict[str, Any],
    checkpoint: dict[str, Any],
    emitted_event_ids: set[str],
) -> None:
    facts = active_facts(snapshot)
    open_slots = slots_by_status(snapshot, "open")
    answered_slots = slots_by_status(snapshot, "answered")
    suggestion_slots = active_suggestion_slots(snapshot)

    if category := checkpoint.get("pain_category"):
        assert any(pain["template_id"] == category for pain in snapshot["pain_points"])
    for slot, value in checkpoint.get("known_facts", {}).items():
        assert facts.get(slot) == value, (slot, facts.get(slot), value)
    for slot in checkpoint.get("known_facts_absent", []):
        assert slot not in facts
    for expected in checkpoint.get("facts_history", []):
        assert any(
            fact["slot"] == expected["slot"]
            and fact["value"] == expected["value"]
            and fact["status"] == expected["status"]
            for fact in snapshot["facts"]
        )
    for slot in checkpoint.get("open_gaps_include", []):
        assert slot in open_slots
    for slot in checkpoint.get("open_gaps_exclude", []):
        assert slot not in open_slots
    for slot in checkpoint.get("answered_gaps", []):
        assert slot in answered_slots
    if expected_ask := checkpoint.get("ask_now_gap"):
        assert ask_now_slot(snapshot) == expected_ask
    for slot in checkpoint.get("no_active_suggestion_gaps", []):
        assert slot not in suggestion_slots
    assert_snapshot_invariants(snapshot, emitted_event_ids)


async def receive_snapshot(websocket: Any, timeout: float = 5.0) -> dict[str, Any]:
    message = await asyncio.wait_for(websocket.recv(), timeout)
    assert isinstance(message, str)
    return json.loads(message)


async def run_scenario(
    *,
    base_url: str,
    token: str,
    lang: str,
    marker: str,
) -> None:
    fixture = load_jsonl(FIXTURES[lang])
    golden = json.loads(GOLDENS[lang].read_text(encoding="utf-8"))
    checkpoints = {item["after_event_seq"]: item for item in golden["checkpoints"]}
    session_id = f"meeting-intel-packaged-{lang}-{uuid.uuid4().hex}"
    websocket_url = (
        f"ws://127.0.0.1:{base_url.rsplit(':', 1)[1]}"
        f"/ws/meeting/{session_id}"
    )
    emitted_event_ids: set[str] = set()

    async with connect(
        websocket_url,
        origin=ORIGIN,
        subprotocols=[WS_PROTOCOL, f"token.{token}"],
        open_timeout=5,
        close_timeout=2,
    ) as websocket:
        initial = await receive_snapshot(websocket)
        assert initial["version"] == 0 and initial["meeting_id"] == session_id
        assert_snapshot_invariants(initial, emitted_event_ids)

        for sequence_id, event in enumerate(fixture):
            text = event["text"]
            if lang == "en" and sequence_id == 0:
                text = f"{text} {marker}"
            offset_seconds = float(event["offset_ms"]) / 1000.0
            result = http_json(
                base_url,
                f"/ingest/meetily/{session_id}",
                token,
                method="POST",
                payload={
                    "lang": lang,
                    "payload": {
                        "text": text,
                        "source": "Audio",
                        "sequence_id": sequence_id,
                        "is_partial": False,
                        "confidence": 0.9,
                        "audio_start_time": offset_seconds,
                        "audio_end_time": offset_seconds + 3.0,
                    },
                },
            )
            assert result == {
                "status": "applied",
                "received_sequence_id": sequence_id,
                "next_expected_sequence_id": sequence_id + 1,
                "state_version": sequence_id + 1,
            }
            emitted_event_ids.add(f"{session_id}:seg{sequence_id}")
            snapshot = await receive_snapshot(websocket)
            assert snapshot["version"] == sequence_id + 1
            assert_snapshot_invariants(snapshot, emitted_event_ids)
            if checkpoint := checkpoints.get(sequence_id):
                assert_checkpoint(snapshot, checkpoint, emitted_event_ids)

        final = snapshot
        if lang == "en":
            assert active_facts(final)["frequency"] == "every business day"
            assert ask_now_slot(final) == "volume"
            assert "business_impact" not in active_suggestion_slots(final)
        elif lang == "ja":
            assert active_facts(final)["source_of_truth"] == "倉庫側"
            assert active_facts(final)["business_impact"] == "出荷作業の遅延"
            assert active_facts(final)["owner"] == "一次対応: 山田さん / 最終確認: 情シス"
            assert ask_now_slot(final) == "affected_scope"
            assert "owner" not in active_suggestion_slots(final)
        else:
            assert active_facts(final)["source_of_truth"] == "WMS"
            assert active_facts(final)["business_impact"] == "이 차이 때문에 출하가 지연됩니다."
            assert ask_now_slot(final) == "owner"
            assert "source_of_truth" not in active_suggestion_slots(final)

    assert http_json(base_url, f"/meeting/{session_id}", token, method="DELETE") == {"reset": True}


async def run_multilingual(base_url: str, token: str, marker: str) -> None:
    await run_scenario(base_url=base_url, token=token, lang="en", marker=marker)
    await run_scenario(base_url=base_url, token=token, lang="ja", marker=marker)
    await run_scenario(base_url=base_url, token=token, lang="ko", marker=marker)


def wait_for_compatibility(
    process: subprocess.Popen[bytes], base_url: str, token: str, timeout: float
) -> int:
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"packaged backend exited before readiness with code {process.returncode}"
            )
        try:
            compatibility = http_json(base_url, "/health/compatibility", token, timeout=1.0)
            assert compatibility == {
                "status": "ok",
                "product": "meeting-intelligence-copilot",
                "api_version": 13,
                "backend_version": "0.6.1",
                "capability_auth": True,
            }
            return round((time.monotonic() - started) * 1000)
        except (AssertionError, OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.2)
    raise TimeoutError("packaged backend compatibility health did not become ready")


def assert_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))


def assert_listener_stopped(port: int) -> None:
    for _ in range(25):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.2)
    raise RuntimeError("packaged backend listener remained after process-tree shutdown")


def inspect_logs(paths: list[Path], *, token: str, marker: str, max_bytes: int) -> None:
    for path in paths:
        size = path.stat().st_size if path.exists() else 0
        assert size <= max_bytes, f"operational log exceeded {max_bytes} bytes: {path}"
        content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        assert marker not in content
        assert token not in content


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def assert_context_cache_encrypted(database: Path, marker: str) -> None:
    cache_files = list(database.parent.glob(f"{database.name}*"))
    assert cache_files, "packaged context acceptance did not create its SQLite cache"
    encoded_marker = marker.encode("utf-8")
    for path in cache_files:
        assert encoded_marker not in path.read_bytes(), (
            f"packaged context cache exposed the controlled plaintext marker: {path.name}"
        )


def run_context_acceptance(
    *, base_url: str, token: str, marker: str, cache_root: Path
) -> None:
    knowledge_root = cache_root / "knowledge"
    knowledge_root.mkdir(parents=True)
    (knowledge_root / "inventory-policy.md").write_text(
        f"SAP is the inventory authority. {marker}", encoding="utf-8"
    )
    local_definition = {
        "connector_id": "packaged-local",
        "kind": "local_files",
        "display_name": "Packaged local knowledge",
        "principal_id": "caller-controlled-and-ignored",
        "root_path": str(knowledge_root),
    }
    configured = http_json(
        base_url,
        "/context/connectors",
        token,
        method="POST",
        payload={"definition": local_definition},
    )
    assert configured["phase"] == "ready"
    assert configured["auth_enabled"] is True
    synced = http_json(
        base_url,
        "/context/connectors/packaged-local/sync",
        token,
        method="POST",
    )
    assert synced == {"connector_id": "packaged-local", "indexed_documents": 1}
    result = http_json(
        base_url,
        "/context/search",
        token,
        method="POST",
        payload={
            "text": marker,
            "principal_id": "caller-controlled-and-ignored",
            "limit": 4,
        },
    )
    assert len(result["citations"]) == 1
    assert result["citations"][0]["source_label"] == "inventory-policy.md"
    assert marker in result["citations"][0]["excerpt"]

    context_database = cache_root / "context.sqlite3"
    assert_context_cache_encrypted(context_database, marker)

    # This creates and removes a real current-user Credential Manager entry.
    # Configuration does not contact the endpoint and must remain degraded
    # until the user explicitly runs the remote connection check.
    erp_definition = {
        "connector_id": "packaged-erp",
        "kind": "odata",
        "display_name": "Packaged ERP credential check",
        "principal_id": "caller-controlled-and-ignored",
        "base_url": "https://erp.invalid/odata",
        "entities": {"Orders": ["OrderId"]},
    }
    erp = http_json(
        base_url,
        "/context/connectors",
        token,
        method="POST",
        payload={"definition": erp_definition, "credential": marker},
    )
    assert erp["phase"] == "degraded" and erp["auth_enabled"] is True
    assert erp["detail_code"] == "connection_not_checked"
    assert http_json(
        base_url,
        "/context/connectors/packaged-erp",
        token,
        method="DELETE",
    ) == {"deleted": True}

    session_id = f"meeting-intel-packaged-context-{uuid.uuid4().hex}"
    acknowledgement = http_json(
        base_url,
        f"/ingest/meetily/{session_id}",
        token,
        method="POST",
        payload={
            "lang": "en",
            "payload": {
                "text": "SAP and ERP inventory values do not match.",
                "source": "Audio",
                "sequence_id": 0,
                "is_partial": False,
                "confidence": 0.9,
                "audio_start_time": 0.0,
                "audio_end_time": 3.0,
            },
        },
    )
    assert acknowledgement["status"] == "applied"
    snapshot = asyncio.run(
        read_current_snapshot(
            base_url,
            session_id,
            token,
            expected_version=acknowledgement["state_version"],
        )
    )
    pain_id = snapshot["pain_points"][0]["pain_id"]
    draft = http_json(
        base_url,
        "/issues/drafts",
        token,
        method="POST",
        payload={
            "session_id": session_id,
            "pain_id": pain_id,
            "state_version": snapshot["version"],
            "connector_ids": ["packaged-local"],
            "include_context": True,
        },
    )
    assert draft["draft_id"].startswith("draft-")
    try:
        http_json(
            base_url,
            "/issues/drafts/submit",
            token,
            method="POST",
            payload={},
        )
    except urllib.error.HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError("packaged backend exposed an external action submission route")

    assert http_json(base_url, f"/meeting/{session_id}", token, method="DELETE") == {
        "reset": True
    }

    assert http_json(base_url, "/context", token, method="DELETE") == {"deleted": True}
    assert http_json(base_url, "/context/connectors", token) == []
    assert_context_cache_encrypted(context_database, marker)


async def read_current_snapshot(
    base_url: str,
    session_id: str,
    token: str,
    *,
    expected_version: int,
) -> dict[str, Any]:
    websocket_url = (
        f"ws://127.0.0.1:{base_url.rsplit(':', 1)[1]}"
        f"/ws/meeting/{session_id}"
    )
    async with connect(
        websocket_url,
        origin=ORIGIN,
        subprotocols=[WS_PROTOCOL, f"token.{token}"],
        open_timeout=5,
        close_timeout=2,
    ) as websocket:
        return await receive_snapshot_at_least(websocket, expected_version)


async def receive_snapshot_at_least(
    websocket: Any,
    expected_version: int,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Skip the mandatory v0 reset frame and wait for the catch-up snapshot."""

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(
                f"snapshot version {expected_version} was not received within {timeout}s"
            )
        snapshot = await receive_snapshot(websocket, remaining)
        if snapshot.get("version", -1) >= expected_version:
            return snapshot


def remove_acceptance_tree(path: Path, *, prefix: str, label: str) -> None:
    temp_root = Path(tempfile.gettempdir()).resolve()
    resolved = path.resolve()
    if resolved.parent != temp_root or not resolved.name.startswith(prefix):
        raise RuntimeError(f"refusing to remove an unexpected acceptance-{label} path")
    deadline = time.monotonic() + 5.0
    while True:
        try:
            shutil.rmtree(resolved)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"failed to remove the packaged acceptance {label}"
                ) from None
            time.sleep(0.1)


def run(args: argparse.Namespace) -> int:
    artifact = args.artifact.resolve(strict=True)
    if not artifact.is_file():
        raise ValueError(f"packaged backend artifact is not a file: {artifact}")
    assert_port_available(args.port)

    token = secrets.token_hex(32)
    marker = f"PACKAGED_BILINGUAL_MARKER_{uuid.uuid4().hex}"
    log_root = Path(tempfile.mkdtemp(prefix="meeting-intel-packaged-multilingual-"))
    cache_root = Path(tempfile.mkdtemp(prefix="meeting-intel-packaged-cache-"))
    stdout_path = log_root / "backend.stdout.log"
    stderr_path = log_root / "backend.stderr.log"
    environment = os.environ.copy()
    environment["MEETING_INTELLIGENCE_BACKEND_PORT"] = str(args.port)
    environment["MEETING_INTELLIGENCE_TOKEN"] = token
    environment["MEETING_INTELLIGENCE_CACHE_DIR"] = str(cache_root)
    environment["MEETING_INTELLIGENCE_CONTEXT_DB"] = str(
        cache_root / "context.sqlite3"
    )
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process: subprocess.Popen[bytes] | None = None

    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                [str(artifact)],
                cwd=artifact.parent,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=creation_flags,
            )
            cold_start_ms = wait_for_compatibility(
                process, f"http://127.0.0.1:{args.port}", token, args.startup_timeout
            )
            base_url = f"http://127.0.0.1:{args.port}"
            run_context_acceptance(
                base_url=base_url,
                token=token,
                marker=marker,
                cache_root=cache_root,
            )
            asyncio.run(run_multilingual(base_url, token, marker))
    except Exception:
        print(f"FAILED: diagnostic logs retained at {log_root}")
        raise
    finally:
        if process is not None and process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                )
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        remove_acceptance_tree(
            cache_root,
            prefix="meeting-intel-packaged-cache-",
            label="cache",
        )

    assert_listener_stopped(args.port)
    inspect_logs(
        [stdout_path, stderr_path], token=token, marker=marker, max_bytes=args.max_log_bytes
    )
    print("PASS: packaged English manual-work checkpoints and stale-question retraction")
    print("PASS: packaged Japanese source/impact/owner checkpoints without mojibake")
    print("PASS: packaged Korean source/impact/owner checkpoints without mojibake")
    print("PASS: one ASK NOW, at most two follow-ups, and evidence traceability")
    print("PASS: token-authenticated loopback transport and session cleanup")
    print("PASS: encrypted packaged context cache and Credential Manager cleanup")
    print("PASS: packaged context remains read-only with no external submit route")
    print("PASS: bounded logs omit token/transcript marker and process tree stopped")
    print(f"Artifact: {artifact}")
    print(f"Bytes: {artifact.stat().st_size}")
    print(f"SHA-256: {sha256(artifact)}")
    print(f"Cold start ms: {cold_start_ms}")
    if args.keep_logs:
        print(f"Logs retained for inspection: {log_root}")
    else:
        remove_acceptance_tree(
            log_root,
            prefix="meeting-intel-packaged-multilingual-",
            label="log",
        )
        print("PASS: temporary multilingual acceptance logs removed after inspection")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--startup-timeout", type=float, default=20.0)
    parser.add_argument("--max-log-bytes", type=int, default=1_048_576)
    parser.add_argument("--keep-logs", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not 1 <= args.startup_timeout <= 120:
        parser.error("--startup-timeout must be between 1 and 120 seconds")
    if not 1024 <= args.max_log_bytes <= 10_485_760:
        parser.error("--max-log-bytes must be between 1024 and 10485760")
    return args


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
