"""Batch 'partial' file status: stages 1-2 succeeded, stage 3 was skipped.

Partial files must be retried on resume (so stage 3 can run once the AI
adapter is available), and a retried file must be counted once — by its
newest progress entry — not once per attempt.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from a11yfix.batch import (
    aggregate_rollup,
    create_batch,
    latest_progress_by_file,
    record_progress,
    shard_completed_files,
    shard_pending_files,
)


def _batch(tmp_path, files):
    paths = []
    for name in files:
        p = tmp_path / name
        p.write_bytes(b"stub")
        paths.append(p)
    return create_batch(files=paths, state_dir=tmp_path / "state", mode="full-dry")


def test_partial_file_is_retried_on_resume(tmp_path):
    state = _batch(tmp_path, ["a.pptx", "b.pptx"])
    sid = state.shards[0].id
    f_a, f_b = str(tmp_path / "a.pptx"), str(tmp_path / "b.pptx")

    record_progress(
        state.state_dir, sid, file=f_a, status="partial",
        manifest="m.json", stage_2=3, error="adapter unavailable: claude not found",
    )
    record_progress(state.state_dir, sid, file=f_b, status="done", manifest="m2.json")

    assert f_a not in shard_completed_files(state.state_dir, sid)
    assert f_b in shard_completed_files(state.state_dir, sid)
    assert shard_pending_files(state.state_dir, sid) == [f_a]


def test_retried_file_counts_once_by_newest_status(tmp_path):
    state = _batch(tmp_path, ["a.pptx"])
    sid = state.shards[0].id
    f_a = str(tmp_path / "a.pptx")

    record_progress(
        state.state_dir, sid, file=f_a, status="partial",
        error="adapter unavailable", cost_usd=0.10,
    )
    # Resume retried the file and stage 3 succeeded this time.
    record_progress(state.state_dir, sid, file=f_a, status="done", cost_usd=0.25)

    latest = latest_progress_by_file(state.state_dir, sid)
    assert latest[f_a]["status"] == "done"

    rollup = aggregate_rollup(state.state_dir)
    assert rollup.files_total == 1, "retried file must not double-count"
    assert rollup.files_done == 1
    assert rollup.files_partial == 0
    assert rollup.files_failed == 0
    # Cost was spent on every attempt, so it sums over all entries.
    assert abs(rollup.cost_usd - 0.35) < 1e-9


def test_rollup_counts_partial_files(tmp_path):
    state = _batch(tmp_path, ["a.pptx", "b.pptx"])
    sid = state.shards[0].id

    record_progress(
        state.state_dir, sid, file=str(tmp_path / "a.pptx"),
        status="partial", error="adapter unavailable",
    )
    record_progress(
        state.state_dir, sid, file=str(tmp_path / "b.pptx"),
        status="failed", error="boom",
    )

    rollup = aggregate_rollup(state.state_dir)
    assert rollup.files_partial == 1
    assert rollup.files_failed == 1
    assert rollup.files_done == 0
    # Partial files are not failures: no error entry for them.
    assert [e["file"] for e in rollup.errors] == [str(tmp_path / "b.pptx")]


@pytest.mark.parametrize("backend", ["pi", "opencode", "codex"])
def test_missing_agent_cli_binary_yields_partial_shape(
    backend: str, docx_no_title: Path, tmp_path, monkeypatch
) -> None:
    """--vlm pi/opencode/codex with the binary missing skips stage 3, not the file.

    The per-file pipeline must return the exact FileResult shape _run_batch
    classifies as 'partial' (exit_code == 0, error set, manifest_path set):
    stages 1-2 ran and wrote a manifest; stage 3 was skipped because
    create_adapter raised AdapterUnavailable for the missing binary.
    """
    from a11yfix.cli import _process_one_file

    # Stage 2 mutates the file in place — work on a copy, not the session fixture.
    f = tmp_path / "doc.docx"
    shutil.copy(docx_no_title, f)
    out = tmp_path / "doc.manifest.json"

    monkeypatch.setattr("shutil.which", lambda *a, **k: None)

    result = _process_one_file(
        f,
        report_only=False,
        auto_only=False,
        output=out,
        rules_csv=None,
        skip_csv=None,
        default_lang=None,
        vlm=backend,
        vlm_model=None,
        remediate=False,
        remediate_model="claude-sonnet-4-6",
        dry_run=False,
        print_to_terminal=False,
    )

    # The partial predicate in _run_batch: exit_code == 0 AND error AND manifest_path.
    assert result.exit_code == 0
    assert result.error is not None and "adapter unavailable" in result.error
    assert result.manifest_path == out
    assert out.exists(), "stages 1-2 must still write the manifest"
    assert result.manifest is not None
    assert result.manifest.stage_1_findings_total >= 1, "stage 1 must have run"


def test_batch_run_records_partial_when_vlm_binary_missing(
    docx_no_title: Path, tmp_path, monkeypatch
) -> None:
    """End-to-end: a batch run with --vlm pi and no `pi` on PATH lands partial.

    Drives the real _run_batch (spawned worker subprocess included) — this is
    the regression guard that --vlm actually reaches the per-file pipeline in
    batch mode. The worker inherits the environment, so stripping PATH makes
    the binary genuinely missing inside the child process.
    """
    from a11yfix.cli import _run_batch

    f = tmp_path / "doc.docx"
    shutil.copy(docx_no_title, f)
    state = create_batch(files=[f], state_dir=tmp_path / "state", mode="full-dry")

    # _run_batch sets these in os.environ; register them with monkeypatch so
    # they are removed again after the test.
    monkeypatch.delenv("A11YFIX_STATE_DIR", raising=False)
    monkeypatch.delenv("A11YFIX_MAX_COST_TOTAL_USD", raising=False)
    # Spawn uses sys.executable directly, so an empty PATH only hides `pi`.
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    rc = _run_batch(
        Path(state.state_dir),
        mode="full-dry",
        auto_only=False,
        report_only=False,
        rules_csv=None,
        skip_csv=None,
        default_lang=None,
        vlm="pi",
        vlm_model=None,
        max_cost_total_usd=None,
        per_file_timeout=120,
    )

    latest = latest_progress_by_file(state.state_dir, state.shards[0].id)
    entry = latest[str(f)]
    assert entry["status"] == "partial", entry
    assert "adapter unavailable" in (entry.get("error") or "")
    rollup = aggregate_rollup(state.state_dir)
    assert rollup.files_partial == 1
    assert rollup.files_failed == 0
    # Partial counts as not-success at the process level.
    assert rc == 5
