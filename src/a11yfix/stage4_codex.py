"""Stage-4 launcher for OpenAI Codex (`codex exec`).

Safety model vs the Claude launcher: Codex gets a workspace-write sandbox
scoped to the document's directory instead of PreToolUse hooks, and the
verification gate runs client-side AFTER the session instead of blocking
Stop. After the session, stage-1 detection is re-run and the error-severity
finding count is compared to a pre-session baseline; a regression triggers
one follow-up via `codex exec resume --last`, and if that still regresses,
the backup is restored and exit code 7 is returned. Edit caps /
identical-write loop detection are NOT ported in v1 — the sandbox plus
verify-restore bounds the blast radius to the one file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from a11yfix.manifest import Severity
from a11yfix.stage4 import LaunchPlan, _hard_rules

DEFAULT_CODEX_MODEL = "gpt-5.5"
SESSION_TIMEOUT = 1800  # 30 min hard cap on one codex session
EXIT_VERIFY_REGRESSION = 7


def _codex_bootstrap(manifest_path: Path, file_path: Path) -> str:
    return (
        "You are remediating Microsoft Office accessibility findings.\n"
        f"Manifest (read it first): {manifest_path}\n"
        f"Target file (the ONLY file you may modify): {file_path}\n\n"
        + _hard_rules()
        + "\nWork through residual_findings in the manifest one at a time. "
        "Apply each fix with officecli, run officecli validation after every "
        "write, and stop when every fixable finding is addressed. Do not edit "
        "any other file."
    )


class CodexLauncher:
    name = "codex"

    def available(self) -> bool:
        return shutil.which("codex") is not None

    def launch(self, plan: LaunchPlan, *, dry_run: bool = False) -> int:
        prompt = _codex_bootstrap(plan.manifest, plan.file)
        model = plan.model if plan.model.startswith("gpt") else DEFAULT_CODEX_MODEL
        cmd = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--color",
            "never",
            "-s",
            "workspace-write",
            "-C",
            str(plan.file.parent),
            "-a",
            "never",
            "-m",
            model,
            prompt,
        ]
        if dry_run:
            print("DRY RUN — would execute:")
            print(" ".join(cmd[:-1]) + " <bootstrap prompt>")
            return 0

        baseline = _error_count(plan.file)
        try:
            session_rc = subprocess.run(cmd, timeout=SESSION_TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            # A timed-out session may have left the file half-modified, so it
            # must still fall through to the verify-restore rail below. The
            # codex returncode is unavailable; report 1 because a timeout is
            # never "success", even if the file happens to verify clean.
            print(f"[stage4-codex] session timed out after {SESSION_TIMEOUT}s; verifying file")
            session_rc = 1

        ok, after = _verify(plan.file, baseline)
        if not ok:
            try:
                subprocess.run(
                    [
                        "codex",
                        "exec",
                        "resume",
                        "--last",
                        "--skip-git-repo-check",
                        "-s",
                        "workspace-write",
                        "-C",
                        str(plan.file.parent),
                        "-a",
                        "never",
                        f"Verification failed: error findings went {baseline} -> {after}. "
                        "Re-check your officecli ops and fix the regression.",
                    ],
                    timeout=SESSION_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                # Same rationale: the second verification below decides
                # whether to restore the backup, not the resume's fate.
                print(f"[stage4-codex] resume timed out after {SESSION_TIMEOUT}s; verifying file")
            ok, after = _verify(plan.file, baseline)
        if not ok:
            if plan.backup and plan.backup.exists():
                shutil.copy2(plan.backup, plan.file)
                print(f"[stage4-codex] regression ({baseline} -> {after}); restored backup")
            else:
                print(
                    f"[stage4-codex] regression ({baseline} -> {after}); "
                    "no backup available, file left as-is"
                )
            return EXIT_VERIFY_REGRESSION
        return session_rc


def _error_count(file: Path) -> int:
    findings = _detect(file)
    return sum(1 for f in findings if f.severity == Severity.ERROR)


def _verify(file: Path, baseline: int) -> tuple[bool, int]:
    after = _error_count(file)
    return (after <= baseline, after)


def _detect(file: Path):
    from a11yfix.cli import detect_findings

    return detect_findings(file)
