# Accessibility Pipeline Review - 2026-06-05

Context: Huyen tested the pipeline on a real BIOL2089 Canvas files folder and reported PPT false positives, lower-than-expected fix coverage, and mostly clean results from Word's built-in Accessibility Checker. This report combines those field findings with a code review of the current repo.

## Executive Summary

The student report is credible. The main gap is not just "Microsoft parity is hard"; there are concrete implementation issues that explain the symptoms:

- PPT alt-text detection currently scans text boxes, graphic frames, groups, and shapes as if they may all need image alt text. The intended text-box/media filter is effectively a no-op, so titles and ordinary text boxes can become `alt-text-missing` findings.
- PPT contrast rules assume a white slide background and do not resolve actual slide/master/background fills, so visible colored text can be flagged as low contrast.
- "Full" mode does not mean every finding will be fixed. Stage 3 only fixes issues it can turn into safe officecli operations, and Stage 4 is either a dry-run in the desktop single-file path or disabled for batch runs.
- The Stage 4 Claude launcher has command/hook bugs that can prevent reliable remediation even when the user does launch it manually.

The advertised "~80%" needs to be rephrased. Based on current code, it should not be described as broad auto-fix coverage across real course PPT decks. A more accurate claim is: deterministic + AI fixes cover a subset of mechanically addressable findings, with better behavior on simple Word/PPT fixtures than on arbitrary instructor decks.

## Student-Observed Findings

### S1. PPT text boxes and titles can be falsely reported as missing image alt text

Severity: High  
Observed by Huyen: one deck had about 124 findings, mostly titles/text boxes flagged as missing-image alt text.

Evidence:

- `AltTextRule` explicitly scans `p:sp`, `p:graphicFrame`, and `p:grpSp` in addition to real pictures at `src/a11yfix/rules/alt_text.py:93-98`.
- The code comments say plain text boxes should be skipped, but the branch only executes `pass`; it does not `continue`, so those shapes still fall through to `_alt_text(cnv)` and can emit findings at `src/a11yfix/rules/alt_text.py:115-128`.
- Placeholder skipping is narrow and appears structurally wrong: inside a `p:nvSpPr` node, it looks for `p:nvSpPr` again before checking `p:nvPr`, so common title/body placeholders may not be skipped reliably at `src/a11yfix/rules/alt_text.py:108-114`.

Impact:

- Inflates error counts.
- Makes manifests noisy enough that real image issues are harder to see.
- Explains the mismatch against Word/PowerPoint's built-in checker.

Recommended fix:

- For PPT alt text, start by flagging only `p:pic` with embedded image bytes.
- Add separate, lower-severity rules for charts/SmartArt/groups if needed.
- For `p:sp`, require evidence of actual picture/media content before emitting `alt-text-missing`; otherwise skip text boxes and placeholders.
- Add fixture tests for title placeholders, body placeholders, ordinary text boxes, charts, and a real image on the same slide.

### S2. PPT contrast can false-positive because background is assumed white

Severity: Medium  
Observed by Huyen: visible colored text was flagged as a contrast problem.

Evidence:

- `ColorContrastRule` defaults every PPT slide background to white at `src/a11yfix/rules/color_contrast.py:68-81`.
- The file itself notes Microsoft uses richer analysis and that this implementation does not fully replicate it at `src/a11yfix/rules/color_contrast.py:10-12`.
- `NonTextContrastRule` is explicitly described as a stub and also assumes white at `src/a11yfix/rules/nontext_contrast.py:1-4` and `src/a11yfix/rules/nontext_contrast.py:31-49`.

Impact:

- Text over dark or colored slide backgrounds can be incorrectly flagged.
- Theme/master/background-dependent decks will diverge from Microsoft Accessibility Checker.

Recommended fix:

- Resolve slide background in order: slide fill, layout fill, master fill, theme default.
- Lower confidence or suppress contrast findings when background cannot be determined.
- Include the assumed/resolved background source in `Finding.extra`.
- Consider marking these as "needs visual verification" instead of strong accessibility findings until background resolution is reliable.

### S3. Full mode does not currently guarantee deck fixes, especially from the desktop app

Severity: High  
Observed by Huyen: full run with Claude Code did not apply fixes to the deck, including real images.

