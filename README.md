# accessibleoffice

Detect and fix Microsoft Office accessibility issues in `.docx` and `.pptx` files.

Implements Microsoft's published [Accessibility Checker rule catalog](https://support.microsoft.com/en-us/office/rules-for-the-accessibility-checker-651e08f2-0fc3-4e10-aaca-74b4a67101c1) directly against OOXML, since Microsoft does not expose the in-app checker as a public API. Fixes are applied through [`officecli`](https://github.com/iOfficeAI/OfficeCLI) for safety and consistency with downstream agentic remediation.

## Install

### Easiest: desktop app

Download the installer for your platform from the [latest release](https://github.com/ildunari/accessibleoffice/releases) (`.dmg` for Mac, `.exe` for Windows, `.AppImage` or `.deb` for Linux), open it, and run the app. On first launch, the app shows a one-button setup wizard that:

1. Detects whether you have Python 3.11+ (and walks you through installing it from python.org if not)
2. Copies its bundled OfficeCLI binary to `~/.accessibleoffice/bin/`
3. Creates a private Python runtime at `~/.accessibleoffice-runtime/` and installs the scanner

No terminal required. No admin password required. Drag a Word or PowerPoint file onto the app: it scans, applies the deterministic fixes automatically, and reports what remains (alt text and slide titles need `full` mode; contrast and reading order are flagged for human review). See [Usage](#usage) for what each mode changes.

### CLI install

If you want the command-line tool globally:

```bash
pipx install git+https://github.com/ildunari/accessibleoffice.git
officecli skills install pptx                # only needed for --mode full (stage 4)
officecli skills install word
```

Full mode (stage 4) also requires [Claude Code](https://claude.com/product/claude-code) on your PATH. You'll need [`officecli`](https://github.com/iOfficeAI/OfficeCLI/releases) on PATH for any mode that writes (auto, full).

### Development

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

What `auto` actually fixes: only the **deterministic** issues — document title, table header rows, and (with `--default-lang`) the document language. The bulk of real-world findings on content-heavy decks — alt text, slide titles, link text — need a model and are applied by `--mode full` (stage 3); contrast and reading-order are human-judgment calls that are never auto-applied. The report ends with a `What's left:` breakdown (AI-fixable / deterministic / manual) so you know whether `auto` actually changed anything or you need `full` — fixability is classified per finding, not per rule, so e.g. an off-canvas slide title is honestly listed under manual review rather than promised to `full`, and a leftover document-language finding points you at `--default-lang`. If a run applies no fixes — `auto` with nothing deterministic to do, or `full` when the AI stage produces zero operations — it leaves the file byte-for-byte unchanged.

The AI stages are backend-pluggable. `--vlm` picks the stage-3 backend, `--agent` picks the stage-4 one:

- `--vlm claude` (default) drives Claude Code with its OAuth login — no API key needed. `claude-api` / `anthropic` use the Anthropic SDK (`ANTHROPIC_API_KEY`); `openai` / `openrouter` hit the chat-completions API directly (`OPENAI_API_KEY` / `OPENROUTER_API_KEY`) — the cheapest path to any model, including everything OpenRouter proxies. `pi`, `opencode`, and `codex` shell out to those agent CLIs and reuse whatever auth they're already logged in with (a Claude Max, ChatGPT, or Copilot subscription works); the binary must be on PATH. `--vlm-model` overrides any backend's default model.
- `--agent claude` (default) runs stage-4 remediation (`--remediate` / `--mode full`) in Claude Code with hook-based safety rails; `--agent codex` runs it in OpenAI Codex inside a sandboxed session with a verify-restore gate.

A missing backend — binary not on PATH, key not set — never fails the run: stages 1–2 still apply and the report lists the file as needing the AI stages (batch runs mark it `partial`). The `pi`, `opencode`, and `codex` VLM backends are experimental until validated against the live binaries.

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

A Tauri 2 desktop GUI lives in [`desktop/`](desktop). Drag a `.docx` or `.pptx`, pick a mode, see the manifest. Cross-platform builds (macOS `.dmg`, Windows `.exe`, Linux `.AppImage`) — see [`desktop/BUILD.md`](desktop/BUILD.md).

The app bundles OfficeCLI and the AccessibleOffice wheel as resources and runs a one-time setup wizard on first launch: detects Python (links to python.org if missing), copies OfficeCLI to `~/.accessibleoffice/bin/`, creates a private venv, and installs the wheel. Subsequent launches go straight to the file picker.

For local development (the resources are populated by `scripts/prepare-resources.sh`):

```bash
scripts/prepare-resources.sh        # downloads OfficeCLI + builds the wheel into desktop/src-tauri/resources/
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
