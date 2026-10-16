"""User-selected Windows batch scripts, independent of the server processes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from src import config

log = logging.getLogger(__name__)
_PREFIX = "_EVEJS_STARTUP_SCRIPT_"
_ENTRY_FIELDS = {"id", "name", "filename", "arguments", "auto_start"}


class ScriptError(ValueError):
    """Invalid entry or unreadable persistent script configuration."""


@dataclass(frozen=True)
class ScriptEntry:
    id: str
    name: str
    filename: str
    arguments: str = ""
    auto_start: bool = True

    @classmethod
    def create(cls, name: str, filename: str, *, arguments: str = "", auto_start: bool = True) -> ScriptEntry:
        entry = cls(uuid4().hex, name.strip(), filename.strip(), arguments.strip(), auto_start)
        validate_entry(entry)
        return entry


def parse_arguments(text: str) -> tuple[str, ...]:
    """Use Windows argument rules; never interpret this field as shell code."""
    if not isinstance(text, str) or len(text) > 8192 or any(c in text for c in "\r\n\x00"):
        raise ScriptError("Invalid script arguments.")
    if not text.strip():
        return ()
    # CommandLineToArgvW treats argv[0] specially, hence the dummy executable.
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW("script " + text, ctypes.byref(count))
    if not pointer:
        raise ScriptError("Invalid script arguments.")
    try:
        values = tuple(pointer[index] for index in range(1, count.value))
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = (wintypes.HLOCAL,)
        kernel.LocalFree.restype = wintypes.HLOCAL
        kernel.LocalFree(ctypes.cast(pointer, wintypes.HLOCAL))
    if len(values) > 64 or any('"' in value for value in values):
        raise ScriptError("Arguments cannot contain literal double quotes.")
    return values


def validate_entry(entry: ScriptEntry) -> None:
    if not isinstance(entry.id, str) or not re.fullmatch(r"[a-f0-9]{32}", entry.id):
        raise ScriptError("Invalid script identifier.")
    if not isinstance(entry.name, str) or not entry.name.strip() or len(entry.name) > 128 or any(c in entry.name for c in "\r\n\x00"):
        raise ScriptError("Enter a script name.")
    if (not isinstance(entry.filename, str) or len(entry.filename) > 4096
            or any(c in entry.filename for c in '\r\n\x00"')
            or not Path(entry.filename).is_absolute() or Path(entry.filename).suffix.casefold() != ".bat"):
        raise ScriptError("Choose an absolute .bat file path.")
    if not isinstance(entry.auto_start, bool):
        raise ScriptError("Invalid automatic startup setting.")
    parse_arguments(entry.arguments)


def root_key(root: str | Path) -> str:
    if not str(root).strip() or not Path(root).is_absolute():
        raise ScriptError("Select an EveJS folder in Settings first.")
    return os.path.normcase(str(Path(root).resolve()))


class ScriptStore:
    """Atomically saved entries keyed by the physical EveJS installation."""

    def __init__(self, filename: Path | None = None):
        self.filename = filename or config.CONFIG_DIR / "startup_scripts.json"

    def _load(self) -> dict:
        if not self.filename.exists():
            return {"schemaVersion": 1, "installations": {}}
        try:
            if self.filename.stat().st_size > 1024 * 1024:
                raise ValueError("too large")
            data = json.loads(self.filename.read_text(encoding="utf-8-sig"))
            if (not isinstance(data, dict) or set(data) != {"schemaVersion", "installations"}
                    or data["schemaVersion"] != 1 or not isinstance(data["installations"], dict)):
                raise ValueError("unsupported format")
            for root, rows in data["installations"].items():
                if not isinstance(root, str) or not isinstance(rows, list) or len(rows) > 64:
                    raise ValueError("invalid installation")
                ids = set()
                for row in rows:
                    if not isinstance(row, dict) or set(row) != _ENTRY_FIELDS:
                        raise ValueError("invalid entry")
                    item = ScriptEntry(**row)
                    validate_entry(item)
                    if item.id in ids:
                        raise ValueError("duplicate identifier")
                    ids.add(item.id)
            return data
        except (OSError, ValueError, TypeError) as exc:
            raise ScriptError("Startup script settings could not be read.") from exc

    def list(self, root: str | Path) -> list[ScriptEntry]:
        key = root_key(root)
        return [ScriptEntry(**row) for row in self._load()["installations"].get(key, [])]

    def _write(self, data: dict) -> None:
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if len(content.encode("utf-8")) > 1024 * 1024:
            raise ScriptError("Startup script settings could not be saved.")
        self.filename.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=self.filename.parent, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.filename)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, root: str | Path, entry: ScriptEntry) -> None:
        validate_entry(entry)
        key = root_key(root)
        data = self._load()
        rows = data["installations"].setdefault(key, [])
        for index, row in enumerate(rows):
            if row["id"] == entry.id:
                rows[index] = asdict(entry)
                break
        else:
            if len(rows) >= 64:
                raise ScriptError("An installation can have at most 64 startup scripts.")
            rows.append(asdict(entry))
        self._write(data)

    def remove(self, root: str | Path, identifier: str) -> None:
        key = root_key(root)
        data = self._load()
        data["installations"][key] = [row for row in data["installations"].get(key, []) if row["id"] != identifier]
        self._write(data)


def launch_batch(entry: ScriptEntry) -> subprocess.Popen:
    validate_entry(entry)
    filename = Path(entry.filename)
    if not filename.is_file():
        raise FileNotFoundError(entry.filename)
    arguments = parse_arguments(entry.arguments)
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith(_PREFIX)}
    environment[_PREFIX + "FILE"] = str(filename)
    # Each value is expanded exactly once inside its own quotes. No user text
    # enters the command string; /v:off preserves literal exclamation marks.
    command = 'cmd.exe /d /v:off /s /c ""%' + _PREFIX + 'FILE%"'
    for index, value in enumerate(arguments):
        key = _PREFIX + "ARG_" + str(index)
        environment[key] = value
        command += ' "%' + key + '%"'
    command += '"'
    return subprocess.Popen(command, cwd=str(filename.parent), env=environment,
                            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP)


class StartupScriptController(QObject):
    """Track batch wrappers without claiming ownership of their detached tools."""

    changed = pyqtSignal(str)
    error = pyqtSignal(str, str)

    def __init__(self, store: ScriptStore | None = None, *, launcher=launch_batch, parent=None):
        super().__init__(parent)
        self.store = store or ScriptStore()
        self._launcher = launcher
        self._processes: dict[tuple[str, str], subprocess.Popen] = {}
        self._states: dict[tuple[str, str], tuple[str, str]] = {}
        self._sessions: dict[str, object] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.poll)

    def status(self, root, identifier) -> tuple[str, str]:
        return self._states.get((root_key(root), identifier), ("NOT STARTED", ""))

    def run(self, root: str, entry: ScriptEntry) -> bool:
        key = (root_key(root), entry.id)
        process = self._processes.get(key)
        if process is not None and process.poll() is None:
            return False
        try:
            process = self._launcher(entry)
        except (OSError, ValueError, RuntimeError) as exc:
            self._states[key] = ("FAILED", str(exc)[:1024])
            log.warning("Startup batch failed: id=%s error=%s", entry.id, exc)
            self.changed.emit(key[0])
            return False
        self._processes[key] = process
        self._states[key] = ("RUNNING", "")
        self._timer.start()
        self.changed.emit(key[0])
        return True

    @pyqtSlot(str, object)
    def start_for_server(self, root: str, session: object) -> None:
        try:
            key = root_key(root)
        except ScriptError as exc:
            self.error.emit("", str(exc))
            return
        if self._sessions.get(key) == session:
            return
        # Claim first, including failed entries: status polling or a duplicate
        # completion must never turn a script failure into an automatic loop.
        self._sessions[key] = session
        try:
            entries = self.store.list(root)
        except ScriptError as exc:
            log.warning("Startup batch settings unavailable: %s", exc)
            self.error.emit(key, str(exc))
            return
        for entry in entries:
            if entry.auto_start:
                self.run(root, entry)

    @pyqtSlot()
    def poll(self) -> None:
        changed = set()
        for key, process in tuple(self._processes.items()):
            code = process.poll()
            if code is not None:
                del self._processes[key]
                self._states[key] = ("COMPLETED", "") if code == 0 else ("FAILED", str(code))
                changed.add(key[0])
        if not self._processes:
            self._timer.stop()
        for root in changed:
            self.changed.emit(root)
