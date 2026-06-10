import subprocess

import pytest

from a11yfix.ai.agent_cli import jsonl_events, require_binary, run_cli, temp_image
from a11yfix.ai.errors import AdapterCallError, AdapterUnavailable

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc00000030101009a9c181b0000000049454e44ae426082"
)


def test_require_binary_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: None)
    with pytest.raises(AdapterUnavailable):
        require_binary("pi", hint="npm install -g @earendil-works/pi-coding-agent")


def test_run_cli_nonzero_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "err"))
    with pytest.raises(AdapterCallError):
        run_cli(["x"], timeout=5)


def test_jsonl_skips_garbage():
    events = list(jsonl_events('{"a":1}\nnot json\n{"b":2}\n'))
    assert events == [{"a": 1}, {"b": 2}]


def test_temp_image_roundtrip():
    with temp_image(PNG_1PX) as p:
        assert p.suffix == ".png" and p.read_bytes()[:4] == b"\x89PNG"
    assert not p.exists()
