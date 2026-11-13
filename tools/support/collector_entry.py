"""Small standalone collector entry; no QApplication, logger or user config."""
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.runtime.roster_diagnostic_collector import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
