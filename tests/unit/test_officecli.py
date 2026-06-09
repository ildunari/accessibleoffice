"""Unit tests for the officecli subprocess wrapper."""

from __future__ import annotations

import subprocess

from a11yfix.ooxml import officecli
from a11yfix.rules.base import OfficecliOp


def test_batch_requires_parseable_result_for_each_op(tmp_path, monkeypatch):
    doc = tmp_path / "deck.pptx"
    doc.write_bytes(b"x")

    def fake_run(args, *, check=True):
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    monkeypatch.setattr(officecli, "_run", fake_run)
    result = officecli.OfficecliClient(doc).batch(
        [OfficecliOp(verb="set", path="/slide[1]/picture[@id=1]", props={"alt": "x"})]
    )

    assert result.success is False
    assert result.per_op == []


def test_batch_fails_when_result_count_does_not_match_ops(tmp_path, monkeypatch):
    doc = tmp_path / "deck.pptx"
    doc.write_bytes(b"x")

    def fake_run(args, *, check=True):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='{"results":[{"success":true}]}',
            stderr="",
        )

    monkeypatch.setattr(officecli, "_run", fake_run)
    result = officecli.OfficecliClient(doc).batch(
        [
            OfficecliOp(verb="set", path="/slide[1]/picture[@id=1]", props={"alt": "x"}),
            OfficecliOp(verb="set", path="/slide[1]/picture[@id=2]", props={"alt": "y"}),
        ]
    )

    assert result.success is False
    assert len(result.per_op) == 1


def test_validate_nonzero_without_json_errors_is_error(tmp_path, monkeypatch):
    doc = tmp_path / "deck.pptx"
    doc.write_bytes(b"x")

    def fake_run(args, *, check=True):
        return subprocess.CompletedProcess(args, 2, stdout="", stderr="bad file")

    monkeypatch.setattr(officecli, "_run", fake_run)
    result = officecli.OfficecliClient(doc).validate()

    assert result.status == "errors"
    assert result.errors == [{"raw": "bad file"}]
