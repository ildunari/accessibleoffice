"""Stage-4 launcher — hands the manifest off to a Claude Code session.

Spawns Claude Code with:
  - Sonnet 4.6 (cost-effective vs Opus)
  - Thinking enabled high (via "think hard" cadence in bootstrap prompt)
  - The fixing-office-accessibility skill
  - Per-format companion skills (officecli word/pptx + word-docx-production for docx)
  - The minimum-impact mandate restated in the bootstrap prompt
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from a11yfix.manifest import FileFormat, Manifest

DEFAULT_MODEL = "claude-sonnet-4-6"

# Per-format companion skills. Skipped: pptx-mastery (creation-focused, would
# tempt the model toward redesign — we want minimum-impact only).
DOCX_COMPANION_SKILLS = ["officecli-docx", "word-docx-production"]
PPTX_COMPANION_SKILLS = ["officecli-pptx"]


def _bootstrap_prompt(manifest_path: Path, file_path: Path) -> str:
    return f"""You are running stage 4 of an Office accessibility remediation pipeline.

File: {file_path}
Manifest: {manifest_path}

Use the `fixing-office-accessibility` skill. It contains the full procedure,
references, helper scripts, and JSON schemas.

Hard rules for this session — re-stated for emphasis:
1. Minimum-impact mandate: ADA compliance, NOT redesign. Smallest possible edit.
   Never change colors/fonts/sizes/positions unless the rule REQUIRES it.
2. Trust the manifest. Do not re-detect or re-run upstream auto-fixes.
3. Verify property names with `officecli help <fmt> set <element>` before any
   first-of-its-kind --prop write.
4. Backup before first write; validate after each batch; restore on validate failure.
5. Propagate `_finding_id` through every batch op so report.py can link changes back.
6. When in doubt, ask — do not "improve" beyond compliance.

Think hard about each finding cluster before acting. Start by:

```bash
python {{SKILL_DIR}}/scripts/triage.py {manifest_path} --json
```

Work the grouped plan, not the raw list."""


@dataclass
class LaunchPlan:
    file: Path
    manifest: Path
    model: str
    skills: list[str]
    bootstrap: str
    backup: Path | None


def build_launch_plan(
    file_path: Path,
    manifest: Manifest,
    *,
    model: str = DEFAULT_MODEL,
) -> LaunchPlan:
    companion = (
        DOCX_COMPANION_SKILLS if manifest.file_format == FileFormat.DOCX else PPTX_COMPANION_SKILLS
    )
    skills = ["fixing-office-accessibility", *companion]
    manifest_path = file_path.parent / f"{file_path.stem}.manifest.json"
    backup = Path(manifest.file_backup_path) if manifest.file_backup_path else None
    return LaunchPlan(
        file=file_path,
        manifest=manifest_path,
        model=model,
        skills=skills,
        bootstrap=_bootstrap_prompt(manifest_path, file_path),
        backup=backup,
    )


def claude_cli_available() -> bool:
    return shutil.which("claude") is not None


def render_launch_command(plan: LaunchPlan) -> list[str]:
    """The exact argv we'd hand to subprocess. Used by --dry-run and the actual launcher."""
    cmd = [
        "claude",
        "--model",
        plan.model,
        "--append-system-prompt",
        plan.bootstrap,
    ]
    for s in plan.skills:
        cmd.extend(["--skill", s])
    cmd.append(str(plan.file))
    return cmd


def launch(plan: LaunchPlan, *, dry_run: bool = False) -> int:
    if dry_run:
        cmd = render_launch_command(plan)
        print("Stage-4 launch plan:")
        print(f"  file:      {plan.file}")
        print(f"  manifest:  {plan.manifest}")
        print(f"  model:     {plan.model}  (thinking enabled via bootstrap prompt)")
        print(f"  skills:    {', '.join(plan.skills)}")
        print(f"  backup:    {plan.backup}")
        print()
        print("Command:")
        print("  " + " \\\n  ".join(cmd))
        return 0

    if not claude_cli_available():
        print("[error] Claude Code CLI ('claude') not found in PATH.", flush=True)
        print("        Skipping stage 4. Manifest is at:", plan.manifest, flush=True)
        return 4

    # Hand off to interactive Claude Code session.
    cmd = render_launch_command(plan)
    proc = subprocess.run(cmd, env=os.environ.copy())
    return proc.returncode
