#!/usr/bin/env bash
# Build the Tauri macOS app and copy the .app bundle to your Desktop.
# Requires: Rust, Node (for npx), Python 3 with workspace deps (pip install -r requirements.txt).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/src-tauri"

# npm can set CI=1 which breaks `cargo tauri` (invalid --ci flag).
unset npm_config_ci CI CARGO_CI 2>/dev/null || true

export CARGO_TARGET_DIR="${ROOT}/src-tauri/target"

npx --yes @tauri-apps/cli@2 build

APP="${ROOT}/src-tauri/target/release/bundle/macos/Research Workspace.app"
if [[ ! -d "$APP" ]]; then
  echo "Expected bundle not found: $APP" >&2
  exit 1
fi

rm -rf "${HOME}/Desktop/Research Workspace.app"
cp -R "$APP" "${HOME}/Desktop/"
echo "Copied to Desktop: ${HOME}/Desktop/Research Workspace.app"