Evidence:

- CLI `--mode full` sets `remediate=True` at `src/a11yfix/cli.py:641-650`, but Stage 3 only produces operations for supported single-shot kinds.
- Stage 3 alt text only proceeds when `extract_image_for_finding` returns bytes at `src/a11yfix/fixers/single_shot.py:128-158`.
- PPT image extraction only searches `p:pic` by matching `cNvPr@id`; findings emitted for `p:sp`, `p:graphicFrame`, or `p:grpSp` cannot be image-described and are deferred at `src/a11yfix/ooxml/image_extract.py:98-144`.
- The desktop single-file path appends `--dry-run` whenever mode is `full`, so Stage 4 prints a launch command instead of executing remediation at `desktop/src-tauri/src/lib.rs:663-672`.
- Batch full mode explicitly does not spawn interactive Claude Code; the GUI warns that only stages 1-3 run at `desktop/src-tauri/src/lib.rs:889-895`.

Impact:

- A user can reasonably believe "full" ran Claude remediation when it only printed a plan or skipped Stage 4.
- Real images may still not be fixed if image extraction or officecli setting fails.
- False-positive alt findings consume attention and can make it look like no fixes happened.

Recommended fix:

- Rename GUI "Full" to something clearer, such as "AI draft + agent launch plan", unless it actually executes Stage 4.
- In batch mode, label Stage 4 as "not run" in the rollup and manifest.
- Add a manifest field like `stage_4_status: not_requested|dry_run|launched|completed|failed`.
- Add explicit counters for `stage_3_deferred_reason`, especially `no_image_bytes`, `low_confidence`, `officecli_failed`, and `cost_cap`.

### S4. The 80% coverage claim is ambiguous and likely too broad

Severity: Medium

Current behavior:

- Stage 2 deterministic fixes cover only rules with deterministic `fix_deterministic` implementations.
- Stage 3 handles only `alt-text`, `link-text`, and `slide-title` single-shot descriptors at `src/a11yfix/fixers/single_shot.py:128-210`.
- Many findings are intentionally deferred to human/agent review.

Recommendation:

- Split coverage claims into detection coverage vs auto-fix coverage.
- Split by format: DOCX and PPTX have very different reliability profiles.
- Publish an eval table from representative course files: detected, true positives, false positives, Stage 2 fixed, Stage 3 fixed, Stage 4 fixed, residual.

## General Code Review Findings

### G1. Stage 4 tells agents to run `officecli verify`, but installed officecli uses `validate`

Severity: High

Evidence:

- Stage 4 hard rules say to run `officecli verify <file>` at `src/a11yfix/stage4.py:116-118`.
- The embedded workflow repeats `officecli verify <file>` at `src/a11yfix/stage4.py:163-190`.
- The hook only credits `officecli verify` or `officecli vrf` at `src/a11yfix/stage4.py:332-345`.
- Local `officecli --help` lists `validate <file>`, not `verify`.

Impact:

- Manual Stage 4 remediation can dead-end on a nonexistent command.
- Even if the agent runs the correct `officecli validate`, the hook will not count it.

Recommended fix:

- Replace `verify` with `validate` everywhere unless `verify` is confirmed as an alias in the bundled OfficeCLI.
- Update the hook to accept `officecli validate`.
- Add a smoke test that renders a launch plan and checks for valid OfficeCLI commands.

### G2. Stage 4 hook treats every Bash command as a write

Severity: High

Evidence:

- `write_like = tool in ('Bash','Edit','Write','MultiEdit') or 'set' in text or 'apply' in text` at `src/a11yfix/stage4.py:310`.
- That increments write count and resets verification state for every Bash call at `src/a11yfix/stage4.py:317-325`.

Impact:

- Read-only commands such as `ls`, `cat`, `officecli help`, and `officecli validate` consume the edit cap.
- The Stop hook can demand validation after purely read-only inspection.

Recommended fix:

- Classify Bash commands by command content, not by tool name alone.
- Treat only known mutators as writes: `officecli set/add/remove/move/swap/raw-set/batch`, shell redirection to target files, `python` scripts known to write, `cp/mv/rm`, etc.
- Treat `officecli help/get/query/view/validate` as read-only.

