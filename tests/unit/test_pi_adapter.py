import subprocess

import pytest

from a11yfix.ai.errors import AdapterCallError
from a11yfix.ai.pi_adapter import PiAdapter

PI_OK = "\n".join([
    '{"type":"agent_start"}',
    '{"type":"message_end","message":{"role":"assistant",'
    '"content":[{"type":"text","text":"A bar chart of Q3 revenue"}],'
    '"usage":{"input":900,"output":12,"cost":{"total":0.0009}}}}',
    '{"type":"agent_end"}',
])

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc00000030101009a9c181b0000000049454e44ae426082"
)


@pytest.fixture
def pi(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: "/usr/local/bin/pi")
    return PiAdapter()


def _fake_run(monkeypatch, stdout, rc=0, capture=None):
    def run(cmd, **kw):
        if capture is not None:
            capture.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout, "")
    monkeypatch.setattr(subprocess, "run", run)


def test_describe_image(pi, monkeypatch):
    cmds: list = []
    _fake_run(monkeypatch, PI_OK, capture=cmds)
    res = pi.describe_image(PNG_1PX, max_chars=125, context="Shape: chart1")
    assert res.text == "A bar chart of Q3 revenue"
    assert res.usage.cost_usd == 0.0009
    cmd = cmds[0]
    assert cmd[:2] == ["pi", "--mode"] and "--no-tools" in cmd
    assert any(str(a).startswith("@") for a in cmd)  # image attached


def test_no_image_flag_for_text_calls(pi, monkeypatch):
    cmds: list = []
    _fake_run(monkeypatch, PI_OK, capture=cmds)
    pi.suggest_link_text(url="https://x.test/a", surrounding_text="see docs")
    assert not any(str(a).startswith("@") for a in cmds[0])


def test_malformed_output_defers(pi, monkeypatch):
    _fake_run(monkeypatch, "garbage\nnot json")
    with pytest.raises(AdapterCallError):
        pi.suggest_slide_title(slide_text="t", slide_layout="l")
