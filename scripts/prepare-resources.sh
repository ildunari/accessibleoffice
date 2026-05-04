#!/usr/bin/env bash
# Populate desktop/src-tauri/resources/ with the right platform officecli binary
# and the freshly-built Python wheel, so `npm run tauri build` (or CI) bundles
# them into the app.
#
# Usage:
#   scripts/prepare-resources.sh            # auto-detect platform + arch
#   PLATFORM=mac-arm64 scripts/prepare-resources.sh   # override (mac-arm64, mac-x64, win-x64, linux-x64)
#
# Idempotent: re-running re-downloads (or reuses cached) officecli and rebuilds the wheel.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RES_DIR="$REPO_ROOT/desktop/src-tauri/resources"
OFFICECLI_VERSION="${OFFICECLI_VERSION:-v1.0.71}"

PLATFORM="${PLATFORM:-}"
if [ -z "$PLATFORM" ]; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)             PLATFORM="mac-arm64" ;;
    Darwin-x86_64)            PLATFORM="mac-x64" ;;
    Linux-x86_64|Linux-amd64) PLATFORM="linux-x64" ;;
    MINGW*|MSYS*|CYGWIN*)     PLATFORM="win-x64" ;;
    *)
      echo "error: unsupported platform $(uname -s)-$(uname -m). Set PLATFORM=mac-arm64|mac-x64|win-x64|linux-x64." >&2
      exit 1
      ;;
  esac
fi

case "$PLATFORM" in
  mac-arm64)  ASSET="officecli-mac-arm64";   OUT_NAME="officecli" ;;
  mac-x64)    ASSET="officecli-mac-x64";     OUT_NAME="officecli" ;;
  linux-x64)  ASSET="officecli-linux-x64";   OUT_NAME="officecli" ;;
  win-x64)    ASSET="officecli-win-x64.exe"; OUT_NAME="officecli.exe" ;;
  *)
    echo "error: unknown PLATFORM=$PLATFORM" >&2
    exit 1
    ;;
esac

mkdir -p "$RES_DIR"
echo "[prepare-resources] platform=$PLATFORM officecli=$OFFICECLI_VERSION"

# 1. OfficeCLI: download via gh CLI (uses cached auth), fall back to plain curl.
OUT_OFFICECLI="$RES_DIR/$OUT_NAME"
echo "[prepare-resources] downloading $ASSET -> $OUT_OFFICECLI"
if command -v gh >/dev/null 2>&1; then
  gh release download "$OFFICECLI_VERSION" --repo iOfficeAI/OfficeCLI --pattern "$ASSET" \
    --output "$OUT_OFFICECLI" --clobber
else
  curl -fsSL -o "$OUT_OFFICECLI" \
    "https://github.com/iOfficeAI/OfficeCLI/releases/download/$OFFICECLI_VERSION/$ASSET"
fi
chmod +x "$OUT_OFFICECLI"

# 2. Python wheel: build from project root using uv.
echo "[prepare-resources] building Python wheel"
(cd "$REPO_ROOT" && rm -f dist/*.whl && uv build --wheel >/dev/null)
WHEEL="$(ls "$REPO_ROOT"/dist/accessibleoffice-*.whl | head -1)"
if [ -z "$WHEEL" ]; then
  echo "error: uv build did not produce a wheel" >&2
  exit 1
fi
cp "$WHEEL" "$RES_DIR/"
echo "[prepare-resources] wheel: $(basename "$WHEEL")"

ls -lh "$RES_DIR" | grep -v '^total'
echo "[prepare-resources] done"
