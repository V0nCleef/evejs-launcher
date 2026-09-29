"""Shared, rotating application diagnostics for EveJS Launcher."""
from __future__ import annotations

import logging
import os
import re
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..constants import APP_NAME

_LOG_DIR = Path(os.environ.get("APPDATA", "")) / APP_NAME / "logs"
_LOG_FILE = _LOG_DIR / "launcher.log"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3

_FORMAT = "%(asctime)s | %(levelname)-8s | %(module)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_HANDLER_LOCK = threading.RLock()
_SECRET_PATTERNS = (
    (re.compile(r"(?i)(https?://)[^/\s@]+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)/login:[^\s\"']+"), "/login:[REDACTED]"),
    (re.compile(r"(?im)\b((?:authorization|cookie|set-cookie)\s*[:=]\s*)[^\r\n]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b((?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r'''(?i)(["']?(?:password|passwd|pwd|token|access_token|refresh_token|api[_-]?key|secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)'''), r"\1[REDACTED]"),
)


class _DiagnosticFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Format the exception too before redacting; exception messages can
        # contain credentials even when the caller logs no request/settings.
        text = super().format(record)
        for pattern, replacement in _SECRET_PATTERNS:
            text = pattern.sub(replacement, text)
        return text


def setup_logger(name: str) -> logging.Logger:
    """Return a logger that uses the shared rotating root handler.

    The logger writes to ``%APPDATA%/EveJS-Launcher/logs/launcher.log``.
    Multiple calls with the same *name* return the same logger instance
    without adding duplicate handlers.
    """
    root = logging.getLogger()
    with _HANDLER_LOCK:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        target = os.path.normcase(str(_LOG_FILE.resolve()))
        found = any(
            getattr(handler, "_evejs_launcher_log", False)
            and os.path.normcase(handler.baseFilename) == target
            for handler in root.handlers
        )
        if not found:
            handler = RotatingFileHandler(
                _LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            handler._evejs_launcher_log = True
            handler.setLevel(logging.INFO)
            handler.setFormatter(_DiagnosticFormatter(_FORMAT, datefmt=_DATE_FORMAT))
            root.addHandler(handler)
        root.setLevel(logging.INFO)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = True
    return logger
