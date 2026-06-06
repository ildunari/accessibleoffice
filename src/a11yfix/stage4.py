"""Stage-4 launcher — hands the manifest off to a Claude Code session.

Two paths, chosen at runtime:

* **Skill path** (preferred). When the `fixing-office-accessibility` skill is
  installed in the user's Claude Code, we mention it by name in the bootstrap
  prompt and Claude loads the skill files (canonical procedure, helper scripts,
  JSON schemas) on its own.

* **Embedded path** (fallback). When the skill is absent, we ship the same
  procedure inline in the system prompt — a self-contained orchestration spec
  that turns the agent into an Opus orchestrator that fans out to Sonnet/Haiku
  subagents, runs verification gates, and trips out on hooks. The user never
  sees an error: from their seat the two paths are interchangeable.

Hooks (PreToolUse / PostToolUse / Stop) provide three safeguards:
  1. Identical-write loop detection — same officecli call attempted >2x is blocked.
  2. Edit cap — total writes capped per session (default 200).
  3. Verification gate — Stop is blocked until validate-after-write has run.

The hook scripts and a session-scoped Claude settings.json are written next to
the manifest, so they can't leak into the user's global Claude config.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from a11yfix.manifest import FileFormat, Manifest

DEFAULT_MODEL = "claude-opus-4-7"  # orchestrator; subagents use sonnet/haiku
DEFAULT_SUBAGENT_MODEL = "claude-sonnet-4-6"
DEFAULT_GRUNT_MODEL = "claude-haiku-4-5"
DEFAULT_EDIT_CAP = 200

# Per-format companion skills (loaded only on the skill path).
DOCX_COMPANION_SKILLS = ["officecli-docx", "word-docx-production"]
PPTX_COMPANION_SKILLS = ["officecli-pptx"]

SKILL_NAME = "fixing-office-accessibility"


# --------------------------------------------------------------------------
# Skill detection
# --------------------------------------------------------------------------


def claude_cli_available() -> bool:
    return shutil.which("claude") is not None


def claude_skill_available(skill_name: str = SKILL_NAME) -> bool:
    """Probe Claude Code for an installed skill, silently.

    Tries the official `claude skills list` first; falls back to scanning the
    filesystem locations Claude Code reads from. We never raise: a probe
    failure means "skill missing, use fallback" so the user sees nothing.
    """
    if not claude_cli_available():
        return False

    try:
        out = subprocess.run(
            ["claude", "skills", "list"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if out.returncode == 0 and skill_name in out.stdout:
            return True
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass

    candidates = [
        Path.home() / ".claude" / "skills",
        Path.home() / ".config" / "claude" / "skills",
        Path.home() / ".config" / "skillshare" / "skills",
    ]
    for root in candidates:
        if not root.exists():
            continue
        for child in root.rglob("SKILL.md"):
            if skill_name in child.parts or skill_name in child.read_text(errors="ignore")[:600]:
                return True
    return False


# --------------------------------------------------------------------------
# Bootstrap prompts
# --------------------------------------------------------------------------


def _hard_rules() -> str:
    """Rules common to both paths. Motivated, positively-framed, third-person
    where they describe identity. Each rule names the failure it prevents."""
    return """<hard_rules>
1. Minimum-impact mandate. Apply the smallest edit that satisfies the rule.
   Compliance, not redesign. Rationale: every visual change risks breaking
   the author's pedagogical intent and re-running the deck through QA.

2. Trust the manifest. Stages 1-3 already detected and applied deterministic
   fixes. Re-detecting wastes turns and re-running auto-fixes can revert
   manual author intent the upstream stages were careful to preserve.

3. Verify property names before any first-of-its-kind write. Run
   `officecli help <fmt> set <element>` once per element type and cache the
   result for the session. Wrong property names produce silent no-ops that
   look like success.

4. Backup-then-write-then-validate, every batch. Backup is created at session
   start; validate runs after each batch via `officecli validate <file>`; on
   validate failure, restore from backup and stop. This is non-negotiable
   because OOXML errors corrupt files in ways that look fine until Word
   refuses to open them.

5. Propagate `_finding_id` through every operation. Downstream rollup links
   AppliedFix records back to the manifest finding they resolved.

