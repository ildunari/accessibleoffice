"""Smoke tests for the stage-4 launcher's embedded fallback."""

from __future__ import annotations

import json
from pathlib import Path

from a11yfix import stage4
from a11yfix.manifest import FileFormat, Manifest


def _fake_manifest(tmp_path: Path, fmt: FileFormat) -> Manifest:
    backup = tmp_path / "deck.bak.pptx"
    backup.write_bytes(b"x")
    m = Manifest(
        file_path=str(tmp_path / "deck.pptx"),
        file_backup_path=str(backup),
        file_format=fmt,
    )
    return m


def test_embedded_path_when_no_skill(tmp_path, monkeypatch):
    """force_embedded=True must produce a valid plan with no skill flags."""
    file_path = tmp_path / "deck.pptx"
    file_path.write_bytes(b"x")
    manifest = _fake_manifest(tmp_path, FileFormat.PPTX)

    plan = stage4.build_launch_plan(file_path, manifest, force_embedded=True)
    assert not plan.use_skill
    assert plan.skills == []
    assert "<orchestration>" in plan.bootstrap
    assert "Phase 1" in plan.bootstrap
    assert plan.settings_path is not None and plan.settings_path.exists()

    cmd = stage4.render_launch_command(plan)
    assert "--skill" not in cmd
    assert "--settings" in cmd
    assert "claude" == cmd[0]


def test_skill_path_when_skill_present(tmp_path, monkeypatch):
    file_path = tmp_path / "deck.pptx"
    file_path.write_bytes(b"x")
    manifest = _fake_manifest(tmp_path, FileFormat.PPTX)
    monkeypatch.setattr(stage4, "claude_skill_available", lambda *_a, **_kw: True)

    plan = stage4.build_launch_plan(file_path, manifest)
    assert plan.use_skill
    assert stage4.SKILL_NAME in plan.skills
    assert "officecli-pptx" in plan.skills
    cmd = stage4.render_launch_command(plan)
    assert "--skill" in cmd


def test_hooks_enforce_loop_guard(tmp_path):
    """Three identical write attempts should produce a deny on the third."""
    file_path = tmp_path / "doc.docx"
    file_path.write_bytes(b"x")
    manifest = _fake_manifest(tmp_path, FileFormat.DOCX)
    plan = stage4.build_launch_plan(file_path, manifest, force_embedded=True)

    assert plan.settings_path is not None
    settings = json.loads(plan.settings_path.read_text())
    pre_tool_cmd = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    pre_tool = Path(pre_tool_cmd)
    assert pre_tool.exists() and pre_tool.stat().st_mode & 0o111

    # Run the hook three times with the same Bash call. Third call must deny.
    import subprocess

    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "officecli set foo"}}
    )
    decisions = []
    for _ in range(3):
        out = subprocess.run(
            [str(pre_tool)], input=payload, capture_output=True, text=True, check=False
        )
        decisions.append(json.loads(out.stdout)["permissionDecision"])
    assert decisions[0] == "allow"
    assert decisions[1] == "allow"
    assert decisions[2] == "deny"


def test_skill_probe_is_silent_on_missing_claude(monkeypatch):
    """Probe must never raise when `claude` isn't on PATH."""
    monkeypatch.setattr(stage4, "claude_cli_available", lambda: False)
    assert stage4.claude_skill_available() is False
