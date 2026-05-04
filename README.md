# accessibleoffice

Detect and fix Microsoft Office accessibility issues in `.docx` and `.pptx` files.

Implements Microsoft's published [Accessibility Checker rule catalog](https://support.microsoft.com/en-us/office/rules-for-the-accessibility-checker-651e08f2-0fc3-4e10-aaca-74b4a67101c1) directly against OOXML, since Microsoft does not expose the in-app checker as a public API. Fixes are applied through [`officecli`](https://github.com/iOfficeAI/OfficeCLI) for safety and consistency with downstream agentic remediation.

## Install

The easy way (recommended) — global, isolated, on PATH:

```bash
pipx install git+https://github.com/ildunari/accessibleoffice.git    # one-line install
officecli skills install pptx word                           # one-time prereq for stage 4
```

Full mode (stage 4) also requires [Claude Code](https://claude.com/product/claude-code) on your PATH.

Or for development:

```bash
git clone https://github.com/ildunari/accessibleoffice.git ~/LocalDev/office-a11y-fixer
cd ~/LocalDev/office-a11y-fixer
uv venv && uv sync && uv pip install -e .
```

## Run from source (no install)

If you have the desktop pack zip, you don't need to install anything globally — `run.sh` (macOS / Linux) and `run.bat` (Windows) bootstrap a private venv in `~/.accessibleoffice-runtime/`, drop the wheel into it, and pass your args straight to the CLI:

```bash
./run.sh                         # show CLI help
./run.sh path/to/deck.pptx       # scan + auto-fix (default mode = auto)
./run.sh path/to/file.docx --mode scan
```

First run installs the wheel; subsequent runs reuse the venv and start instantly. Delete `~/.accessibleoffice-runtime/` to reset.

## Usage

Pick a mode and point it at a file:

```bash
accessibleoffice deck.pptx --mode scan        # detect only, no writes (CI / audit)
accessibleoffice deck.pptx --mode auto        # fully deterministic — fastest, no AI, no API key
accessibleoffice deck.pptx --mode full        # full pipeline + interactive Claude Code remediation
accessibleoffice deck.pptx --mode full --dry-run   # show what 'full' would do without spending tokens
```

**Default mode is `auto`** — safe, fast, no network.

Granular flags still work for advanced users (`--report-only`, `--auto-only`, `--remediate`, `--rules ...`, `--skip-rules ...`, `--strict`, `--output ...`). Run `accessibleoffice --help` for the full list.

## Pipeline

| Stage | What | Where |
|---|---|---|
| 1. Detect | Run rules engine over OOXML | `src/a11yfix/rules/` |
| 2. Auto-fix (deterministic) | Mechanical fixes (header flags, language tag, etc.) | `src/a11yfix/fixers/deterministic.py` |
| 3. Auto-fix (single-shot AI) | Generate alt text, link text, slide titles | `src/a11yfix/fixers/single_shot.py` |
| 4. Agentic review | Claude Code skill on residual judgment-calls | _separate deliverable_ |

The output of stages 1–3 is a **manifest JSON** that stage 4 consumes — see `docs/manifest-schema.md`.

## Desktop app

A Tauri 2 desktop GUI lives in [`desktop/`](desktop). Drag a `.docx` or `.pptx`, pick a mode, see the manifest. Cross-platform builds (macOS `.dmg`, Windows `.msi`, Linux `.AppImage`) — see [`desktop/BUILD.md`](desktop/BUILD.md). The app auto-detects whether the AccessibleOffice CLI and Claude Code are installed, and gates the **Full** mode on Claude Code.

```bash
cd desktop && npm install && npm run tauri dev
```

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