6. When uncertain, surface the question in the trace and continue with the
   next finding. Do not invent values; do not make stylistic improvements
   beyond what the rule requires.
</hard_rules>"""


def _embedded_orchestration_spec() -> str:
    """The full procedure, inlined for sessions where the skill isn't loaded.

    This is a self-contained orchestration playbook. It instructs the
    orchestrator (Opus 4.7 by default) to fan out to subagents, gates progress
    on verification, and names the hooks that enforce loop safety.
    """
    return """<orchestration>
You are operating as an orchestrator. Your role is to plan, delegate, and
verify; the line-by-line edits run in subagents.

**Phase 1 — Triage and group.**
Read the manifest. Group findings by `rule_id`, then by spatial locality
(same slide, same paragraph). Produce a written plan with one batch per
group, smallest groups first. Show the plan, then proceed.

**Phase 2 — Fan-out.**
For each batch:
  - If the batch is mechanical (single property write per finding,
    identical pattern), spawn a haiku subagent with the manifest slice and
    the exact officecli commands to run.
  - If the batch needs judgment (alt text quality, summary phrasing,
    table-header detection), spawn a sonnet subagent with the slice and
    enough context to write good copy.
Use the Task tool. Spawn in parallel when batches are independent.

Each subagent prompt contains: (a) the manifest slice, (b) the hard rules
restated, (c) the exact commands or write contract, (d) the expected output
shape — a JSON record per finding with `_finding_id`, `op`, `result`,
`new_value`. Subagents do not write directly to the document; they emit
intended operations.

**Phase 3 — Verify.**
You collect subagent outputs. For each batch:
  1. Apply intended operations via officecli (one batched write).
  2. Run `officecli validate <file>` to confirm the file still parses.
  3. Run a targeted re-check against the manifest's residual finding ids:
     each one should now resolve, or be explicitly marked "not fixable
     deterministically — needs author judgment".
  4. If validate fails, restore from backup, narrow the batch to a single
     finding, and retry. If retry fails twice, skip the batch and continue
     — flag it in the final report.

**Phase 4 — Self-improvement loop.**
After every 5 verified batches, spawn a sonnet **reviewer** subagent. Hand
it the original manifest, the operations applied, and the residual list.
The reviewer's job is to look for patterns: are we writing the same
property in slightly different ways? Are we missing a class of finding?
Are we introducing noise in the diffs? It writes a one-page critique. You
read it and adjust your plan for the remaining batches.

**Phase 5 — Final report.**
Emit a JSON summary at the end: total findings, fixed-deterministic,
fixed-with-judgment, deferred-to-author, errors. Write it to the manifest
sibling at `<file>.stage4.report.json`. The CLI reads this and exits.

**Validation cadence (non-negotiable):**
- Never apply >1 batch without running `officecli validate`.
- Never claim a finding fixed without re-checking it against the rule.
- Never proceed past a validation failure; the hooks will block you anyway.

**Loop safety:**
- If you find yourself writing the same property to the same target twice,
  stop and re-read the manifest. Either the first write was wrong, or the
  finding was already resolved upstream.
- If a batch produces no change after 3 attempts, defer it to author
  judgment and move on. Three is the cutoff; the hook will hard-stop at
  the same number.
- Total session writes are capped. The hook will block writes past the
  cap. If you hit the cap, write the report with what's done and exit.
</orchestration>"""


def _bootstrap_prompt_skill_path(manifest_path: Path, file_path: Path) -> str:
    """The lean bootstrap used when the skill is loaded — the skill carries
    the playbook so we don't repeat it here."""
    return f"""You are running stage 4 of an Office accessibility remediation pipeline.

File: {file_path}
Manifest: {manifest_path}

Use the `{SKILL_NAME}` skill. It contains the procedure, references, helper
scripts, and JSON schemas. The skill's `scripts/triage.py` is your starting
point.

{_hard_rules()}

Think hard about each finding cluster before acting. Work the grouped plan,
not the raw list."""