### G3. Batch timeout marks files failed but does not stop the timed-out work

Severity: High

Evidence:

- `_run_batch` wraps `_process_one_file` in a `ThreadPoolExecutor` and calls `future.result(timeout=per_file_timeout)` at `src/a11yfix/cli.py:399-417`.
- On timeout it records failure and continues at `src/a11yfix/cli.py:417-431`, but the executor context still waits for the running thread to finish. Python cannot kill that worker thread.

Impact:

- A timed-out file can keep running, mutate the document, call AI, or block the batch after it has been recorded as failed.
- Cost accounting can be wrong if model calls complete after the timeout record.

Recommended fix:

- Run each file in a subprocess, not a thread, when enforcing wall-clock timeout.
- Kill the process group on timeout.
- Record timeout after the subprocess is actually terminated.

### G4. `officecli batch` result parsing can mark no-op or malformed output as applied

Severity: Medium-High

Evidence:

- `BatchResult.success` is true when return code is zero and all parsed ops are ok; if no per-op JSON is parsed, `all([])` is true at `src/a11yfix/ooxml/officecli.py:104-127`.
- Fixers then zip against `result.per_op or [{}] * len(pending_ops)` and may mark each operation as applied at `src/a11yfix/fixers/deterministic.py:97-115` and `src/a11yfix/fixers/single_shot.py:247-268`.

Impact:

- Manifests can report fixes that officecli did not apply.
- Validation only checks document structure, not whether the intended property changed.

Recommended fix:

- Require per-op result count to equal pending op count.
- Treat empty/unparseable JSON as batch failure even with exit code 0.
- For high-value operations, re-query the edited property before recording `AppliedFix`.

### G5. Desktop cancel only kills the top-level process on Unix

Severity: Medium

Evidence:

- The desktop app stores only the direct child pid at `desktop/src-tauri/src/lib.rs:904-906`.
- Unix cancel sends `kill <pid>` only at `desktop/src-tauri/src/lib.rs:973-982`.
- Windows uses `taskkill /T`, which does terminate child processes at `desktop/src-tauri/src/lib.rs:983-988`.

Impact:

- On macOS/Linux, spawned `officecli`, Python, or AI child processes can survive cancellation.
- Users may believe a run stopped while file mutation or model calls continue.

Recommended fix:

- Spawn CLI runs in their own process group/session.
- On cancel, send SIGTERM then SIGKILL to the process group after a grace period.

## Suggested Fix Order

1. Fix PPT alt-text false positives by restricting `alt-text-missing` to real image-bearing elements first.
2. Fix Stage 4 `verify` vs `validate` and hook write classification, so manual remediation can be trusted.
3. Make GUI/batch mode labels honest about Stage 4: dry-run/not-run/completed.
4. Harden officecli batch result validation before claiming fixes.
5. Replace thread-based batch timeout with subprocess process-group timeout.
6. Improve PPT background resolution for contrast, or demote/suppress uncertain contrast findings.

## Suggested Tests

- PPT with title placeholder only: no `alt-text-missing`.
- PPT with ordinary text box only: no `alt-text-missing`.
- PPT with real `p:pic` missing alt: one `alt-text-missing`, Stage 3 attempts image extraction.
- PPT with real image plus text boxes: only the real image is flagged.
- PPT with dark background and light text: no white-background contrast false positive.
- Stage 4 launch plan contains `officecli validate`, not `officecli verify`.
- Hook fixture: `officecli help` and `officecli validate` do not increment write count; `officecli batch` does.
- Batch timeout fixture proves timed-out file process is killed before progress is recorded.

## Notes For Replying To Huyen

Recommended framing:

- Thank her; her findings are useful and reproduce plausible code-level gaps.
- Clarify that the 80% number should not be interpreted as 80% auto-fix coverage on arbitrary course files.
- Explain that PPT decks are currently noisier than Word docs because the rule scans shapes more broadly than PowerPoint's checker.
- Ask for the deck and manifests, especially the false-positive slide examples, to build regression fixtures.
- Say real images should be candidates for Stage 3 auto-description, but only when the pipeline can extract image bytes and the AI result passes confidence/validation. Current full-mode UX may have made Stage 4 look more complete than it was.
