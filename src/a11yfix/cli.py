"""a11yfix CLI entry point.

Single-file:
    a11yfix <file> [flags]

Folder batch (sequential, per-file write through the same single-file path):
    a11yfix --folder <dir> [flags]
    a11yfix --resume <batch-id>

The folder mode writes batch state under `~/.a11yfix/batches/<id>/`. Subagent
fan-out is orchestrated by the `using-a11yfix` skill — this CLI just provides
the per-file primitive plus a serial folder runner for ad-hoc / non-Claude
Code use.
"""

from __future__ import annotations

import os
import pickle
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import click

from a11yfix.manifest import (
    FileFormat,
    Finding,
    Manifest,
    Severity,
    ValidationResult,
)
from a11yfix.reporting.json_writer import write_manifest
from a11yfix.reporting.terminal import print_report

# -----------------------------------------------------------------------------
# Single-file helpers
# -----------------------------------------------------------------------------


def _detect_format(path: Path) -> FileFormat:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return FileFormat.DOCX
    if suffix == ".pptx":
        return FileFormat.PPTX
    raise click.UsageError(f"Unsupported file type: {suffix} (only .docx and .pptx)")


def _open_doc(path: Path, fmt: FileFormat):
    if fmt == FileFormat.DOCX:
        from a11yfix.ooxml.docx_reader import open_docx

        return open_docx(path)
    from a11yfix.ooxml.pptx_reader import open_pptx

    return open_pptx(path)


def _detect_findings(doc, only_rules: set[str] | None, skip_rules: set[str]) -> list[Finding]:
    from a11yfix import rules  # noqa: F401  (trigger registration)
    from a11yfix.rules.base import rules_for

    findings: list[Finding] = []
    for rule in rules_for(doc.file_format):
        if only_rules and rule.meta.rule_id not in only_rules:
            continue
        if rule.meta.rule_id in skip_rules:
            continue
        try:
            for f in rule.detect(doc):
                findings.append(f)
        except Exception as exc:
            click.echo(f"[warning] rule {rule.meta.rule_id} failed: {exc}", err=True)
    return findings


@dataclass
class FileResult:
    """Outcome of a single-file run, used by both single-file and batch paths."""

    file: Path
    manifest: Manifest | None
    manifest_path: Path | None
    exit_code: int
    error: str | None = None
    elapsed_sec: float = 0.0

    @property
    def stage_2_count(self) -> int:
        return len(self.manifest.stage_2_fixes_applied) if self.manifest else 0

    @property
    def stage_3_count(self) -> int:
        return len(self.manifest.stage_3_fixes_applied) if self.manifest else 0

    @property
    def residual_count(self) -> int:
        return len(self.manifest.residual_findings) if self.manifest else 0