def _bootstrap_prompt_embedded(
    manifest_path: Path,
    file_path: Path,
    *,
    subagent_model: str,
    grunt_model: str,
) -> str:
    """The fat bootstrap used when the skill isn't loaded. Carries the full
    procedure inline."""
    return f"""<role>
This assistant is the orchestrator for stage 4 of an Office accessibility
remediation pipeline. It plans the work, delegates edits to subagents, and
verifies every batch before moving on. Stages 1-3 already ran; the manifest
is the input to this session.
</role>

<context>
File: {file_path}
Manifest: {manifest_path}

The pipeline already applied deterministic fixes (header flags, language
tags, simple alt text). Stage 4's job is the residual — findings that
needed human-quality judgment or that the deterministic stage flagged for
review. The author wants minimum-impact compliance, not redesign.

Available subagent models for this session:
- judgment work (alt text, link text quality, table headers): {subagent_model}
- mechanical writes (toggling flags, applying canned values): {grunt_model}
</context>

{_hard_rules()}

{_embedded_orchestration_spec()}

<output_format>
The session should emit, in order:
  1. The grouped plan (markdown, one paragraph per batch).
  2. Subagent dispatch summary per batch.
  3. Validate result per batch.
  4. The final JSON report at `<file>.stage4.report.json` matching:
     {{
       "file": "...",
       "manifest": "...",
       "totals": {{"detected": N, "fixed_deterministic": N,
                   "fixed_with_judgment": N, "deferred": N, "errors": N}},
       "batches": [{{"rule_id": "...", "ops": N, "validate": "ok|fail",
                     "deferred": [...]}}],
       "review_notes": [...]
     }}
</output_format>

Begin with the grouped plan. Do not act before showing it."""


# --------------------------------------------------------------------------
# Hooks: session-scoped settings.json that injects safeguards
# --------------------------------------------------------------------------


def _write_session_hooks(session_dir: Path, edit_cap: int = DEFAULT_EDIT_CAP) -> Path:
    """Write a session-scoped settings.json + hook scripts.

    The hooks run as small Python one-liners that read tool input/output from
    stdin (Claude Code's hook contract) and decide block/allow. Anything we
    log goes to `session_dir/hook.log` so the orchestrator can read it back.
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    state = session_dir / "hook-state.json"
    log = session_dir / "hook.log"
    if not state.exists():
        state.write_text(json.dumps({"writes": 0, "calls": {}, "verifies_since_write": 1}))

    pre_tool = session_dir / "pre_tool.py"
    pre_tool.write_text(
        f"""#!/usr/bin/env python3
import json, sys, pathlib, hashlib, re, shlex
STATE = pathlib.Path({str(state)!r})
LOG   = pathlib.Path({str(log)!r})
CAP   = {edit_cap}
OFFICECLI_MUTATORS = {{'set','add','remove','move','swap','raw-set','batch','save','create','new','merge','import','add-part'}}
SHELL_MUTATORS = {{'cp','mv','rm','rmdir','mkdir','touch','chmod','chown','python','python3','pip','uv','npm','pnpm','yarn','cargo','git','sed','perl'}}
OFFICECLI_MUTATOR_RE = re.compile(
    r'(^|[;&|]\\s*|&&\\s*|\\|\\|\\s*)(?:env\\s+(?:\\S+\\s+)*)?(?:[A-Za-z_][A-Za-z0-9_]*=\\S+\\s+)*officecli\\s+('
    + '|'.join(re.escape(v) for v in sorted(OFFICECLI_MUTATORS, key=len, reverse=True))
    + r')\\b'
)
data = json.loads(sys.stdin.read() or '{{}}')
tool = data.get('tool_name', '')
inp  = data.get('tool_input', {{}}) or {{}}
text = json.dumps(inp, sort_keys=True)
key  = hashlib.sha1((tool + ':' + text).encode()).hexdigest()
state = json.loads(STATE.read_text())
calls = state.setdefault('calls', {{}})
n = calls.get(key, 0)
calls[key] = n + 1
def bash_command(inp):
    if isinstance(inp, dict):
        for k in ('command', 'cmd', 'script'):
            if isinstance(inp.get(k), str):
                return inp[k]
    return ''
