#!/usr/bin/env bash
# Build the native Linux onedir and its mode-preserving release archive.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
BUILD_PYTHON="${EVEJS_BUILD_PYTHON:-python3}"
"$BUILD_PYTHON" -m PyInstaller --clean --noconfirm build_linux.spec
"$BUILD_PYTHON" scripts/package_linux.py
printf '%s\n' 'Linux package: dist/EveJS-Launcher-Linux-x86_64.tar.gz'
