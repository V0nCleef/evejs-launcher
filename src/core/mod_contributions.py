"""Key ownership and recoverable file transactions for local mod settings.

Use one store/lock per physical coordination root. In particular, every EveJS
root sharing a physical client must use that client's shared store; an owner's
identity still includes its originating EveJS root, mod path and profile ID.
This is cooperative coordination, not an OS sandbox for executable mods.

Whole-file backups are transaction recovery material only. Normal removal is
computed from individual key contributions and never restores an old file over
unrelated edits. The host must hold the store root's lifecycle lease when using
the *_locked methods; other entry points acquire that lease themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Iterable
import uuid

from .mod_config_documents import DocumentValue, canonical_key, edit_value, read_value, text_regions_overlap, values_equal
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock


MAX_FILE_BYTES = 8 * 1024 * 1024
_FORMATS = {"json": {".json"}, "ini": {".ini"}, "yaml": {".yaml", ".yml"}}


class ContributionError(RuntimeError):
    pass


class ContributionConflict(ContributionError):
    """A selected edit/removal needs an explicit conflict resolution."""


class UnknownOwnershipError(ContributionConflict):
    """No contribution history authorizes destructive removal for this owner."""


class RemovalEditConflict(ContributionConflict):
    """A recorded removal would replace a later manual key edit."""


class RecoveryRequired(ContributionError):
    """Unresolved transaction files were preserved for manual review."""


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _digest(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


def _regular_bytes(path: Path) -> bytes | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or getattr(metadata, "st_file_attributes", 0) & 0x400:
        raise ContributionError(f"Not a private regular file: {path}")
    if metadata.st_size > MAX_FILE_BYTES:
        raise ContributionError(f"Configuration file exceeds the size limit: {path}")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        content = stream.read(MAX_FILE_BYTES + 1)
    after = path.lstat()
    if len(content) > MAX_FILE_BYTES or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (metadata.st_dev, metadata.st_ino, len(content), metadata.st_mtime_ns) or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (metadata.st_dev, metadata.st_ino, len(content), metadata.st_mtime_ns):
        raise ContributionConflict(f"File changed while being read: {path}")
    return content


def _physical_directory(path: str | Path) -> Path:
    root = Path(path).resolve(strict=True)
    if not root.is_dir():
        raise ContributionError(f"Not a directory: {root}")
    return root


def _bounded_path(path: str | Path, root: Path, *, create_parents: bool = False) -> Path:
    """Walk the lexical path before resolving anything below the captured root.

    Missing parents are allowed during planning. Creation is a commit/recovery
    operation only; each new/existing ancestor is checked without following a
    link, including Windows junctions that Path.is_symlink() does not identify.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ContributionError("Configuration target escapes its captured allowed root.") from exc
    if not relative.parts or any(
        part in {".", ".."} or ":" in part or any(ord(char) < 32 for char in part)
        for part in relative.parts
    ):
        raise ContributionError("Configuration target must be an unambiguous relative file path.")
    if _physical_directory(root) != root:
        raise ContributionError("The captured allowed root was redirected.")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if not create_parents:
                break
            try:
                current.mkdir()
            except FileExistsError:
                pass
            metadata = current.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ContributionError(f"Configuration ancestor is linked or is not a directory: {current}")
    return candidate


@dataclass(frozen=True)
class ContributionOwner:
    evejs_root: Path
    mod_relative_path: str
    profile_id: str = ""

    def __post_init__(self):
        root = _physical_directory(self.evejs_root)
        if not isinstance(self.mod_relative_path, str) or not self.mod_relative_path:
            raise ContributionError("A relative mod path is required.")
        relative = PurePosixPath(self.mod_relative_path.replace("\\", "/"))
        if relative.is_absolute() or not relative.parts or any(part in {".", ".."} or ":" in part for part in relative.parts):
            raise ContributionError("Mod identity must be relative to its EveJS root.")
        if not isinstance(self.profile_id, str) or len(self.profile_id) > 512 or any(ord(c) < 32 for c in self.profile_id):
            raise ContributionError("Profile identity must be a bounded opaque string.")
        try:
            (root / str(relative)).resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise ContributionError("Mod identity escapes its EveJS root.") from exc
        object.__setattr__(self, "evejs_root", root)
        object.__setattr__(self, "mod_relative_path", relative.as_posix())

    @property
    def record(self) -> dict:
        return {
            "evejs_root": os.path.normcase(str(self.evejs_root)),
            "mod_relative_path": os.path.normcase(self.mod_relative_path),
            "profile_id": self.profile_id,
        }

    @property
    def id(self) -> str:
        return _digest(_json_bytes(self.record))


