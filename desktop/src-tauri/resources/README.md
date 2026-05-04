# Bundled resources

This directory is populated at build time by `scripts/prepare-resources.sh` (locally) or by the CI workflow (GitHub Actions). Contents are gitignored.

Expected files at build time:
- `officecli` — OfficeCLI binary for the target platform (renamed from `officecli-mac-arm64` etc.)
- `accessibleoffice-0.1.0-py3-none-any.whl` — Python wheel built from the project source

The Tauri app copies these to `~/.accessibleoffice/bin/officecli` and `~/.accessibleoffice-runtime/` on first launch.
