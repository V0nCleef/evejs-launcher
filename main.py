"""EveJS Multi-Box Launcher V2 — main entry point.

Bootstraps the QApplication, applies the dark palette/theme, loads fonts,
creates the MainWindow, and enters the Qt event loop.
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path

# ── Suppress noisy Qt DPI warning on Windows (must precede QApplication) ──
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")

# Make ``src`` package importable when invoked as a script.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtGui import QColor, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication

from src.constants import APP_NAME, COLORS
from src import theme


def _build_palette() -> QPalette:
    """Compose the application-wide QPalette from the COLORS palette."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["void_black"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["white"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["deep_space"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["carbon"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["white"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["carbon"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["white"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["teal"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(COLORS["void_black"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["carbon"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["white"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["grey"]))
    return palette


def _load_fonts() -> dict[str, str]:
    """Best-effort font loading. Falls back to system families."""
    try:
        return theme.load_fonts()
    except Exception:
        return {"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"}


def _parse_update_handoff(argv: list[str]):  # type: ignore[no-untyped-def]
    """Return a staged-update request when this build was launched as an agent."""
    if "--apply-update" not in argv:
        return None

    from src.updater.handoff import parse_update_handoff_args

    return parse_update_handoff_args(argv)


def _schedule_pending_update_cleanup() -> None:
    """Clean validated update artifacts from the freshly restarted build."""
    if not getattr(sys, "frozen", False):
        return

    from src.updater.handoff import cleanup_pending_update

    install_dir = Path(sys.executable).resolve().parent
    threading.Thread(
        target=cleanup_pending_update,
        args=(install_dir,),
        daemon=True,
        name="update-cleanup",
    ).start()


def main() -> int:
    handoff = _parse_update_handoff(sys.argv[1:])
    app = QApplication([sys.argv[0]] if handoff is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("NousResearch")

    # Apply dark palette before any widget is constructed.
    app.setPalette(_build_palette())

    # Load fonts + build QSS theme and apply globally.
    fonts = _load_fonts()
    try:
        app.setStyleSheet(theme.build_qss(fonts))
    except Exception:
        # QSS failure shouldn't prevent the app from starting.
        pass

    if handoff is not None:
        from src.updater.checker import get_current_version
        from src.updater.handoff import UpdateHandoffWindow

        window = UpdateHandoffWindow(handoff, get_current_version())
        window.show()
        return app.exec()

    # ── First-run setup wizard ────────────────────────────────────────
    from src import config
    cfg = config.load()
    if not cfg.get("evejs_root"):
        from src.wizard import SetupWizard
        wizard = SetupWizard()
        if wizard.exec() == 1:  # QDialog.DialogCode.Accepted
            cfg = config.load()  # re-read after wizard saved it
        # Even if cancelled, let the user configure via Settings later.

    # Lazy import so any failure inside MainWindow still surfaces through
    # the fatal handler below with a full traceback.
    from src.app import MainWindow

    window = MainWindow()
    window.show()
    _schedule_pending_update_cleanup()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — surface everything on fatal
        print(f"FATAL ERROR: {exc}", flush=True)
        traceback.print_exc()
        sys.exit(1)
