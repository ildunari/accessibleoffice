import pytest

from a11yfix.stage4 import ClaudeLauncher, get_launcher


def test_claude_launcher_default():
    launcher = get_launcher("claude")
    assert isinstance(launcher, ClaudeLauncher)
    assert launcher.name == "claude"


def test_unknown_launcher():
    with pytest.raises(ValueError):
        get_launcher("bogus")
