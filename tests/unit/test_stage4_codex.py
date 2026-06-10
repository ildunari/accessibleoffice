"""Tests for the Codex stage-4 launcher (sandbox flags + verify-restore gate)."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from a11yfix.stage4 import LaunchPlan
from a11yfix.stage4_codex import CodexLauncher, _error_count


def _fake_codex(tmp_path: Path, monkeypatch, rc: int = 0) -> Path:
    log = tmp_path / "codex_calls.log"
    script = tmp_path / "bin" / "codex"
    script.parent.mkdir()
    script.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit {rc}\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}:{os.environ['PATH']}")
    return log


def _plan(tmp_path: Path) -> LaunchPlan:
    f = tmp_path / "deck.pptx"
    f.write_bytes(b"fake")
    b = tmp_path / "deck.backup.pptx"
    b.write_bytes(b"orig")
    m = tmp_path / "deck.manifest.json"
    m.write_text("{}")
    return LaunchPlan(
        file=f,
        manifest=m,
        model="gpt-5.5",
        subagent_model="",
        grunt_model="",
        skills=[],
        bootstrap="",
        backup=b,
        use_skill=False,
        settings_path=None,
    )


def test_available_tracks_codex_on_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert CodexLauncher().available() is False
    _fake_codex(tmp_path, monkeypatch)
    assert CodexLauncher().available() is True


def test_sandbox_and_approval_flags(tmp_path, monkeypatch):
    log = _fake_codex(tmp_path, monkeypatch)
    monkeypatch.setattr("a11yfix.stage4_codex._error_count", lambda f: 0)
    assert CodexLauncher().launch(_plan(tmp_path)) == 0
    args = log.read_text()
    assert "-s workspace-write" in args
    assert "-a never" in args
    assert f"-C {tmp_path}" in args
    assert "resume" not in args  # no follow-up when verification passes


def test_dry_run_executes_nothing(tmp_path, monkeypatch):
    log = _fake_codex(tmp_path, monkeypatch)
    assert CodexLauncher().launch(_plan(tmp_path), dry_run=True) == 0
    assert not log.exists()


def test_regression_restores_backup(tmp_path, monkeypatch):
    log = _fake_codex(tmp_path, monkeypatch)
    counts = iter([2, 5, 5])  # baseline 2, after session 5, after follow-up 5
    monkeypatch.setattr("a11yfix.stage4_codex._error_count", lambda f: next(counts))
    plan = _plan(tmp_path)
    assert CodexLauncher().launch(plan) == 7
    assert plan.file.read_bytes() == b"orig"
    assert "resume --last" in log.read_text()


def test_follow_up_recovers(tmp_path, monkeypatch):
    _fake_codex(tmp_path, monkeypatch)
    counts = iter([2, 5, 1])  # regression, then follow-up fixes it
    monkeypatch.setattr("a11yfix.stage4_codex._error_count", lambda f: next(counts))
    plan = _plan(tmp_path)
    assert CodexLauncher().launch(plan) == 0
    assert plan.file.read_bytes() == b"fake"


def test_session_timeout_restores_backup(tmp_path, monkeypatch):
    # fake codex that outlives the timeout
    script = tmp_path / "bin" / "codex"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nsleep 5\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{script.parent}:{os.environ['PATH']}")
    monkeypatch.setattr("a11yfix.stage4_codex.SESSION_TIMEOUT", 1)
    counts = iter([2, 5, 5])  # baseline, after timeout, after resume (also times out)
    monkeypatch.setattr("a11yfix.stage4_codex._error_count", lambda f: next(counts))
    plan = _plan(tmp_path)
    assert CodexLauncher().launch(plan) == 7
    assert plan.file.read_bytes() == b"orig"


def test_regression_without_backup_prints_diagnostic(tmp_path, monkeypatch, capsys):
    _fake_codex(tmp_path, monkeypatch)
    counts = iter([2, 5, 5])  # baseline 2, regression persists through follow-up
    monkeypatch.setattr("a11yfix.stage4_codex._error_count", lambda f: next(counts))
    plan = _plan(tmp_path)
    plan.backup = None
    assert CodexLauncher().launch(plan) == 7
    assert plan.file.read_bytes() == b"fake"  # not restored
    assert "no backup available, file left as-is" in capsys.readouterr().out


def test_error_count_runs_real_detection(docx_no_title: Path, tmp_path):
    f = tmp_path / "doc.docx"
    shutil.copy(docx_no_title, f)
    count = _error_count(f)
    assert isinstance(count, int)
    assert count >= 0
