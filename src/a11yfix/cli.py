"""a11yfix CLI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

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
    # Trigger rule registration
    from a11yfix import rules  # noqa: F401
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
        except Exception as exc:  # never let a single rule break the run
            click.echo(f"[warning] rule {rule.meta.rule_id} failed: {exc}", err=True)
    return findings


@click.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
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
@click.option("--vlm", type=click.Choice(["claude", "openai"]), default="claude", show_default=True)
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
def main(
    file: Path,
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
) -> None:
    """Detect and fix accessibility issues in .docx and .pptx files."""
    # Apply --mode preset (still allow granular flags to override).
    if mode is None and not (report_only or auto_only or remediate):
        mode = "auto"  # safe default
    if mode == "scan":
        report_only = True
    elif mode == "auto":
        auto_only = True
    elif mode == "full":
        remediate = True

    fmt = _detect_format(file)
    only = set(rules_csv.split(",")) if rules_csv else None
    skip = set(skip_csv.split(",")) if skip_csv else set()

    try:
        doc = _open_doc(file, fmt)
    except Exception as exc:
        click.echo(f"[error] could not open {file}: {exc}", err=True)
        sys.exit(4)

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
        print_report(manifest)
        if remediate:
            from a11yfix.stage4 import build_launch_plan, launch

            plan = build_launch_plan(file, manifest, model=remediate_model)
            sys.exit(launch(plan, dry_run=dry_run))
        sys.exit(_strict_exit(manifest, strict, strict_warnings))

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
        print_report(manifest)
        if remediate:
            from a11yfix.stage4 import build_launch_plan, launch

            plan = build_launch_plan(file, manifest, model=remediate_model)
            sys.exit(launch(plan, dry_run=dry_run))
        sys.exit(_strict_exit(manifest, strict, strict_warnings))

    # Stage 3 — single-shot AI
    from a11yfix.fixers.single_shot import apply_single_shot_fixes

    try:
        if vlm == "claude":
            from a11yfix.ai.claude_adapter import ClaudeAdapter

            adapter = ClaudeAdapter()
        else:
            click.echo(f"[error] vlm={vlm} not implemented in v0.1; use --auto-only", err=True)
            sys.exit(4)
    except RuntimeError as exc:
        click.echo(f"[warning] AI adapter unavailable: {exc} — skipping stage 3", err=True)
        manifest.residual_findings = findings_left
        if output:
            write_manifest(manifest, output)
        print_report(manifest)
        sys.exit(_strict_exit(manifest, strict, strict_warnings))

    ss = apply_single_shot_fixes(findings_left, doc, adapter)
    manifest.stage_3_fixes_applied = ss.applied
    manifest.residual_findings = ss.deferred

    # If --output not specified but --remediate is, write a default manifest path
    # next to the file so the stage-4 session can find it.
    manifest_path = output
    if remediate and manifest_path is None:
        manifest_path = file.parent / f"{file.stem}.manifest.json"
    if manifest_path:
        write_manifest(manifest, manifest_path)
    print_report(manifest)

    if remediate:
        from a11yfix.stage4 import build_launch_plan, launch

        plan = build_launch_plan(file, manifest, model=remediate_model)
        rc = launch(plan, dry_run=dry_run)
        sys.exit(rc)

    sys.exit(_strict_exit(manifest, strict, strict_warnings))


def _strict_exit(manifest: Manifest, strict: bool, strict_warnings: bool) -> int:
    if manifest.validation.status == "errors":
        return 3
    if strict and any(f.severity == Severity.ERROR for f in manifest.residual_findings):
        return 1
    if strict_warnings and any(f.severity == Severity.WARNING for f in manifest.residual_findings):
        return 2
    return 0


if __name__ == "__main__":
    main()