def _process_one_file(
    file: Path,
    *,
    report_only: bool,
    auto_only: bool,
    output: Path | None,
    rules_csv: str | None,
    skip_csv: str | None,
    default_lang: str | None,
    vlm: str,
    remediate: bool,
    remediate_model: str,
    dry_run: bool,
    print_to_terminal: bool = True,
) -> FileResult:
    """Run the single-file pipeline. Returns a FileResult.

    Raises only on totally unrecoverable failure; per-file errors are captured.
    """
    start = time.monotonic()
    try:
        fmt = _detect_format(file)
    except click.UsageError as exc:
        return FileResult(file=file, manifest=None, manifest_path=None, exit_code=4, error=str(exc))

    only = set(rules_csv.split(",")) if rules_csv else None
    skip = set(skip_csv.split(",")) if skip_csv else set()

    try:
        doc = _open_doc(file, fmt)
    except Exception as exc:
        return FileResult(
            file=file,
            manifest=None,
            manifest_path=None,
            exit_code=4,
            error=f"could not open: {exc}",
            elapsed_sec=time.monotonic() - start,
        )

    findings = _detect_findings(doc, only, skip)
    manifest = Manifest(
        file_path=str(file.resolve()),
        file_format=fmt,
        stage_1_findings_total=len(findings),
        residual_findings=list(findings),
        validation=ValidationResult(status="skipped"),
    )

    if report_only:
        manifest_path = output
        if remediate and manifest_path is None:
            manifest_path = file.parent / f"{file.stem}.manifest.json"
        if manifest_path:
            write_manifest(manifest, manifest_path)
        if print_to_terminal:
            print_report(manifest)
        if remediate:
            from a11yfix.stage4 import build_launch_plan, launch

            plan = build_launch_plan(file, manifest, model=remediate_model)
            rc = launch(plan, dry_run=dry_run)
            return FileResult(
                file=file,
                manifest=manifest,
                manifest_path=manifest_path,
                exit_code=rc,
                elapsed_sec=time.monotonic() - start,
            )
        return FileResult(
            file=file,
            manifest=manifest,
            manifest_path=manifest_path,
            exit_code=0,
            elapsed_sec=time.monotonic() - start,
        )

    # Stage 2 — deterministic
    from a11yfix.fixers.deterministic import apply_deterministic_fixes

    det = apply_deterministic_fixes(findings, doc, default_lang=default_lang)
    manifest.stage_2_fixes_applied = det.applied
    manifest.file_backup_path = det.backup_path
    manifest.validation = ValidationResult(
        status=det.validation_status, errors=list(det.validation_errors)
    )
    findings_left: list[Finding] = list(det.deferred)

    if auto_only:
        manifest.residual_findings = findings_left
        manifest_path = output
        if remediate and manifest_path is None:
            manifest_path = file.parent / f"{file.stem}.manifest.json"
        if manifest_path:
            write_manifest(manifest, manifest_path)
        if print_to_terminal:
            print_report(manifest)
        if remediate:
            from a11yfix.stage4 import build_launch_plan, launch

            plan = build_launch_plan(file, manifest, model=remediate_model)
            rc = launch(plan, dry_run=dry_run)
            return FileResult(
                file=file,
                manifest=manifest,
                manifest_path=manifest_path,
                exit_code=rc,
                elapsed_sec=time.monotonic() - start,
            )
        return FileResult(
            file=file,
            manifest=manifest,
            manifest_path=manifest_path,
            exit_code=0,
            elapsed_sec=time.monotonic() - start,
        )

    # Stage 3 — single-shot AI
    from a11yfix.fixers.single_shot import apply_single_shot_fixes

    try:
        if vlm == "claude":
            from a11yfix.ai.agent_sdk_adapter import ClaudeAgentSDKAdapter

            adapter = ClaudeAgentSDKAdapter()
        elif vlm == "claude-api":
            from a11yfix.ai.claude_adapter import ClaudeAdapter

            adapter = ClaudeAdapter()
        else:
            return FileResult(
                file=file,
                manifest=manifest,
                manifest_path=None,
                exit_code=4,
                error=f"vlm={vlm} not implemented",
                elapsed_sec=time.monotonic() - start,
            )
    except RuntimeError as exc:
        click.echo(f"[warning] AI adapter unavailable: {exc} — skipping stage 3", err=True)
        manifest.residual_findings = findings_left
        if output:
            write_manifest(manifest, output)
        if print_to_terminal:
            print_report(manifest)
        return FileResult(
            file=file,
            manifest=manifest,
            manifest_path=output,
            exit_code=0,
            error=f"adapter unavailable: {exc}",
            elapsed_sec=time.monotonic() - start,
        )

    cap = None
    cap_env = os.environ.get("A11YFIX_MAX_COST_TOTAL_USD")
    if cap_env:
        try:
            cap = float(cap_env)
        except ValueError:
            cap = None
    ss = apply_single_shot_fixes(findings_left, doc, adapter, max_cost_total_usd=cap)
    manifest.stage_3_fixes_applied = ss.applied
    manifest.residual_findings = ss.deferred

    manifest_path = output
    if remediate and manifest_path is None:
        manifest_path = file.parent / f"{file.stem}.manifest.json"
    if manifest_path:
        write_manifest(manifest, manifest_path)
    if print_to_terminal:
        print_report(manifest)

    if remediate:
        from a11yfix.stage4 import build_launch_plan, launch

        plan = build_launch_plan(file, manifest, model=remediate_model)
        rc = launch(plan, dry_run=dry_run)
        return FileResult(
            file=file,
            manifest=manifest,
            manifest_path=manifest_path,
            exit_code=rc,
            elapsed_sec=time.monotonic() - start,
        )

    return FileResult(
        file=file,
        manifest=manifest,
        manifest_path=manifest_path,
        exit_code=0,
        elapsed_sec=time.monotonic() - start,
    )


