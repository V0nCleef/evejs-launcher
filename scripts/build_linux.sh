#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# EveJS Launcher V1 — Linux Build Script
#
# Produces a standalone binary at  dist/evejs-launcher
#
# Prerequisites:
#   python3 -m pip install -r requirements.txt
#
# Optional:  wmctrl  or  xdotool  for EVE window detection/focus.
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "════════════════════════════════════════════════════════════"
echo "  EveJS Launcher V1 — Linux Build"
echo "════════════════════════════════════════════════════════════"
echo ""

# ── Step 1: Dependencies ─────────────────────────────────────────────────────
echo "[1/3] Installing dependencies..."
python3 -m pip install -r requirements.txt --quiet
echo "  ✓ Done."

# ── Step 2: Build ────────────────────────────────────────────────────────────
echo "[2/3] Building with PyInstaller..."
python3 -m PyInstaller build_linux.spec --clean --noconfirm
echo "  ✓ Done."

# ── Step 3: Verify ───────────────────────────────────────────────────────────
echo "[3/3] Verifying build..."
BINARY="dist/evejs-launcher"
if [[ -f "$BINARY" ]]; then
    SIZE=$(stat -c%s "$BINARY" 2>/dev/null || stat -f%z "$BINARY" 2>/dev/null)
    SIZE_MB=$(echo "scale=1; $SIZE / 1048576" | bc 2>/dev/null || echo "?")
    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo "  BUILD SUCCESSFUL"
    echo "  Output:  $BINARY"
    echo "  Size:    ${SIZE_MB} MB  (${SIZE} bytes)"
    echo "════════════════════════════════════════════════════════════"
    echo ""
    echo "To run:  ./$BINARY"
    echo ""
    echo "Optional runtime dependencies for best experience:"
    echo "  • wmctrl    — EVE window detection & auto-focus"
    echo "  • xdotool   — fallback window management"
    echo "  • Proton    — run the EVE client (GE-Proton recommended)"
    echo "    Install:  ProtonUp-Qt  or 手动放到 ~/.steam/steam/compatibilitytools.d/"
else
    echo "[ERROR] Binary not found at $BINARY"
    exit 1
fi
