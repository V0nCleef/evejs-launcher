"""Shared pytest fixtures for Qt-based launcher tests."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFontDatabase


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Return one offscreen QApplication for the full test session."""
    app = QApplication.instance() or QApplication([])
    # Qt's Windows offscreen plugin can expose no fonts at all. Load system
    # faces in the test harness so layout checks measure text, not tofu boxes.
    if os.name == "nt" and not QFontDatabase.families():
        from pathlib import Path
        for name in ("segoeui.ttf", "bahnschrift.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name))
    return app


@pytest.fixture
def close_main_window(qapp):
    """Drain the real asynchronous close before deleting a full launcher window."""
    import time
    from PyQt6.QtCore import QEvent, QThread
    from PyQt6.QtTest import QTest

    def close(window):
        deadline = time.monotonic() + 10
        accepted = window.close()
        while not accepted or any(thread.isRunning() for thread in window.findChildren(QThread)):
            assert time.monotonic() < deadline, 'Launcher close did not release its workers'
            qapp.processEvents()
            QTest.qWait(5)
            accepted = window.close()
        window.deleteLater()
        qapp.sendPostedEvents(window, QEvent.Type.DeferredDelete)

    return close
