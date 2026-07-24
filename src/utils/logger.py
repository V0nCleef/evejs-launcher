"""Application logging for EveJS Launcher V2."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..constants import APP_NAME

_LOG_DIR = Path(os.environ.get("APPDATA", "")) / APP_NAME / "logs"
_LOG_FILE = _LOG_DIR / "launcher.log"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3

_FORMAT = "%(asctime)s | %(levelname)-8s | %(module)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(name: str) -> logging.Logger:
    """Return a configured logger with a rotating file handler.

    The logger writes to ``%APPDATA%/EveJS-Launcher-V2/logs/launcher.log``.
    Multiple calls with the same *name* return the same logger instance
    without adding duplicate handlers.
    """
    logger = logging.getLogger(name)

    # Prevent adding duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    return logger
