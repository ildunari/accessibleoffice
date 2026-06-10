import subprocess

import pytest

from a11yfix.ai.errors import AdapterCallError
from a11yfix.ai.opencode_adapter import OpenCodeAdapter

OPENCODE_OK = "\n".join([
    '{"type":"message.part.updated","part":{"type":"text","text":"A bar chart of Q3 revenue"}}',
    '{"type":"message.updated","info":{"role":"assistant","cost":0.0011,'
    '"tokens":{"input":1500,"output":14,"reasoning":0}}}',
])

OPENCODE_CUMULATIVE = "\n".join([
    '{"type":"message.part.updated","part":{"type":"text","text":"A bar"}}',
    '{"type":"message.part.updated","part":{"type":"text","text":"A bar chart of Q3 revenue"}}',
    '{"type":"message.updated","info":{"role":"assistant","cost":0.0011,'
    '"tokens":{"input":1500,"output":14}}}',
])

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc00000030101009a9c181b0000000049454e44ae426082"
)


@pytest.fixture
def oc(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: "/usr/local/bin/opencode")
    return OpenCodeAdapter()


def _fake_run(monkeypatch, stdout, rc=0, capture=None):
    def run(cmd, **kw):
        if capture is not None:
            capture.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout, "")
    monkeypatch.setattr(subprocess, "run", run)


def test_describe_image(oc, monkeypatch):
    cmds: list = []
    _fake_run(monkeypatch, OPENCODE_OK, capture=cmds)
    res = oc.describe_image(PNG_1PX, max_chars=125, context="Shape: chart1")
    assert res.text == "A bar chart of Q3 revenue"
    assert res.usage.cost_usd == 0.0011
    assert res.usage.input_tokens == 1500
    cmd = cmds[0]
    assert cmd[:4] == ["opencode", "run", "--format", "json"]
    assert "-f" in cmd
    img_arg = cmd[cmd.index("-f") + 1]
    assert img_arg and not img_arg.startswith("-")  # a path follows -f
    prompt = cmd[-1]
    assert "alt text" in prompt  # system fragment prepended
    assert "Shape: chart1" in prompt  # user fragment


def test_no_image_flag_for_text_calls(oc, monkeypatch):
    cmds: list = []
    _fake_run(monkeypatch, OPENCODE_OK, capture=cmds)
    oc.suggest_link_text(url="https://x.test/a", surrounding_text="see docs")
    assert "-f" not in cmds[0]


def test_streamed_cumulative_text_not_duplicated(oc, monkeypatch):
    _fake_run(monkeypatch, OPENCODE_CUMULATIVE)
    res = oc.suggest_slide_title(slide_text="t", slide_layout="l")
    assert res.text == "A bar chart of Q3 revenue"


def test_malformed_output_defers(oc, monkeypatch):
    _fake_run(monkeypatch, "garbage\nnot json")
    with pytest.raises(AdapterCallError):
        oc.suggest_slide_title(slide_text="t", slide_layout="l")
