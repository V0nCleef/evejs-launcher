"""Shared pytest fixtures for Qt-based launcher tests."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Return one offscreen QApplication for the full test session."""
    app = QApplication.instance() or QApplication([])
    return app
