"""QThread workers for database operations.

Runs SQLite queries off the GUI thread so the launcher UI stays responsive
while scanning large gamestore.sqlite files.
"""
from PyQt6.QtCore import QThread, pyqtSignal

from ..core import db


class AccountLoader(QThread):
    """Load all EveJS accounts + characters in a background thread.

    Signals:
        finished(list): Emitted with list[Account] on success.
        progress(int):  Emitted with percentage (0-100) at coarse milestones.
        error(str):     Emitted with human-readable message on failure.
    """

    finished = pyqtSignal(list)
    progress = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, evejs_root: str, parent=None):
        super().__init__(parent)
        self._evejs_root = evejs_root

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            self.progress.emit(10)
            accounts = db.load_accounts(self._evejs_root)
            self.progress.emit(90)
            self.finished.emit(accounts)
            self.progress.emit(100)
        except Exception as exc:  # pragma: no cover - defensive
            self.error.emit(f"Failed to load accounts: {exc}")


class CharacterDetailLoader(QThread):
    """Load the full JSON detail blob for a single character.

    Signals:
        finished(dict): Emitted with the character dict (empty dict on miss).
        error(str):     Emitted with human-readable message on failure.
    """

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, evejs_root: str, char_id: int, parent=None):
        super().__init__(parent)
        self._evejs_root = evejs_root
        self._char_id = int(char_id)

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            detail = db.get_character_detail(self._evejs_root, self._char_id)
            self.finished.emit(detail or {})
        except Exception as exc:  # pragma: no cover - defensive
            self.error.emit(
                f"Failed to load character {self._char_id}: {exc}"
            )
