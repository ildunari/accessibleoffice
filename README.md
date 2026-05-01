# a11yfix

Detect and fix Microsoft Office accessibility issues in `.docx` and `.pptx` files.

Implements Microsoft's published [Accessibility Checker rule catalog](https://support.microsoft.com/en-us/office/rules-for-the-accessibility-checker-651e08f2-0fc3-4e10-aaca-74b4a67101c1) directly against OOXML, since Microsoft does not expose the in-app checker as a public API. Fixes are applied through [`officecli`](https://github.com/iOfficeAI/OfficeCLI) for safety and consistency with downstream agentic remediation.

## Install

```bash
cd ~/LocalDev/office-a11y-fixer
uv venv
uv sync
uv pip install -e .

# Prereq for stage-4 agentic remediation (separate deliverable)
officecli skills install pptx word
```

## Usage

```bash
a11yfix deck.pptx                          # full pipeline (detect + auto-fix + AI)
a11yfix deck.pptx --report-only            # detection only, no writes
a11yfix deck.pptx --auto-only              # detect + deterministic fixes (no AI)
a11yfix deck.pptx --output report.json     # write manifest for stage-4 handoff
a11yfix deck.pptx --strict                 # CI mode: non-zero exit on Errors
```

## Pipeline

| Stage | What | Where |
|---|---|---|
| 1. Detect | Run rules engine over OOXML | `src/a11yfix/rules/` |
| 2. Auto-fix (deterministic) | Mechanical fixes (header flags, language tag, etc.) | `src/a11yfix/fixers/deterministic.py` |
| 3. Auto-fix (single-shot AI) | Generate alt text, link text, slide titles | `src/a11yfix/fixers/single_shot.py` |
| 4. Agentic review | Claude Code skill on residual judgment-calls | _separate deliverable_ |

The output of stages 1–3 is a **manifest JSON** that stage 4 consumes — see `docs/manifest-schema.md`.

## Documentation

- [`docs/rule-catalog.md`](docs/rule-catalog.md) — every rule with WCAG mapping and plain-English impact
- [`docs/officecli-cookbook.md`](docs/officecli-cookbook.md) — accessibility-focused officecli command recipes
- [`docs/manifest-schema.md`](docs/manifest-schema.md) — the stage-3 → stage-4 contract

## Development

```bash
pytest -q                  # unit + integration
ruff check src/
black --check src/
mypy src/
```
