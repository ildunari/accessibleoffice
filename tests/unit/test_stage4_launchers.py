from pathlib import Path

import pytest

import a11yfix.stage4
from a11yfix.stage4 import ClaudeLauncher, LaunchPlan, get_launcher


def test_claude_launcher_default():
    launcher = get_launcher("claude")
    assert isinstance(launcher, ClaudeLauncher)
    assert launcher.name == "claude"


def test_claude_launcher_delegates_to_stage4_launch(monkeypatch):
    """The default (claude) launcher must route through a11yfix.stage4.launch."""
    calls = []
    monkeypatch.setattr(
        a11yfix.stage4,
        "launch",
        lambda plan, *, dry_run=False: calls.append((plan, dry_run)) or 0,
    )
    plan = LaunchPlan(
        file=Path("deck.pptx"),
        manifest=Path("deck.manifest.json"),
        model="m",
        subagent_model="s",
        grunt_model="g",
        skills=[],
        bootstrap="",
        backup=None,
        use_skill=False,
        settings_path=None,
    )
    assert ClaudeLauncher().launch(plan, dry_run=True) == 0
    assert calls == [(plan, True)]


def test_unknown_launcher():
    with pytest.raises(ValueError):
        get_launcher("bogus")
