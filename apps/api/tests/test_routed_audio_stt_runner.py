"""Provenance tests for the routed-audio STT acceptance runner."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def _runner() -> ModuleType:
    script = Path(__file__).resolve().parents[3] / "scripts" / "run-routed-audio-stt-acceptance.py"
    spec = importlib.util.spec_from_file_location("run_routed_audio_stt_acceptance", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def test_git_commit_requires_clean_tracked_source(tmp_path: Path) -> None:
    runner = _runner()
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "pilot@example.invalid")
    _git(tmp_path, "config", "user.name", "Pilot Test")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("reviewed\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-m", "reviewed")

    commit = runner.git_commit(tmp_path)
    assert len(commit) == 40

    tracked.write_text("modified\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unstaged tracked changes"):
        runner.git_commit(tmp_path)

    _git(tmp_path, "add", "tracked.txt")
    with pytest.raises(RuntimeError, match="staged tracked changes"):
        runner.git_commit(tmp_path)

    tracked.write_text("reviewed\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    (tmp_path / "untracked.txt").write_text("local-only\n", encoding="utf-8")
    assert runner.git_commit(tmp_path) == commit