def _strict_exit(manifest: Manifest, strict: bool, strict_warnings: bool) -> int:
    if manifest.validation.status == "errors":
        return 3
    if strict and any(f.severity == Severity.ERROR for f in manifest.residual_findings):
        return 1
    if strict_warnings and any(f.severity == Severity.WARNING for f in manifest.residual_findings):
        return 2
    return 0


# -----------------------------------------------------------------------------
# Batch helpers (folder + resume)
# -----------------------------------------------------------------------------


# Default per-file wall-clock timeout for batch runs. Any file that takes
# longer is killed and marked failed so it can't tank an entire shard.
PER_FILE_TIMEOUT_SEC = 180


def _process_one_file_worker(kwargs: dict[str, object], result_path: str) -> None:
    """Multiprocessing target for killable per-file batch execution."""
    try:
        result = _process_one_file(**cast(Any, kwargs))
        payload = ("ok", result)
    except BaseException as exc:  # pragma: no cover - defensive worker boundary
        payload = ("err", f"{type(exc).__name__}: {exc}")
    with Path(result_path).open("wb") as f:
        pickle.dump(payload, f)


def _run_batch(
    state_dir: Path,
    *,
    mode: str,
    auto_only: bool,
    report_only: bool,
    rules_csv: str | None,
    skip_csv: str | None,
    default_lang: str | None,
    vlm: str,
    max_cost_total_usd: float | None,
    per_file_timeout: int = PER_FILE_TIMEOUT_SEC,
) -> int:
    """Serial batch runner for `--folder` and `--resume`.

    Walks every shard, processes its still-pending files, writes manifests
    next to each source file, and updates progress.jsonl after every file.
    The skill-orchestrated path uses subagents instead — but having this
    serial path means CLI-only users (and resume) can drive a batch end-to-end.
    """
    from a11yfix.batch import (
        BatchState,
        record_progress,
        set_shard_status,
        shard_pending_files,
        write_rollup,
    )
    from a11yfix.cost_meter import CostMeter

    state = BatchState.load(state_dir)
    # Tell stage 3 (and adapter) to record cost into this batch.
    os.environ["A11YFIX_STATE_DIR"] = str(state.state_dir)
    meter = CostMeter(state.state_dir)
    effective_cap = (
        max_cost_total_usd if max_cost_total_usd is not None else state.max_cost_total_usd
    )
    if effective_cap is not None:
        os.environ["A11YFIX_MAX_COST_TOTAL_USD"] = str(effective_cap)

    if mode == "scan":
        report_only = True
        auto_only = False
    elif mode == "auto":
        auto_only = True
        report_only = False
    elif mode == "full-dry":
        report_only = False
        auto_only = False

    cap = max_cost_total_usd if max_cost_total_usd is not None else state.max_cost_total_usd

    click.echo(
        f"[batch] {state.batch_id}  mode={mode}  shards={len(state.shards)}  "
        f"cap={('$' + format(cap, '.2f')) if cap else 'none'}"
    )

    grand_done = 0
    grand_failed = 0
    for shard in state.shards:
        pending = shard_pending_files(state_dir, shard.id)
        if not pending:
            set_shard_status(state_dir, shard.id, "done")
            continue
        set_shard_status(state_dir, shard.id, "running")
        click.echo(f"[batch] {shard.id}: {len(pending)} files")
        for raw in pending:
            f = Path(raw)
            # Effective stage-3 toggle: if cap reached, force auto_only for this file.
            local_auto_only = auto_only
            if cap is not None and meter.would_exceed(cap, additional=0.0):
                if not auto_only:
                    click.echo(
                        f"[batch] cost cap reached; falling back to auto for {f.name}",
                        err=True,
                    )
                local_auto_only = True

            cost_before = meter.total()
            manifest_out = f.parent / f"{f.stem}.manifest.json"
            import multiprocessing

            ctx = multiprocessing.get_context()
            fd, result_tmp = tempfile.mkstemp(
                prefix="a11yfix-worker-", suffix=".pkl", dir=str(state_dir)
            )
            os.close(fd)
            worker_kwargs = {
                "file": f,
                "report_only": report_only,
                "auto_only": local_auto_only,
                "output": manifest_out,
                "rules_csv": rules_csv,
                "skip_csv": skip_csv,
                "default_lang": default_lang,
                "vlm": vlm,
                "remediate": False,  # never spawn interactive in batch
                "remediate_model": "claude-sonnet-4-6",
                "dry_run": True,
                "print_to_terminal": False,
            }
            try:
                proc = ctx.Process(target=_process_one_file_worker, args=(worker_kwargs, result_tmp))
                proc.start()
                proc.join(per_file_timeout)
                if proc.is_alive():
                    proc.terminate()
                    proc.join(5)
                    if proc.is_alive() and hasattr(proc, "kill"):
                        proc.kill()
                        proc.join(2)
                    record_progress(
                        state_dir,
                        shard.id,
                        file=str(f),
                        status="failed",
                        error=f"timeout after {per_file_timeout}s",
                        cost_usd=meter.total() - cost_before,
                    )
                    grand_failed += 1
                    click.echo(
                        f"  [fail] {f.name}: timeout after {per_file_timeout}s",
                        err=True,
                    )
                    continue
                try:
                    with Path(result_tmp).open("rb") as rf:
                        status, payload = pickle.load(rf)
                except (EOFError, OSError, pickle.PickleError):
                    status, payload = (
                        "err",
                        f"worker exited {proc.exitcode} without returning a result",
                    )
                if status == "err":
                    record_progress(
                        state_dir,
                        shard.id,
                        file=str(f),
                        status="failed",
                        error=str(payload),
                        cost_usd=meter.total() - cost_before,
                    )
                    grand_failed += 1
                    click.echo(f"  [fail] {f.name}: {payload}", err=True)
                    continue
                result = cast(FileResult, payload)
            finally:
                Path(result_tmp).unlink(missing_ok=True)

            cost_delta = meter.total() - cost_before
            if result.error and result.manifest is None:
                record_progress(
                    state_dir,
                    shard.id,
                    file=str(f),
                    status="failed",
                    error=result.error,
                    cost_usd=cost_delta,
                )
                grand_failed += 1
                click.echo(f"  [fail] {f.name}: {result.error}", err=True)
                continue

            record_progress(
                state_dir,
                shard.id,
                file=str(f),
                status="done",
                manifest=str(result.manifest_path) if result.manifest_path else None,
                stage_2=result.stage_2_count,
                stage_3=result.stage_3_count,
                residual=result.residual_count,
                cost_usd=cost_delta,
            )
            grand_done += 1
            click.echo(
                f"  [ok ] {f.name}  s2={result.stage_2_count} "
                f"s3={result.stage_3_count} residual={result.residual_count} "
                f"({result.elapsed_sec:.1f}s)"
            )
        set_shard_status(state_dir, shard.id, "done")

    rollup = write_rollup(state_dir)
    click.echo("")
    click.echo(
        f"[batch] done  files={rollup.files_total}  done={rollup.files_done}  "
        f"failed={rollup.files_failed}  fixes(s2)={rollup.fixes_stage_2}  "
        f"fixes(s3)={rollup.fixes_stage_3}  residual={rollup.residual_total}  "
        f"cost=${rollup.cost_usd:.4f}"
    )
    click.echo(f"[batch] state: {state_dir}")
    return 0 if rollup.files_failed == 0 else 5