@dataclass(frozen=True)
class FileTarget:
    path: Path
    allowed_root: Path
    format: str
    encoding: str | None = None

    @classmethod
    def capture(cls, path: str | Path, allowed_root: str | Path, format: str, *, encoding: str | None = None) -> "FileTarget":
        root = _physical_directory(allowed_root)
        candidate = _bounded_path(path, root)
        if format in {"file", "text"}:
            if (format == "file" and encoding is not None) or candidate.suffix.casefold() in {".json", ".ini", ".yaml", ".yml", ".sqlite", ".sqlite3", ".db"}:
                raise ContributionError("Use structured contributions for configuration files; databases are not overlay targets.")
        elif format not in _FORMATS or candidate.suffix.casefold() not in _FORMATS[format]:
            raise ContributionError("Only explicit JSON, INI or YAML configuration files can be edited.")
        folded = tuple(part.casefold() for part in candidate.parts)
        if any(folded[index:index + 2] == ("_local", "gamestore") for index in range(len(folded) - 1)):
            raise ContributionError("Shared GameStore data is not a mod configuration target.")
        content = _regular_bytes(candidate)
        _bounded_path(candidate, root)
        if content is not None and content.startswith(b"SQLite format 3\0"):
            raise ContributionError("A shared database is not a mod configuration document.")
        return cls(candidate, root, format, encoding)

    @property
    def record(self) -> dict:
        return {"path": str(self.path), "allowed_root": str(self.allowed_root), "format": self.format, "encoding": self.encoding}

    @property
    def id(self) -> str:
        return _digest(os.path.normcase(str(self.path)).encode("utf-8"))


def read_target(target: FileTarget) -> bytes | None:
    """Read a captured target without creating directories, locks or state.

    The host can use this when opening/cancelling a settings dialog. A missing
    private profile directory is represented by None until an actual save.
    """
    if not isinstance(target, FileTarget):
        raise ContributionError("A captured configuration target is required.")
    current = FileTarget.capture(target.path, target.allowed_root, target.format, encoding=target.encoding)
    if current != target:
        raise ContributionError("Configuration target changed after capture.")
    content = _regular_bytes(target.path)
    _bounded_path(target.path, target.allowed_root)
    return content


@dataclass(frozen=True)
class KeyEdit:
    target: FileTarget
    key: tuple[str, ...]
    value: object = None
    allow_override: bool = False
    delete: bool = False
    accept_current: bool = False


@dataclass(frozen=True)
class FileChange:
    target: FileTarget
    before: bytes | None
    after: bytes | None


@dataclass(frozen=True)
class ContributionPlan:
    storage_root: Path
    files: tuple[FileChange, ...]
    index_before: bytes | None
    index_after: bytes | None

    @property
    def changed_paths(self) -> tuple[Path, ...]:
        return tuple(item.target.path for item in self.files if item.before != item.after)

    @property
    def is_noop(self) -> bool:
        return not self.changed_paths and self.index_before == self.index_after


@dataclass(frozen=True)
class ConfigurationReview:
    preserve: ContributionPlan
    restore: ContributionPlan
    owners: tuple[ContributionOwner, ...]
    kind: str = "removal"


class RemovalReviewRequired(ContributionConflict):
    def __init__(self, review: ConfigurationReview):
        super().__init__("This mod's proposed file changes need review." if review.kind == "helper"
                         else "An unrecorded edit needs review before removing this mod's shared settings.")
        self.review = review


