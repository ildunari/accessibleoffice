# Manifest Schema (v1)

The manifest is the **stage-3 → stage-4 contract**. Stages 1–3 (this project) write it; stage 4 (the Claude Code skill) reads it.

Schema is versioned. Breaking changes bump `schema_version`.

## Top-level shape

```json
{
  "schema_version": "1",
  "file_path": "/abs/path/deck.pptx",
  "file_format": "pptx",
  "file_backup_path": "/abs/path/.a11yfix/deck.pptx.bak",
  "scan_timestamp": "2026-05-01T12:34:56+00:00",
  "stage_1_findings_total": 42,
  "stage_2_fixes_applied": [...],
  "stage_3_fixes_applied": [...],
  "residual_findings": [...],
  "validation": {"status": "ok", "errors": []}
}
```

## Finding

```json
{
  "id": "alt-3-pic-2",
  "rule_id": "alt-text-missing",
  "severity": "error",
  "wcag_sc": ["1.1.1"],
  "officecli_path": "/sld[3]/pic[2]",
  "current_value": "",
  "plain_impact": "Screen readers cannot describe this image to users.",
  "why_human_needed": "",
  "related_findings": [],
  "extra": {"shape_kind": "pic", "shape_name": "Picture 2", "slide_index": 3}
}
```

`severity` ∈ `error` | `warning` | `tip` | `intelligent_services`

`why_human_needed` is populated only when neither stage-2 nor stage-3 could fix the finding. Stage 4 should read this to triage.

## AppliedFix

```json
{
  "finding_id": "alt-3-pic-2",
  "rule_id": "alt-text-missing",
  "officecli_path": "/sld[3]/pic[2]",
  "stage": 3,
  "before": "",
  "after": "Bar chart showing Q3 revenue by region",
  "ai_model": "claude",
  "confidence": 0.85
}
```

## ValidationResult

```json
{"status": "ok",     "errors": []}
{"status": "errors", "errors": [{"path": "/...", "msg": "..."}]}
{"status": "skipped", "errors": []}
```

`skipped` means stage 2 didn't run (e.g. `--report-only` or no fixable findings).

## Idempotency

If a manifest is fed back through stages 2–3 with the same file, no double-application should occur:

- Stage 2: fixes only fire if the rule's detection still flags the issue. Once fixed, detection no longer matches.
- Stage 3: cache hits short-circuit AI calls; officecli `set` is itself idempotent for the same value.

## Stage-4 contract

Stage 4 reads `residual_findings`, makes its own decisions, and writes a **complementary** report (separate file). It must not mutate this manifest.