def _resolve_batch_id(batch_id: str) -> Path:
    """Resolve `<id>` to a state dir under DEFAULT_BATCHES_ROOT."""
    from a11yfix.batch import DEFAULT_BATCHES_ROOT

    direct = DEFAULT_BATCHES_ROOT / batch_id
    if direct.exists():
        return direct
    # Allow prefix match if unambiguous.
    if DEFAULT_BATCHES_ROOT.exists():
        matches = [d for d in DEFAULT_BATCHES_ROOT.iterdir() if d.name.startswith(batch_id)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise click.UsageError(
                f"batch id '{batch_id}' is ambiguous: {[m.name for m in matches]}"
            )
    # Maybe an absolute state-dir path.
    p = Path(batch_id).expanduser()
    if p.is_dir() and (p / "state.json").exists():
        return p
    raise click.UsageError(f"unknown batch: {batch_id}")


# -----------------------------------------------------------------------------
# CLI definition
# -----------------------------------------------------------------------------


@click.command()
@click.argument(
    "file",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    required=False,
)
@click.option("--report-only", is_flag=True, help="Stage 1 only; no writes.")
@click.option("--auto-only", "--no-ai", "auto_only", is_flag=True, help="Skip stage 3 (AI).")
@click.option(
    "--output", "-o", type=click.Path(path_type=Path), default=None, help="Write manifest JSON."
)
@click.option("--strict", is_flag=True, help="Non-zero exit if any Error severity remains.")
@click.option("--strict-warnings", is_flag=True, help="Non-zero exit if any Warning remains.")
@click.option(
    "--rules", "rules_csv", default=None, help="Comma-separated rule IDs to run (allowlist)."
)
@click.option("--skip-rules", "skip_csv", default=None, help="Comma-separated rule IDs to skip.")
@click.option("--max-ai-cost-usd", type=float, default=0.50, show_default=True)
@click.option(
    "--default-lang", default=None, help="If provided, set document language deterministically."
)
@click.option(
    "--vlm",
    type=click.Choice(["claude", "claude-api", "openai"]),
    default="claude",
    show_default=True,
    help=(
        "claude = Claude Code OAuth via claude-agent-sdk (no API key). "
        "claude-api = Anthropic SDK (requires ANTHROPIC_API_KEY, supports vision). "
        "openai = not implemented in v0.1."
    ),
)
@click.option(
    "--remediate",
    is_flag=True,
    help="After stages 1-3, launch Claude Code (Sonnet 4.6 + thinking) on residual findings via the fixing-office-accessibility skill.",
)
@click.option(
    "--remediate-model",
    default="claude-sonnet-4-6",
    show_default=True,
    help="Model for the stage-4 session.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="With --remediate: print the launch command, don't execute.",
)
@click.option(
    "--mode",
    type=click.Choice(["scan", "auto", "full"]),
    default=None,
    help=(
        "Preset: scan = detect only, no writes. "
        "auto = fully deterministic (stages 1+2, no AI, no agentic). "
        "full = detect + deterministic + AI + agentic Claude Code remediation. "
        "Default if unset: auto."
    ),
)
# ---- batch flags ----
@click.option(
    "--folder",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Process every .docx/.pptx in this folder as a batch.",
)
@click.option(
    "--state-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to write batch state. Default: ~/.a11yfix/batches/<auto-id>.",
)
@click.option(
    "--shard-size",
    type=int,
    default=10,
    show_default=True,
    help="Files per shard (used by skill-driven subagent fan-out).",
)
@click.option(
    "--max-concurrent",
    type=int,
    default=8,
    show_default=True,
    help="Max parallel shards (subagent fan-out only; CLI itself runs serial).",
)
@click.option(
    "--max-cost-total-usd",
    type=float,
    default=None,
    help="Cumulative cost cap across the batch. Stage 3 falls back to auto when reached.",
)
@click.option(
    "--resume",
    "resume_id",
    default=None,
    help="Resume an interrupted batch by id (or prefix), or absolute state-dir path.",
)
def main(
    file: Path | None,
    report_only: bool,
    auto_only: bool,
    output: Path | None,
    strict: bool,
    strict_warnings: bool,
    rules_csv: str | None,
    skip_csv: str | None,
    max_ai_cost_usd: float,
    default_lang: str | None,
    vlm: str,
    remediate: bool,
    remediate_model: str,
    dry_run: bool,
    mode: str | None,
    folder: Path | None,
    state_dir: Path | None,
    shard_size: int,
    max_concurrent: int,
    max_cost_total_usd: float | None,
    resume_id: str | None,
) -> None:
    """Detect and fix accessibility issues in .docx and .pptx files."""

    # ---- Mode preset (still allow granular flags to override) ----
    if mode is None and not (report_only or auto_only or remediate):
        mode = "auto"
    if mode == "scan":
        report_only = True
    elif mode == "auto":
        auto_only = True
    elif mode == "full":
        remediate = True

    # ---- Routing ----
    is_batch = bool(folder) or bool(resume_id)
    if is_batch and file is not None:
        raise click.UsageError("FILE and --folder/--resume are mutually exclusive.")
    if not is_batch and file is None:
        raise click.UsageError("Pass FILE, or --folder DIR, or --resume BATCH_ID.")

    if is_batch:
        # Map cli --mode to batch run mode (no interactive stage 4 in batch).
        if remediate and not dry_run:
            click.echo(
                "[batch] --remediate without --dry-run is not allowed in batch; "
                "stage 4 must be driven one file at a time after the batch.",
                err=True,
            )
            sys.exit(4)
        batch_mode = "scan" if report_only else ("auto" if auto_only else "full-dry")

        if resume_id:
            sd = _resolve_batch_id(resume_id)
            click.echo(f"[batch] resuming {sd.name}")
        else:
            from a11yfix.batch import (
                create_batch,
                dedupe_and_validate,
                discover_files,
            )

            files = discover_files(folder)
            kept, skipped = dedupe_and_validate(files)
            for s in skipped:
                click.echo(f"[skip] {s}", err=True)
            if not kept:
                click.echo(f"[batch] no .docx/.pptx files in {folder}", err=True)
                sys.exit(4)

            state = create_batch(
                files=kept,
                state_dir=state_dir,
                mode=batch_mode,
                model="claude-sonnet-4-6",
                shard_size=shard_size,
                max_concurrent=max_concurrent,
                source_root=str(folder.expanduser().resolve()),
                max_cost_total_usd=max_cost_total_usd,
            )
            sd = Path(state.state_dir)
            click.echo(
                f"[batch] {state.batch_id}  files={len(kept)}  "
                f"shards={len(state.shards)}  state={sd}"
            )

        rc = _run_batch(
            sd,
            mode=batch_mode,
            auto_only=auto_only,
            report_only=report_only,
            rules_csv=rules_csv,
            skip_csv=skip_csv,
            default_lang=default_lang,
            vlm=vlm,
            max_cost_total_usd=max_cost_total_usd,
        )
        sys.exit(rc)

    # ---- Single-file path ----
    assert file is not None
    if not file.exists():
        raise click.UsageError(f"file not found: {file}")
    result = _process_one_file(
        file,
        report_only=report_only,
        auto_only=auto_only,
        output=output,
        rules_csv=rules_csv,
        skip_csv=skip_csv,
        default_lang=default_lang,
        vlm=vlm,
        remediate=remediate,
        remediate_model=remediate_model,
        dry_run=dry_run,
        print_to_terminal=True,
    )
    if result.manifest is None:
        click.echo(f"[error] {result.error}", err=True)
        sys.exit(result.exit_code)
    if result.exit_code != 0:
        sys.exit(result.exit_code)
    sys.exit(_strict_exit(result.manifest, strict, strict_warnings))


if __name__ == "__main__":
    main()
