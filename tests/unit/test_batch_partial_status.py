"""Batch 'partial' file status: stages 1-2 succeeded, stage 3 was skipped.

Partial files must be retried on resume (so stage 3 can run once the AI
adapter is available), and a retried file must be counted once — by its
newest progress entry — not once per attempt.
"""

from __future__ import annotations

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