def is_write_like(tool, inp, text):
    if tool in ('Edit','Write','MultiEdit'):
        return True
    if tool != 'Bash':
        return False
    cmd = bash_command(inp).strip()
    if not cmd:
        return False
    if OFFICECLI_MUTATOR_RE.search(cmd):
        return True
    if re.search(r'(^|\\s)(>|>>|2>|&>)', cmd):
        return True
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return any(tok in cmd for tok in (' officecli batch ', ' officecli set ', ' officecli raw-set '))
    if not parts:
        return False
    exe = pathlib.Path(parts[0]).name
    if exe == 'officecli':
        verb = parts[1] if len(parts) > 1 else ''
        return verb in OFFICECLI_MUTATORS
    return exe in SHELL_MUTATORS
write_like = is_write_like(tool, inp, text)
# Loop guard: same call >2x is blocked.
if n >= 2 and write_like:
    LOG.open('a').write(f'[loop-block] {{tool}} repeated {{n+1}}x — blocking\\n')
    print(json.dumps({{'permissionDecision':'deny','permissionDecisionReason':'loop-guard: identical write attempted 3 times. Re-read manifest and replan.'}}))
    sys.exit(0)
# Edit cap.
if write_like:
    state['writes'] = state.get('writes', 0) + 1
    if state['writes'] > CAP:
        LOG.open('a').write(f'[cap-block] writes={{state["writes"]}} cap={{CAP}}\\n')
        print(json.dumps({{'permissionDecision':'deny','permissionDecisionReason':f'edit cap reached ({{CAP}}). Write report and exit.'}}))
        STATE.write_text(json.dumps(state))
        sys.exit(0)
    # Reset validation counter on any new write.
    state['verifies_since_write'] = 0
STATE.write_text(json.dumps(state))
print(json.dumps({{'permissionDecision':'allow'}}))
"""
    )
    pre_tool.chmod(0o755)

    post_tool = session_dir / "post_tool.py"
    post_tool.write_text(
        f"""#!/usr/bin/env python3
import json, sys, pathlib
STATE = pathlib.Path({str(state)!r})
LOG   = pathlib.Path({str(log)!r})
data = json.loads(sys.stdin.read() or '{{}}')
tool = data.get('tool_name', '')
tool_input = data.get('tool_input', {{}}) or {{}}
inp  = json.dumps(tool_input, sort_keys=True)
state = json.loads(STATE.read_text())
def bash_command(inp):
    if isinstance(inp, dict):
        for k in ('command', 'cmd', 'script'):
            if isinstance(inp.get(k), str):
                return inp[k]
    return ''
def command_validates(cmd):
    return bool(__import__('re').search(r'(^|[;&|]\\s*|&&\\s*|\\|\\|\\s*)officecli\\s+(validate|verify|vrf)\\b', cmd))
def tool_succeeded(data):
    response = data.get('tool_response', data.get('tool_output', data.get('result', {{}})))
    if isinstance(response, dict):
        for k in ('exit_code', 'exitCode', 'status'):
            if k in response:
                return response[k] in (0, '0', 'success', 'ok')
        if response.get('error') or response.get('stderr'):
            return False
    text = json.dumps(response, sort_keys=True)
    if '"exit_code": 0' in text or '"exitCode": 0' in text:
        return True
    if '"exit_code":' in text or '"exitCode":' in text or 'error' in text.lower():
        return False
    return False
if tool == 'Bash' and command_validates(bash_command(tool_input)) and tool_succeeded(data):
    state['verifies_since_write'] = state.get('verifies_since_write', 0) + 1
    LOG.open('a').write(f'[verify-ok] verifies_since_write={{state["verifies_since_write"]}}\\n')
STATE.write_text(json.dumps(state))
"""
    )
    post_tool.chmod(0o755)

    stop_hook = session_dir / "stop.py"
    stop_hook.write_text(
        f"""#!/usr/bin/env python3
import json, sys, pathlib
STATE = pathlib.Path({str(state)!r})
LOG   = pathlib.Path({str(log)!r})
state = json.loads(STATE.read_text())
# If there have been writes but no validate since the last one, refuse to stop.
if state.get('writes', 0) > 0 and state.get('verifies_since_write', 0) == 0:
    LOG.open('a').write('[stop-block] writes happened without a validate; forcing continuation\\n')
    print(json.dumps({{'decision':'block','reason':'writes happened without validate. Run `officecli validate <file>` then emit the stage4.report.json before stopping.'}}))
    sys.exit(0)
