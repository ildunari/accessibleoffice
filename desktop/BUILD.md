# Building AccessibleOffice desktop

The desktop app is a Tauri 2 shell over the Python `accessibleoffice` CLI. It runs on macOS, Windows, and Linux. Build artifacts land under `desktop/src-tauri/target/release/bundle/`.

## Prerequisites

- Node 20+ (`node --version`)
- Rust stable (`rustup show active-toolchain`)
- Platform-specific toolchains:
  - **macOS**: Xcode Command Line Tools (`xcode-select --install`)
  - **Windows**: WebView2 (preinstalled on Windows 11) and the MSVC build tools
  - **Linux**: `webkit2gtk-4.1-dev`, `libayatana-appindicator3-dev`, `librsvg2-dev`, `libssl-dev`, `pkg-config`

## Local build (host platform only)

```bash
cd desktop
npm install
npm run tauri build
```

Outputs (filenames track Tauri's `productName`):

| Platform | Path |
|---|---|
| macOS | `src-tauri/target/release/bundle/dmg/AccessibleOffice_0.1.0_*.dmg` |
| Windows | `src-tauri/target/release/bundle/nsis/AccessibleOffice_0.1.0_x64-setup.exe` |
| Linux  | `src-tauri/target/release/bundle/appimage/AccessibleOffice_0.1.0_amd64.AppImage` |

## macOS universal binary

```bash
rustup target add x86_64-apple-darwin aarch64-apple-darwin
cd desktop
npm run tauri build -- --target universal-apple-darwin
```

The dmg ends up under `src-tauri/target/universal-apple-darwin/release/bundle/dmg/`.

## Cross-platform release via GitHub Actions

`.github/workflows/desktop-release.yml` runs on `workflow_dispatch` or a tag matching `desktop-v*`. It builds the matrix (`macos-14`, `windows-latest`, `ubuntu-22.04`) and uploads bundle artifacts to the run page.

```bash
gh workflow run desktop-release
# or
git tag desktop-v0.1.0 && git push origin desktop-v0.1.0
```

## Runtime requirement

The desktop app **does not bundle the Python CLI**. Users install it once with `pipx`:

```bash
pipx install git+https://github.com/ildunari/a11yfix.git
```

This installs the `accessibleoffice` command. Full mode also requires [Claude Code](https://claude.com/product/claude-code) on PATH — the desktop app detects it automatically and gates the **Full** mode card on its presence.

If either CLI is missing, the app shows an install hint and a "Check again" button instead of crashing.

## Notarization & signing

Out of scope for v0.1. To enable:

- **macOS**: set `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` in CI; `tauri-action` notarizes automatically.
- **Windows**: set `WINDOWS_CERTIFICATE` and `WINDOWS_CERTIFICATE_PASSWORD` for code signing the NSIS installer.
