import subprocess

import pytest

from a11yfix.ai.codex_adapter import CodexAdapter
from a11yfix.ai.errors import AdapterCallError

CODEX_OK = "\n".join([
    '{"type":"thread.started","thread_id":"t1"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"i1","type":"agent_message",'
    '"text":"A bar chart of Q3 revenue"}}',
    '{"type":"turn.completed","usage":{"input_tokens":27000,'
    '"cached_input_tokens":2000,"output_tokens":12}}',
])

CODEX_FAIL = (
    '{"type":"error","message":"boom"}\n'
    '{"type":"turn.failed","error":{"message":"boom"}}'
)

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc00000030101009a9c181b0000000049454e44ae426082"
)


@pytest.fixture
def codex(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: "/usr/local/bin/codex")
    return CodexAdapter()


def _fake_run(monkeypatch, stdout, rc=0, capture=None):
    def run(cmd, **kw):
        if capture is not None:
            capture.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout, "")
    monkeypatch.setattr(subprocess, "run", run)


def test_describe_image(codex, monkeypatch):
    cmds: list = []
    _fake_run(monkeypatch, CODEX_OK, capture=cmds)
    res = codex.describe_image(PNG_1PX, max_chars=125, context="Shape: chart1")
    assert res.text == "A bar chart of Q3 revenue"
    assert res.usage.input_tokens == 25000  # 27000 minus the 2000 cached subset
    assert res.usage.output_tokens == 12
    assert res.usage.cache_read_tokens == 2000
    assert res.usage.cost_usd is None  # codex exec reports tokens, not dollars
    cmd = cmds[0]
    assert cmd[:4] == ["codex", "exec", "--json", "--ephemeral"]
    # =-attached form: a bare -i is multi-value greedy and would swallow the prompt
    image_args = [a for a in cmd if a.startswith("--image=")]
    assert len(image_args) == 1 and image_args[0].endswith(".png")
    assert "-s" in cmd and "read-only" in cmd
    assert "\n\n" in cmd[-1]  # combined system+user prompt is the last arg


def test_no_image_flag_for_text_calls(codex, monkeypatch):
    cmds: list = []
    _fake_run(monkeypatch, CODEX_OK, capture=cmds)
    codex.suggest_link_text(url="https://x.test/a", surrounding_text="see docs")
    assert not any(a == "-i" or a.startswith("--image") for a in cmds[0])


def test_turn_failed_raises(codex, monkeypatch):
    _fake_run(monkeypatch, CODEX_FAIL)  # rc=0: failure is in the event stream
    with pytest.raises(AdapterCallError, match="boom"):
        codex.suggest_slide_title(slide_text="t", slide_layout="l")


def test_bare_model_id_reported(codex, monkeypatch):
    _fake_run(monkeypatch, CODEX_OK)
    res = codex.suggest_link_text(url="https://x.test/a", surrounding_text="docs")
    # Bare model id (not "codex:...") so CostMeter's estimator pricing lookup
    # hits the gpt-* rows instead of falling back to default rates.
    assert res.model == "gpt-5.4-mini"


def test_malformed_output_defers(codex, monkeypatch):
    _fake_run(monkeypatch, "garbage\nnot json")
    with pytest.raises(AdapterCallError):
        codex.suggest_slide_title(slide_text="t", slide_layout="l")


def test_gpt_pricing_rows():
    from a11yfix.cost_meter import estimate_cost_usd

    assert abs(estimate_cost_usd(model="gpt-5.4-mini", input_tokens=1_000_000) - 0.75) < 1e-9
    assert abs(estimate_cost_usd(model="gpt-5.5", output_tokens=1_000_000) - 30.0) < 1e-9