print(json.dumps({{}}))
"""
    )
    stop_hook.chmod(0o755)

    settings = {
        "hooks": {
            "PreToolUse": [{"hooks": [{"type": "command", "command": str(pre_tool)}]}],
            "PostToolUse": [{"hooks": [{"type": "command", "command": str(post_tool)}]}],
            "Stop": [{"hooks": [{"type": "command", "command": str(stop_hook)}]}],
        }
    }
    settings_path = session_dir / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2))
    return settings_path


# --------------------------------------------------------------------------
# Plan + launch
# --------------------------------------------------------------------------


@dataclass
class LaunchPlan:
    file: Path
    manifest: Path
    model: str
    subagent_model: str
    grunt_model: str
    skills: list[str]
    bootstrap: str
    backup: Path | None
    use_skill: bool
    settings_path: Path | None


def build_launch_plan(
    file_path: Path,
    manifest: Manifest,
    *,
    model: str = DEFAULT_MODEL,
    subagent_model: str = DEFAULT_SUBAGENT_MODEL,
    grunt_model: str = DEFAULT_GRUNT_MODEL,
    edit_cap: int = DEFAULT_EDIT_CAP,
    force_embedded: bool = False,
) -> LaunchPlan:
    use_skill = (not force_embedded) and claude_skill_available()
    companion = (
        DOCX_COMPANION_SKILLS if manifest.file_format == FileFormat.DOCX else PPTX_COMPANION_SKILLS
    )
    skills = [SKILL_NAME, *companion] if use_skill else []
    manifest_path = file_path.parent / f"{file_path.stem}.manifest.json"
    backup = Path(manifest.file_backup_path) if manifest.file_backup_path else None

    if use_skill:
        bootstrap = _bootstrap_prompt_skill_path(manifest_path, file_path)
    else:
        bootstrap = _bootstrap_prompt_embedded(
            manifest_path,
            file_path,
            subagent_model=subagent_model,
            grunt_model=grunt_model,
        )

    session_dir = file_path.parent / f".{file_path.stem}.stage4"
    settings_path = _write_session_hooks(session_dir, edit_cap=edit_cap)

    return LaunchPlan(
        file=file_path,
        manifest=manifest_path,
        model=model,
        subagent_model=subagent_model,
        grunt_model=grunt_model,
        skills=skills,
        bootstrap=bootstrap,
        backup=backup,
        use_skill=use_skill,
        settings_path=settings_path,
    )


def render_launch_command(plan: LaunchPlan) -> list[str]:
    cmd = [
        "claude",
        "--model",
        plan.model,
        "--append-system-prompt",
        plan.bootstrap,
    ]
    if plan.settings_path:
        cmd.extend(["--settings", str(plan.settings_path)])
    for s in plan.skills:
        cmd.extend(["--skill", s])
    cmd.append(str(plan.file))
    return cmd


def launch(plan: LaunchPlan, *, dry_run: bool = False) -> int:
    if dry_run:
        cmd = render_launch_command(plan)
        path_label = "skill" if plan.use_skill else "embedded fallback"
        print("Stage-4 launch plan:")
        print(f"  file:      {plan.file}")
        print(f"  manifest:  {plan.manifest}")
        print(f"  model:     {plan.model}  (orchestrator)")
        print(f"  subagent:  {plan.subagent_model}  (judgment)")
        print(f"  grunt:     {plan.grunt_model}  (mechanical)")
        print(f"  path:      {path_label}")
        if plan.skills:
            print(f"  skills:    {', '.join(plan.skills)}")
        print(f"  hooks:     {plan.settings_path}")
        print(f"  backup:    {plan.backup}")
        print()
        print("Command:")
        print("  " + " \\\n  ".join(cmd))
        return 0

    if not claude_cli_available():
        print("[error] Claude Code CLI ('claude') not found in PATH.", flush=True)
        print("        Skipping stage 4. Manifest is at:", plan.manifest, flush=True)
        return 4

    cmd = render_launch_command(plan)
    proc = subprocess.run(cmd, env=os.environ.copy())
    return proc.returncode