@dataclass(frozen=True)
class CommitResult:
    transaction_id: str | None
    changed_paths: tuple[Path, ...]

    @property
    def is_noop(self) -> bool:
        return self.transaction_id is None


@dataclass(frozen=True)
class RecoveryReport:
    recovered: tuple[str, ...] = ()
    unresolved: tuple[Path, ...] = ()


def _state(value: DocumentValue) -> dict:
    return {"present": value.present, "value": value.value, "raw": value.raw}


def _value(state: dict) -> DocumentValue:
    if not isinstance(state, dict) or set(state) != {"present", "value", "raw"} or type(state["present"]) is not bool or (state["raw"] is not None and not isinstance(state["raw"], str)):
        raise ContributionError("Invalid stored contribution value.")
    _json_bytes(state)
    return DocumentValue(**state)


class ContributionStore:
    def __init__(self, storage_root: str | Path, *, allowed_roots: Iterable[str | Path] | None = None):
        self.root = _physical_directory(storage_root)
        self.allowed_roots = frozenset(_physical_directory(path) for path in (allowed_roots if allowed_roots is not None else (self.root,)))
        self.directory = self.root / "_local" / "launcher-mods"
        self.index_path = self.directory / "contributions.json"
        self.transactions = self.directory / "transactions"

    def _internal(self, path: Path) -> Path:
        try:
            path.relative_to(self.directory)
            if path.resolve(strict=False) != path:
                raise ValueError
        except ValueError as exc:
            raise ContributionError("Contribution state path was redirected.") from exc
        return path

    def _target(self, target: FileTarget) -> FileTarget:
        if not isinstance(target, FileTarget) or target.allowed_root not in self.allowed_roots:
            raise ContributionError("Target is outside this operation's captured allowed roots.")
        current = FileTarget.capture(target.path, target.allowed_root, target.format, encoding=target.encoding)
        if current != target or target.path.is_relative_to(self.directory):
            raise ContributionError("Configuration target was redirected or addresses contribution state.")
        return current

    def _load(self) -> tuple[dict, bytes | None]:
        content = _regular_bytes(self._internal(self.index_path))
        if content is None:
            return {"schema": 1, "owners": {}, "documents": {}}, None
        state = self._read_json(content)
        if set(state) != {"schema", "owners", "documents"} or state["schema"] != 1 or not isinstance(state["owners"], dict) or not isinstance(state["documents"], dict):
            raise ContributionError("Unsupported or malformed contribution registry.")
        return state, content

    @staticmethod
    def _read_json(content: bytes) -> dict:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ContributionError("Duplicate journal/registry JSON key.")
                result[key] = value
            return result
        try:
            result = json.loads(content.decode("utf-8"), object_pairs_hook=unique, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (UnicodeError, ValueError) as exc:
            raise ContributionError("Contribution journal/registry is not valid JSON.") from exc

    def _check_recovery(self, paths: Iterable[Path]) -> None:
        report = self.recover_locked()
        blocked = set(report.unresolved)
        if self.index_path in blocked or blocked.intersection(paths):
            raise RecoveryRequired("A previous transaction still needs review for the selected files.")

    def plan_edits(self, owner: ContributionOwner, edits: Iterable[KeyEdit]) -> ContributionPlan:
        with acquire_mod_lifecycle_lock(self.root):
            return self.plan_edits_locked(owner, edits)

    def plan_edits_locked(self, owner: ContributionOwner, edits: Iterable[KeyEdit]) -> ContributionPlan:
        return self.plan_batch_locked(((owner, edits),))

    def plan_batch(self, requests: Iterable[tuple[ContributionOwner, Iterable[KeyEdit]]]) -> ContributionPlan:
        """Prepare several owners' key edits as one atomic recovery unit."""
        with acquire_mod_lifecycle_lock(self.root):
            return self.plan_batch_locked(requests)

    def plan_batch_locked(self, requests: Iterable[tuple[ContributionOwner, Iterable[KeyEdit]]]) -> ContributionPlan:
        requests = tuple((owner, tuple(edits)) for owner, edits in requests)
        if any(not isinstance(edit, KeyEdit) for _owner, edits in requests for edit in edits):
            raise ContributionError("Only declared key edits can enter a contribution batch.")
        self._check_recovery(edit.target.path for _owner, edits in requests for edit in edits)
        state, before_index = self._load()
        original_state = _json_bytes(state)
        files: dict[str, FileChange] = {}
        for owner, edits in requests:
            self._apply_edits(owner, edits, state, files)
        after_index = before_index if _json_bytes(state) == original_state else _json_bytes(state)
        return ContributionPlan(self.root, tuple(files.values()), before_index, after_index)

    def _apply_edits(self, owner: ContributionOwner, edits: tuple[KeyEdit, ...], state: dict, files: dict[str, FileChange]) -> None:
        if not isinstance(owner, ContributionOwner):
            raise ContributionError("An explicit contribution owner is required.")
        seen = set()
        for edit in edits:
            if not isinstance(edit, KeyEdit) or any(type(flag) is not bool for flag in (edit.allow_override, edit.delete, edit.accept_current)):
                raise ContributionError("Key edits require explicit Boolean override/delete flags.")
            target = self._target(edit.target)
            key = canonical_key(target.format, edit.key)
            key_id = json.dumps(key, ensure_ascii=False)
            identity = (target.id, key_id)
            if identity in seen:
                raise ContributionConflict("A transaction edits the same key more than once.")
            seen.add(identity)
            if target.id not in files:
                content = _regular_bytes(target.path)
                files[target.id] = FileChange(target, content, content)
            file = files[target.id]
            document = state["documents"].setdefault(target.id, {"target": target.record, "keys": {}})
            if document["target"] != target.record or not isinstance(document["keys"], dict):
                raise ContributionError("Stored target contract does not match the captured target.")
            for other in document["keys"].values():
                other_key = canonical_key(target.format, other["key"])
                if target.format == "text" and other_key != key and text_regions_overlap(file.after, key, other_key, encoding=target.encoding):
                    raise ContributionConflict("Text contributions overlap or change another region's anchors.")
                if other_key != key and (other_key[:len(key)] == key or key[:len(other_key)] == other_key):
                    raise ContributionConflict("Ancestor and descendant key contributions overlap.")
            current = read_value(file.after, target.format, key, encoding=target.encoding)
            after = file.after if edit.delete and not current.present else edit_value(file.after, target.format, tuple(edit.key), edit.value, delete=edit.delete, encoding=target.encoding)
            desired = read_value(after, target.format, key, encoding=target.encoding)
            if target.format == "text":
                for other in document["keys"].values():
                    read_value(after, "text", tuple(other["key"]), encoding=target.encoding)
            entry = document["keys"].setdefault(key_id, {"key": list(edit.key), "baseline": _state(current), "layers": []})
            layers = entry["layers"]
            if not isinstance(layers, list):
                raise ContributionError("Invalid contribution layers.")
            _value(entry["baseline"])
            existing = next((layer for layer in layers if layer["owner"] == owner.id), None)
            if existing is not None and values_equal(_value(existing["state"]), desired) and values_equal(current, _value(layers[-1]["state"])) and not edit.allow_override:
                # Repeating an unchanged request does not promote an owner
                # whose contribution is currently below explicit precedence.
                continue
            if layers and not values_equal(current, _value(layers[-1]["state"])):
                if (edit.accept_current and not edit.delete and current.present
                        and layers[-1]["owner"] == owner.id and values_equal(current, desired)):
                    # A private-profile helper can retain a runtime-written
                    # value without writing the file or claiming it was the
                    # original contribution. Keep it through later removal.
                    layers[:] = [layer for layer in layers if layer["owner"] != owner.id]
                    layers.append({"owner": None, "state": _state(current)})
                    layers.append({"owner": owner.id, "state": _state(current)})
                    continue
                if not edit.allow_override:
                    raise ContributionConflict(f"The value of {key!r} was edited outside its recorded contributions.")
                # Retain this observed manual value as an unowned layer; do not
                # pretend it was supplied by any particular mod.
                layers.append({"owner": None, "state": _state(current)})
            if layers and layers[-1]["owner"] != owner.id and not values_equal(desired, _value(layers[-1]["state"])) and not edit.allow_override:
                raise ContributionConflict(f"Another contribution owns {key!r}; choose precedence explicitly.")
            if existing is None or not values_equal(_value(existing["state"]), desired):
                layers[:] = [layer for layer in layers if layer["owner"] != owner.id]
                layers.append({"owner": owner.id, "state": _state(desired)})
            elif layers[-1]["owner"] != owner.id and edit.allow_override:
                layers.remove(existing)
                layers.append(existing)
            files[target.id] = FileChange(target, file.before, after)
            state["owners"][owner.id] = owner.record

    def plan_remove(self, owner: ContributionOwner) -> ContributionPlan:
        with acquire_mod_lifecycle_lock(self.root):
            return self.plan_remove_locked(owner)

    def plan_remove_locked(self, owner: ContributionOwner) -> ContributionPlan:
        return self.plan_remove_batch_locked((owner,))

    def owners_for_mod(self, owner: ContributionOwner) -> tuple[ContributionOwner, ...]:
        """Read recorded global/profile owners for this exact physical mod.

        No lock, directories or recovery state are created. Missing history is
        an empty result; this never invents ownership from files on disk.
        """
        if not isinstance(owner, ContributionOwner):
            raise ContributionError("An explicit mod identity is required.")
        state, _content = self._load()
        identity = owner.record
        result = []
        for owner_id, record in state["owners"].items():
            if not isinstance(record, dict) or any(record.get(name) != identity[name] for name in ("evejs_root", "mod_relative_path")):
                continue
            if set(record) != {"evejs_root", "mod_relative_path", "profile_id"}:
                raise ContributionError("The selected mod has malformed ownership history.")
            recorded = ContributionOwner(Path(record["evejs_root"]), record["mod_relative_path"], record["profile_id"])
            if recorded.id != owner_id:
                raise ContributionError("The selected mod's owner identity does not match its record.")
            result.append(recorded)
        return tuple(sorted(result, key=lambda value: value.id))

    def owners_for_path(self, path: str | Path) -> tuple[ContributionOwner, ...]:
        """Read every recorded key contributor to one physical configuration file."""
        needle = os.path.normcase(str(Path(path).resolve(strict=False)))
        state, _content = self._load()
        owner_ids = set()
        for document in state["documents"].values():
            if not isinstance(document, dict) or not isinstance(document.get("target"), dict):
                raise ContributionError("A recorded contribution target is malformed.")
            target = document["target"]
            if not isinstance(target.get("path"), str):
                raise ContributionError("A recorded contribution target path is malformed.")
            if os.path.normcase(str(Path(target["path"]).resolve(strict=False))) != needle:
                continue
            try:
                self._target(FileTarget(**{**target, "path": Path(target["path"]),
                    "allowed_root": Path(target["allowed_root"])}))
                for entry in document["keys"].values():
                    owner_ids.update(layer["owner"] for layer in entry["layers"])
            except (KeyError, TypeError, AttributeError) as exc:
                raise ContributionError("The file's recorded contribution layers are malformed.") from exc
        result = []
        for owner_id in owner_ids:
            record = state["owners"].get(owner_id)
            if not isinstance(record, dict) or set(record) != {"evejs_root", "mod_relative_path", "profile_id"}:
                raise ContributionError("The file has malformed ownership history.")
            recorded = ContributionOwner(Path(record["evejs_root"]), record["mod_relative_path"], record["profile_id"])
            if recorded.id != owner_id:
                raise ContributionError("The file's contributor identity does not match its record.")
            result.append(recorded)
        return tuple(sorted(result, key=lambda value: value.id))

    def plan_remove_batch(self, owners: Iterable[ContributionOwner], *, preserve_roots: Iterable[str | Path] = ()) -> ContributionPlan:
        with acquire_mod_lifecycle_lock(self.root):
            return self.plan_remove_batch_locked(owners, preserve_roots=preserve_roots)

    def review_remove_locked(self, owners: Iterable[ContributionOwner], *, preserve_roots: Iterable[str | Path] = ()) -> ConfigurationReview:
        """Prepare both choices without writing; committing rechecks every byte."""
        owners, preserve_roots = tuple(owners), tuple(preserve_roots)
        preserve = self.plan_remove_batch_locked(owners, preserve_roots=preserve_roots, local_edits="preserve")
        restore = self.plan_remove_batch_locked(owners, preserve_roots=preserve_roots, local_edits="restore")
        if preserve.index_before != restore.index_before or tuple((f.target, f.before) for f in preserve.files) != tuple((f.target, f.before) for f in restore.files):
            raise ContributionConflict("Files changed while preparing the conflict review. Refresh and retry.")
        affected = tuple(dict.fromkeys(owner for file in preserve.files for owner in self.owners_for_path(file.target.path)))
        return ConfigurationReview(preserve, restore, affected)

    def plan_remove_batch_locked(self, owners: Iterable[ContributionOwner], *, preserve_roots: Iterable[str | Path] = (), local_edits: str = "reject") -> ContributionPlan:
        if local_edits not in {"reject", "preserve", "restore"}:
            raise ValueError("Unknown local-edit resolution.")
        owners = tuple(owners)
        if any(not isinstance(owner, ContributionOwner) for owner in owners):
            raise ContributionError("Removal requires explicit recorded owners.")
        owner_ids = {owner.id for owner in owners}
        preserved = []
        for value in preserve_roots:
            path = Path(value)
            anchor = next((root for root in self.allowed_roots if path.is_relative_to(root)), None)
            if anchor is None:
                raise ContributionError("A preserved directory is outside the captured allowed roots.")
            # Include the requested directory itself in the no-follow walk.
            _bounded_path(path / ".preserve-scope.json", anchor)
            preserved.append(path)
        self._check_recovery(())
        state, before_index = self._load()
        if owner_ids - state["owners"].keys():
            raise UnknownOwnershipError("No recorded key contributions exist for this owner; existing files were preserved.")
        selected = [document for document in state["documents"].values()
            if not any(Path(document["target"]["path"]).is_relative_to(path) for path in preserved)
            and any(any(layer["owner"] in owner_ids for layer in entry["layers"]) for entry in document["keys"].values())]
        self._check_recovery(Path(document["target"]["path"]) for document in selected)
        # Recovery may have restored the registry, so always reload afterwards.
        state, before_index = self._load()
        original_state = _json_bytes(state)
        files = []
        for document in state["documents"].values():
            if any(Path(document["target"]["path"]).is_relative_to(path) for path in preserved):
                continue
            entries = document["keys"]
            owned = [key_id for key_id, entry in entries.items() if any(layer["owner"] in owner_ids for layer in entry["layers"])]
            if not owned:
                continue
            target = self._target(FileTarget(**{**document["target"], "path": Path(document["target"]["path"]), "allowed_root": Path(document["target"]["allowed_root"])}))
            before = after = _regular_bytes(target.path)
            for key_id in owned:
                entry = entries[key_id]
                key = tuple(entry["key"])
                current = read_value(after, target.format, key, encoding=target.encoding)
                remaining = [layer for layer in entry["layers"] if layer["owner"] not in owner_ids]
                replacement = _value(remaining[-1]["state"] if remaining else entry["baseline"])
                if entry["layers"][-1]["owner"] in owner_ids:
                    if not values_equal(current, _value(entry["layers"][-1]["state"])) and not values_equal(current, replacement):
                        if local_edits == "reject":
                            raise RemovalEditConflict(f"Removal would overwrite an unrecorded edit to {key!r} in {target.path}.")
                        if local_edits == "preserve":
                            # Keep the observed user value as an unowned layer,
                            # so later removal of a neighbor also preserves it.
                            remaining.append({"owner": None, "state": _state(current)})
                            replacement = current
                    if current.present or replacement.present:
                        after = edit_value(after, target.format, key, replacement.value, delete=not replacement.present, encoding=target.encoding, raw_value=replacement.raw)
                if remaining:
                    entry["layers"] = remaining
                else:
                    del entries[key_id]
            files.append(FileChange(target, before, after))
        after_index = before_index if _json_bytes(state) == original_state else _json_bytes(state)
        return ContributionPlan(self.root, tuple(files), before_index, after_index)

    def commit(self, plan: ContributionPlan) -> CommitResult:
        with acquire_mod_lifecycle_lock(self.root):
            return self.commit_locked(plan)

    def commit_locked(self, plan: ContributionPlan) -> CommitResult:
        if not isinstance(plan, ContributionPlan) or plan.storage_root != self.root:
            raise ContributionError("The plan belongs to a different coordination root.")
        self._check_recovery(item.target.path for item in plan.files)
        if _regular_bytes(self._internal(self.index_path)) != plan.index_before:
            raise ContributionConflict("Contribution ownership changed after planning.")
        for item in plan.files:
            self._target(item.target)
            if _regular_bytes(item.target.path) != item.before:
                raise ContributionConflict(f"File changed after planning: {item.target.path}")
        if plan.is_noop:
            return CommitResult(None, ())
        self._internal(self.transactions).mkdir(parents=True, exist_ok=True)
        transaction_id = uuid.uuid4().hex
        folder = self._internal(self.transactions / transaction_id)
        folder.mkdir()
        records = []
        changes = [(item.target.path, item.before, item.after, item.target.record) for item in plan.files if item.before != item.after]
        if plan.index_before != plan.index_after:
            changes.append((self.index_path, plan.index_before, plan.index_after, None))
        for index, (path, before, after, target) in enumerate(changes):
            for label, content in (("before", before), ("after", after)):
                if content is not None:
                    self._write_bytes(folder / f"{index}.{label}", content)
            records.append({"path": str(path), "target": target, "before": _digest(before), "after": _digest(after)})
        journal = {"schema": 1, "id": transaction_id, "state": "prepared", "records": records, "pending": list(range(len(records)))}
        self._save_journal(folder, journal)
        try:
            for index, (path, before, after, target) in enumerate(changes):
                self._record_path(records[index])
                if _regular_bytes(path) != before:
                    raise ContributionConflict(f"File changed during commit: {path}")
                if after is not None and target is not None:
                    _bounded_path(path, Path(target["allowed_root"]), create_parents=True)
                    if _regular_bytes(path) != before:
                        raise ContributionConflict(f"File changed during directory creation: {path}")
                self._replace_file(path, after)
            journal["state"] = "committed"
            self._save_journal(folder, journal)
        except Exception:
            unresolved = self._rollback(folder, journal)
            if unresolved:
                raise RecoveryRequired("Commit failed; changed files were preserved for recovery review.")
            raise
        return CommitResult(transaction_id, plan.changed_paths)

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        if len(content) > MAX_FILE_BYTES:
            raise ContributionError("Staged configuration exceeds the size limit.")
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def _replace_file(self, path: Path, content: bytes | None) -> None:
        """Atomic replacement within the target volume; an injection seam for tests."""
        if content is None:
            path.unlink(missing_ok=True)
            return
        temporary = path.with_name(f".{path.name}.launcher-{uuid.uuid4().hex}.tmp")
        try:
            self._write_bytes(temporary, content)
            if path.exists():
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _save_journal(self, folder: Path, journal: dict) -> None:
        self._replace_file(self._internal(folder / "journal.json"), _json_bytes(journal))

    def _record_path(self, record: dict) -> Path:
        self._record_shape(record)
        path = Path(record["path"])
        if record["target"] is None:
            if path != self.index_path:
                raise ContributionError("Recovery record redirects the contribution registry.")
            return self._internal(path)
        value = record["target"]
        target = FileTarget(Path(value["path"]), Path(value["allowed_root"]), value["format"], value.get("encoding"))
        self._target(target)
        if path != target.path:
            raise ContributionError("Recovery target and path disagree.")
        return path

    @staticmethod
    def _record_shape(record: dict) -> None:
        if not isinstance(record, dict) or set(record) != {"path", "target", "before", "after"} or not isinstance(record["path"], str):
            raise ContributionError("Invalid recovery record.")
        for label in ("before", "after"):
            value = record[label]
            if value is not None and (not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value)):
                raise ContributionError("Invalid recovery content identity.")
        target = record["target"]
        if target is not None and (
            not isinstance(target, dict) or set(target) != {"path", "allowed_root", "format", "encoding"}
            or any(not isinstance(target[name], str) for name in ("path", "allowed_root", "format"))
            or (target["encoding"] is not None and not isinstance(target["encoding"], str))
        ):
            raise ContributionError("Invalid recovery target contract.")

    def _staged(self, folder: Path, index: int, label: str, expected: str | None) -> bytes | None:
        content = _regular_bytes(self._internal(folder / f"{index}.{label}"))
        if _digest(content) != expected:
            raise ContributionError("Recovery material does not match its recorded identity.")
        return content

    def _rollback(self, folder: Path, journal: dict) -> tuple[Path, ...]:
        # Validate every pending record before the first rollback mutation.
        for index in journal["pending"]:
            self._record_shape(journal["records"][index])
        journal["state"] = "recovering"
        self._save_journal(folder, journal)
        unresolved = []
        for index in reversed(tuple(journal["pending"])):
            record = journal["records"][index]
            path = Path(record["path"])
            try:
                self._record_path(record)
                before = self._staged(folder, index, "before", record["before"])
                after = self._staged(folder, index, "after", record["after"])
                current = _regular_bytes(path)
                if current == before:
                    pass
                elif current == after:
                    if before is not None and record["target"] is not None:
                        _bounded_path(path, Path(record["target"]["allowed_root"]), create_parents=True)
                    self._replace_file(path, before)
                else:
                    raise ContributionConflict("Recovery found an intervening file edit.")
            except (OSError, ContributionError):
                unresolved.append(path)
                continue
            journal["pending"].remove(index)
            self._save_journal(folder, journal)
        journal["state"] = "needs_review" if unresolved else "rolled_back"
        self._save_journal(folder, journal)
        return tuple(unresolved)

    def recover(self) -> RecoveryReport:
        with acquire_mod_lifecycle_lock(self.root):
            return self.recover_locked()

    def recover_locked(self) -> RecoveryReport:
        if not self._internal(self.transactions).exists():
            return RecoveryReport()
        recovered, unresolved = [], []
        for folder in sorted(self.transactions.iterdir()):
            self._internal(folder)
            if len(folder.name) != 32 or any(char not in "0123456789abcdef" for char in folder.name) or not folder.is_dir():
                raise ContributionError("Unexpected transaction directory.")
            content = _regular_bytes(self._internal(folder / "journal.json"))
            if content is None:
                # Crash during staging: no prepared journal means no target
                # replacement was permitted. Leave this recovery material alone.
                continue
            journal = self._read_json(content)
            if set(journal) != {"schema", "id", "state", "records", "pending"} or journal["schema"] != 1 or journal["id"] != folder.name or not isinstance(journal["records"], list) or not isinstance(journal["pending"], list) or any(type(i) is not int or not 0 <= i < len(journal["records"]) for i in journal["pending"]) or len(set(journal["pending"])) != len(journal["pending"]):
                raise ContributionError("Invalid contribution recovery journal.")
            if journal["state"] in {"committed", "rolled_back"}:
                continue
            if journal["state"] not in {"prepared", "recovering", "needs_review"}:
                raise ContributionError("Unknown contribution transaction state.")
            remaining = self._rollback(folder, journal)
            unresolved.extend(remaining)
            if not remaining:
                recovered.append(folder.name)
        return RecoveryReport(tuple(recovered), tuple(dict.fromkeys(unresolved)))
