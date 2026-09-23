"""Root-local ownership, ordering and reversible storage for standalone mods.

These operations never execute package code. A private loader folder can be
imported or explicitly adopted without installer enrollment or approved hashes.
Hashes detect a changed transaction, not whether the author is trusted. Shared
source/client changes belong to their own providers, not folder removal.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from typing import Callable, Iterable, Mapping
import uuid
import zipfile

from .mod_activation_state import (
    fail_mod_activation, mark_mod_activation_pending, prepare_mod_activation,
)
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_manifest import (
    ActivationKind, MANIFEST_FILENAME, MAX_CONFIG_BYTES, Mod, ModManagerError, scan_mods,
    set_mod_active_locked,
)
from .mod_api_manifest import MAX_API_MANIFEST_BYTES, SOURCE_PACKAGE_CAPABILITIES, read_api_manifest


REGISTRY_VERSION = 1
REGISTRY_DIRECTORY = Path("_local") / "launcher-mods"
_LOADERS = ("loader.js", "loader.js.disabled", "loader.js.off", "loader.js.bak")
_RESERVED = {"con", "prn", "aux", "nul", "clock$", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


class LocalModPackageError(ModManagerError):
    """An import, ownership check or reversible transaction could not complete."""


@dataclass(frozen=True)
class PackageLimits:
    max_files: int = 20_000
    max_bytes: int = 2 * 1024 ** 3
    max_file_bytes: int = 512 * 1024 ** 2


@dataclass(frozen=True)
class ImportPreview:
    source: Path
    folder_name: str
    package_prefix: str
    file_count: int
    total_bytes: int
    active: bool
    mod_id: str = ""
    package_kind: str = "loader"
    has_loader: bool = True
    public_descriptor: bool = False


@dataclass(frozen=True)
class CleanupDecision:
    ready: bool
    state: str = "ready"
    message: str = ""
    restart_required: bool = False


@dataclass(frozen=True)
class LocalModRecord:
    record_id: str
    mod_id: str
    relative_path: str
    status: str
    fingerprint: str
    archive_path: str = ""
    cleanup: Mapping[str, object] | None = None
    transaction: Mapping[str, object] | None = None
    package_kind: str = "loader"
    enabled: bool | None = None
    public_descriptor: bool = False


@dataclass(frozen=True)
class RegistryRelocationPreview:
    saved_root: str
    current_root: str
    registry_sha256: str
    record_count: int


@dataclass(frozen=True)
class CleanupRequest:
    root: Path
    action: str
    mod: Mod
    record: LocalModRecord | None
    operation_id: str


CleanupGate = Callable[[CleanupRequest], CleanupDecision]


class LocalModCleanupPending(LocalModPackageError):
    def __init__(self, decision: CleanupDecision) -> None:
        self.decision = decision
        super().__init__(decision.message or "The mod must finish cleanup before this action.")


def _component(value: str) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."} or value[-1:] in {".", " "}
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or c in '<>:"/\\|?*' for c in value)
            or value.split(".", 1)[0].casefold() in _RESERVED):
        raise LocalModPackageError(f"Unsafe package path component: {value!r}.")
    return value


def _relative(value: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise LocalModPackageError("A package path must be text.")
    value = value.replace("\\", "/")
    if value.startswith("/") or not value:
        raise LocalModPackageError("Package paths must be relative.")
    parts = value.rstrip("/").split("/")
    for part in parts:
        _component(part)
    return PurePosixPath(*parts)


def _ordinary(path: Path, *, directory: bool | None = None) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise LocalModPackageError(f"Linked/reparse paths are not standalone package files: {path.name}.")
    if directory is True and not stat.S_ISDIR(info.st_mode):
        raise LocalModPackageError(f"Expected a directory: {path.name}.")
    if directory is False and not stat.S_ISREG(info.st_mode):
        raise LocalModPackageError(f"Expected an ordinary file: {path.name}.")
    if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
        raise LocalModPackageError(f"Unsupported package file: {path.name}.")
    return info


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LocalModPackageError(f"Duplicate JSON key: {key}.")
        result[key] = value
    return result


def _json(content: bytes) -> dict:
    def reject_constant(_value):
        raise LocalModPackageError("Non-finite JSON values are unsupported.")
    try:
        value = json.loads(content, object_pairs_hook=_unique_object, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise LocalModPackageError("The package registry/descriptor is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise LocalModPackageError("Expected a JSON object.")
    return value


def _folder_entries(folder: Path, limits: PackageLimits) -> list[tuple[str, int, bool]]:
    _ordinary(folder, directory=True)
    entries: list[tuple[str, int, bool]] = []
    for current, dirs, files in os.walk(folder, followlinks=False):
        for name in sorted(dirs + files, key=str.casefold):
            path = Path(current) / name
            info = _ordinary(path)
            entries.append((path.relative_to(folder).as_posix(), info.st_size,
                            stat.S_ISDIR(info.st_mode)))
            if len(entries) > limits.max_files * 2:
                raise LocalModPackageError("The package exceeds the import size/count limit.")
    _validate_entries(entries, limits)
    return entries


def _zip_entries(archive: zipfile.ZipFile, limits: PackageLimits) -> list[tuple[str, int, bool]]:
    entries = []
    for member in archive.infolist():
        mode = member.external_attr >> 16
        if (member.flag_bits & 1 or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                or member.external_attr & 0x400):
            raise LocalModPackageError("Encrypted or linked ZIP entries are unsupported.")
        entries.append((member.filename, member.file_size, member.is_dir()))
    _validate_entries(entries, limits)
    return entries


def _validate_entries(entries: list[tuple[str, int, bool]], limits: PackageLimits) -> None:
    paths: dict[str, bool] = {}
    spellings: dict[str, str] = {}
    total = count = 0
    for name, size, directory in entries:
        relative = _relative(name)
        key = str(relative).casefold()
        if key in paths:
            raise LocalModPackageError(f"Duplicate/case-colliding package path: {name}.")
        paths[key] = directory
        for part in (relative, *relative.parents):
            spelling = str(part)
            previous = spellings.setdefault(spelling.casefold(), spelling)
            if previous != spelling:
                raise LocalModPackageError("The package contains case-colliding directory names.")
        if not directory:
            count += 1
            total += size
            if size > limits.max_file_bytes:
                raise LocalModPackageError("A package file exceeds the import size limit.")
    for key in paths:
        for parent in PurePosixPath(key).parents:
            if str(parent) in paths and not paths[str(parent)]:
                raise LocalModPackageError("A package file is also used as a directory.")
    if count > limits.max_files or len(entries) > limits.max_files * 2 or total > limits.max_bytes:
        raise LocalModPackageError("The package exceeds the import size/count limit.")


def _package_prefix(entries: list[tuple[str, int, bool]]) -> str:
    files = [str(_relative(name)) for name, _, directory in entries if not directory]
    prefixes = {str(PurePosixPath(name).parent) for name in files
                if PurePosixPath(name).name in (*_LOADERS, MANIFEST_FILENAME)}
    if "." in prefixes:
        return ""
    candidates = [prefix for prefix in prefixes
                  if all(name.startswith(prefix + "/") for name in files)]
    if len(candidates) != 1:
        raise LocalModPackageError("Select one standalone mod folder/ZIP containing a loader or public package descriptor.")
    return candidates[0]


def _fingerprint(folder: Path, limits: PackageLimits) -> str:
    digest = hashlib.sha256()
    for name, _, directory in sorted(_folder_entries(folder, limits)):
        digest.update((name + ("/" if directory else "") + "\0").encode("utf-8"))
        if not directory:
            path = folder / name
            before = _ordinary(path, directory=False)
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            after = _ordinary(path, directory=False)
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise LocalModPackageError("A package file changed during inspection; retry the operation.")
    return digest.hexdigest()


class LocalModPackages:
    """A captured physical root. UI callers must run mutations off the GUI thread.

    The caller coordinates running services and supplies any declared cleanup
    capability. No cleanup helper is inferred or executed from a file name.
    """

    def __init__(self, root: str | Path, *, limits: PackageLimits | None = None) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise LocalModPackageError("The EveJS root is not a directory.")
        self.limits = limits or PackageLimits()

    @property
    def registry_path(self) -> Path:
        return self.root / REGISTRY_DIRECTORY / "registry.json"

    def _path(self, relative: str | Path) -> Path:
        parts = _relative(str(relative).replace(os.sep, "/")).parts
        path = self.root
        for part in parts:
            path = path / part
            if os.path.lexists(path):
                _ordinary(path)
        try:
            path.resolve().relative_to(self.root)
        except ValueError as exc:
            raise LocalModPackageError("The path escapes the selected EveJS root.") from exc
        return path

    def _read_registry(self, *, allow_foreign_root: bool) -> tuple[dict, bool, bytes | None]:
        path = self._path(REGISTRY_DIRECTORY / "registry.json")
        if not path.exists():
            return ({"schemaVersion": REGISTRY_VERSION, "root": str(self.root), "order": [], "records": {}}, True, None)
        _ordinary(path, directory=False)
        if path.stat().st_size > 8 * 1024 ** 2:
            raise LocalModPackageError("The local mod registry is too large.")
        content = path.read_bytes()
        if len(content) > 8 * 1024 ** 2:
            raise LocalModPackageError("The local mod registry is too large.")
        value = _json(content)
        if (set(value) != {"schemaVersion", "root", "order", "records"}
                or type(value["schemaVersion"]) is not int or value["schemaVersion"] != REGISTRY_VERSION
                or not isinstance(value["root"], str) or not value["root"]
                or not Path(value["root"]).is_absolute()
                or not isinstance(value["order"], list) or not isinstance(value["records"], dict)):
            raise LocalModPackageError("The local mod registry has an unsupported schema or invalid root metadata.")
        owns_root = os.path.normcase(value["root"]) == os.path.normcase(str(self.root))
        if not owns_root and not allow_foreign_root:
            raise LocalModPackageError("The local mod registry belongs to another EveJS root.")
        if any(not isinstance(key, str) for key in value["order"]) or len(set(value["order"])) != len(value["order"]):
            raise LocalModPackageError("The saved mod order is invalid.")
        installed = set()
        for key, row in value["records"].items():
            record = self._record(key, row)
            if record.status == "installed":
                identity = record.relative_path.casefold()
                if identity in installed:
                    raise LocalModPackageError("More than one record claims the same installed folder.")
                installed.add(identity)
        return value, owns_root, content

    def _load(self) -> dict:
        """Load registry metadata only when it belongs to this exact root."""
        return self._read_registry(allow_foreign_root=False)[0]

    def _record(self, key: str, row: dict) -> LocalModRecord:
        try:
            record = LocalModRecord(**row)
            if record.record_id != key or len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
                raise ValueError("Invalid record identity")
            relative = _relative(record.relative_path)
            if len(relative.parts) != 2 or relative.parts[0] != "mods":
                raise ValueError("Invalid private folder")
            if not isinstance(record.mod_id, str) or not record.mod_id:
                raise ValueError("Invalid mod identity")
            if record.package_kind not in {"loader", "settings", "client-package", "source-integrated"}:
                raise ValueError("Invalid package kind")
            if record.enabled is not None and type(record.enabled) is not bool:
                raise ValueError("Invalid configured package state")
            if type(record.public_descriptor) is not bool:
                raise ValueError("Invalid public descriptor flag")
            if record.status not in {"installed", "quarantined"}:
                raise ValueError("Invalid package status")
            if record.archive_path and record.archive_path != (REGISTRY_DIRECTORY / "quarantine" / key).as_posix():
                raise ValueError("Invalid quarantine path")
            if (not isinstance(record.fingerprint, str) or len(record.fingerprint) != 64
                    or any(c not in "0123456789abcdef" for c in record.fingerprint)):
                raise ValueError("Invalid package fingerprint")
            if record.cleanup is not None and (
                    not isinstance(record.cleanup, dict) or type(record.cleanup.get("ready")) is not bool):
                raise ValueError("Invalid cleanup state")
            if record.transaction is not None:
                tx = record.transaction
                if not isinstance(tx, dict) or set(tx) != {"action", "source", "destination", "status", "fingerprint"}:
                    raise ValueError("Invalid transaction")
                expected = {
                    "import": ((REGISTRY_DIRECTORY / "staging" / key).as_posix(), record.relative_path, "installed"),
                    "remove": (record.relative_path, record.archive_path, "quarantined"),
                    "restore": (record.archive_path, record.relative_path, "installed"),
                }.get(tx["action"])
                if expected is None or (tx["source"], tx["destination"], tx["status"]) != expected or tx["fingerprint"] != record.fingerprint:
                    raise ValueError("Transaction paths do not match package ownership")
            return record
        except (TypeError, ValueError, KeyError) as exc:
            raise LocalModPackageError("A local mod record is invalid; keep it for recovery.") from exc

    def _save(self, registry: dict, *, expected_content: bytes | None = None) -> None:
        path = self._path(REGISTRY_DIRECTORY / "registry.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path(REGISTRY_DIRECTORY / "registry.json")
        temporary = path.with_name(f".registry-{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write((json.dumps(registry, indent=2, ensure_ascii=True) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            if expected_content is not None:
                _ordinary(path, directory=False)
                if path.read_bytes() != expected_content:
                    raise LocalModPackageError(
                        "The copied mod registry changed before registration; the reviewed bytes were not replaced."
                    )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _put(registry: dict, record: LocalModRecord) -> None:
        registry["records"][record.record_id] = dict(vars(record))

    def records(self) -> tuple[LocalModRecord, ...]:
        return tuple(self._record(key, row) for key, row in self._load()["records"].items())

    def can_manage(self, mod: Mod) -> bool:
        """Read-only private-folder eligibility; mutations recheck under the lock."""
        try:
            registry = self._load()
            self._no_pending(registry)
            self._standalone_mod(mod, registry)
        except (OSError, ValueError, LocalModPackageError):
            return False
        return True

    def mod_key(self, mod: Mod) -> str:
        if mod.evejs_root is None or Path(mod.evejs_root).resolve() != self.root:
            raise LocalModPackageError("The mod belongs to a different EveJS root.")
        try:
            relative = mod.path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise LocalModPackageError("The mod folder is outside this EveJS root.") from exc
        return relative.casefold() + "|" + mod.id.casefold()

    def _ordered(self, mods: Iterable[Mod], registry: dict) -> list[Mod]:
        mods = list(mods)
        keys = [self.mod_key(mod) for mod in mods]
        if len(keys) != len(set(keys)):
            raise LocalModPackageError("The supplied mod list repeats an identity.")
        positions = {key: index for index, key in enumerate(registry["order"])}
        return [mod for _, mod in sorted(enumerate(mods), key=lambda pair: (
            positions.get(keys[pair[0]], len(positions)), pair[0]))]

    def sort_mods(self, mods: Iterable[Mod]) -> list[Mod]:
        """Preserve input order for an unregistered root and newly seen mods."""
        return self._ordered(mods, self._load())

    def sort_mods_for_runtime(self, mods: Iterable[Mod]) -> list[Mod]:
        """Read runtime ordering from a valid registry without granting ownership.

        A copied registry can supply package order after its metadata and
        pending-transaction state are validated. Mod keys remain bound to this
        physical root, while management and mutation keep using strict ``_load``.
        """
        mods = list(mods)
        registry, _owns_root, _content = self._read_registry(allow_foreign_root=True)
        self._no_pending(registry)
        return self._ordered(mods, registry)

    def _relocation_snapshot(self) -> tuple[dict, bytes]:
        registry, owns_root, content = self._read_registry(allow_foreign_root=True)
        if content is None:
            raise LocalModPackageError("This EveJS folder has no saved launcher mod registry to register.")
        if owns_root:
            raise LocalModPackageError("The launcher mod registry already belongs to this EveJS folder.")
        self._no_pending(registry)
        for key, row in registry["records"].items():
            record = self._record(key, row)
            package = self._path(record.relative_path)
            if package.exists():
                _ordinary(package, directory=True)
            if record.archive_path:
                archive = self._path(record.archive_path)
                if archive.exists():
                    _ordinary(archive, directory=True)
        try:
            from .mod_updates import pending_updates
            if pending_updates(self.root):
                raise LocalModPackageError(
                    "Recover pending mod updates before registering this copied mod registry."
                )
        except LocalModPackageError:
            raise
        except Exception as exc:
            raise LocalModPackageError(
                "Could not verify mod update recovery state; the copied registry was left unchanged."
            ) from exc
        return registry, content

    def relocation_preview(self) -> RegistryRelocationPreview:
        """Describe a safe, explicit registration of copied records to this root."""
        registry, content = self._relocation_snapshot()
        return RegistryRelocationPreview(
            saved_root=registry["root"],
            current_root=str(self.root),
            registry_sha256=hashlib.sha256(content).hexdigest(),
            record_count=len(registry["records"]),
        )

    def _backup_registry(self, content: bytes) -> Path:
        directory = self._path(REGISTRY_DIRECTORY)
        _ordinary(directory, directory=True)
        for _ in range(8):
            suffix = uuid.uuid4().hex
            backup = self._path(REGISTRY_DIRECTORY / f"registry.before-relocation-{suffix}.json")
            temporary = self._path(REGISTRY_DIRECTORY / f".registry-backup-{suffix}.tmp")
            created_temporary = False
            try:
                with temporary.open("xb") as stream:
                    created_temporary = True
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                # A same-directory hard link publishes only a complete backup
                # and fails rather than replacing any pre-existing path.
                os.link(temporary, backup)
                return backup
            except FileExistsError:
                continue
            except OSError as exc:
                raise LocalModPackageError(
                    "Could not create an atomic backup of the copied mod registry; it was not registered."
                ) from exc
            finally:
                if created_temporary:
                    temporary.unlink(missing_ok=True)
        raise LocalModPackageError("Could not allocate a unique backup path; the copied registry was not registered.")

    def relocate_registry(self, expected_sha256: str) -> Path:
        """Register a reviewed copied registry to this root, retaining its exact prior bytes."""
        if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
                or any(char not in "0123456789abcdef" for char in expected_sha256)):
            raise LocalModPackageError("Refresh the copied registry preview before registering it.")
        with acquire_mod_lifecycle_lock(self.root):
            registry, content = self._relocation_snapshot()
            if hashlib.sha256(content).hexdigest() != expected_sha256:
                raise LocalModPackageError(
                    "The copied mod registry changed after preview. Refresh the Mods page and review it again."
                )
            backup = self._backup_registry(content)
            current_registry, current_content = self._relocation_snapshot()
            if current_content != content or current_registry != registry:
                raise LocalModPackageError(
                    f"The copied mod registry changed during registration. Its original bytes were backed up at {backup}."
                )
            relocated = {**current_registry, "root": str(self.root)}
            try:
                self._save(relocated, expected_content=content)
                verified = self._load()
            except Exception as exc:
                raise LocalModPackageError(
                    f"Could not verify registry registration. The original registry backup is at {backup}."
                ) from exc
            if verified != relocated:
                raise LocalModPackageError(
                    f"Registry registration did not preserve its metadata. The original registry backup is at {backup}."
                )
            return backup.resolve(strict=True)

    def _remember(self, registry: dict, mods: Iterable[Mod]) -> list[Mod]:
        result = self._ordered(mods, registry)
        for mod in result:
            key = self.mod_key(mod)
            if key not in registry["order"]:
                registry["order"].append(key)
        return result

    def remember_order(self, mods: Iterable[Mod]) -> list[Mod]:
        with acquire_mod_lifecycle_lock(self.root):
            registry = self._load()
            self._no_pending(registry)
            result = self._remember(registry, mods)
            self._save(registry)
            return result

    def set_order(self, mods: Iterable[Mod]) -> tuple[str, ...]:
        """Persist an explicitly requested full order; retain missing/removed keys."""
        with acquire_mod_lifecycle_lock(self.root):
            registry = self._load()
            self._no_pending(registry)
            mods = list(mods)
            self._ordered(mods, registry)
            keys = [self.mod_key(mod) for mod in mods]
            registry["order"] = keys + [key for key in registry["order"] if key not in keys]
            self._save(registry)
            return tuple(keys)

    def inspect(self, source: str | Path, *, folder_name: str | None = None) -> ImportPreview:
        source = Path(source).absolute()
        _ordinary(source)
        if source.is_dir():
            entries = _folder_entries(source, self.limits)
        else:
            try:
                with zipfile.ZipFile(source) as archive:
                    entries = _zip_entries(archive, self.limits)
            except (zipfile.BadZipFile, OSError) as exc:
                raise LocalModPackageError("Select a folder or a readable ZIP package.") from exc
        prefix = _package_prefix(entries)
        name = folder_name or (PurePosixPath(prefix).name if prefix else (source.name if source.is_dir() else source.stem))
        _component(name)
        package_files = [str(_relative(name)) for name, _, directory in entries if not directory]
        active_name = prefix + "/loader.js" if prefix else "loader.js"
        has_loader = any((prefix + "/" if prefix else "") + name in package_files for name in _LOADERS)
        descriptor_name = (prefix + "/" if prefix else "") + MANIFEST_FILENAME
        metadata = None
        if descriptor_name in package_files:
            if source.is_dir():
                descriptor = source / descriptor_name
                if descriptor.stat().st_size > MAX_API_MANIFEST_BYTES:
                    raise LocalModPackageError("The package descriptor is too large.")
                metadata = _json(descriptor.read_bytes())
            else:
                with zipfile.ZipFile(source) as archive:
                    member = next(info for info in archive.infolist() if str(_relative(info.filename)) == descriptor_name)
                    if member.file_size > MAX_API_MANIFEST_BYTES:
                        raise LocalModPackageError("The package descriptor is too large.")
                    metadata = _json(archive.read(member))
        package_kind, mod_id = self._package_identity(metadata, name, has_loader)
        return ImportPreview(source, name, prefix, len(package_files),
                             sum(size for _, size, directory in entries if not directory), active_name in package_files,
                             mod_id, package_kind, has_loader,
                             metadata is not None and type(metadata.get("schemaVersion")) is int and metadata["schemaVersion"] == 3)

    @staticmethod
    def _package_identity(metadata: dict | None, name: str, has_loader: bool) -> tuple[str, str]:
        """Recognize package shape; public API discovery validates the full schema."""
        if metadata is not None:
            source_package = False
            if metadata.get("kind") == "source-integrated" and metadata.get("schemaVersion") == 3:
                api, activation = metadata.get("launcherApi"), metadata.get("activation")
                capabilities = api.get("capabilities") if isinstance(api, dict) else None
                source_package = (isinstance(activation, dict) and activation.get("strategy") == "package"
                    and isinstance(capabilities, list) and all(isinstance(item, str) for item in capabilities)
                    and SOURCE_PACKAGE_CAPABILITIES.issubset(capabilities))
            if (metadata.get("kind") == "source-integrated" and not source_package) or metadata.get("schemaVersion") == 2:
                raise LocalModPackageError("This is a source integration; use its installation/removal provider.")
            if type(metadata.get("schemaVersion")) is int and metadata["schemaVersion"] == 3:
                kind = metadata.get("kind")
                if kind in {"settings", "client-package"} or source_package or has_loader:
                    mod_id = metadata.get("id", name)
                    if (not isinstance(mod_id, str) or not mod_id or len(mod_id) > 128
                            or mod_id != mod_id.strip()
                            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in mod_id)):
                        raise LocalModPackageError("The declared package identity is invalid.")
                    return (kind if kind in {"settings", "client-package"} or source_package else "loader"), mod_id
        if not has_loader:
            raise LocalModPackageError("A descriptor-only package needs a supported schema-3 package kind.")
        return "loader", name

    def _copy_package(self, preview: ImportPreview, destination: Path) -> None:
        source = preview.source
        destination.mkdir()
        if source.is_dir():
            package = source / preview.package_prefix
            entries = _folder_entries(package, self.limits)
            for name, _, directory in entries:
                target = destination / str(_relative(name))
                if directory:
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _ordinary(package / name, directory=False)
                    shutil.copyfile(package / name, target)
            if _fingerprint(package, self.limits) != _fingerprint(destination, self.limits):
                raise LocalModPackageError("The source package changed while it was being copied.")
        else:
            with zipfile.ZipFile(source) as archive:
                _zip_entries(archive, self.limits)
                prefix = preview.package_prefix + "/" if preview.package_prefix else ""
                total = 0
                for member in archive.infolist():
                    normalized = str(_relative(member.filename))
                    if not normalized.startswith(prefix) or normalized == prefix.rstrip("/"):
                        continue
                    relative = normalized[len(prefix):]
                    target = destination / str(_relative(relative))
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    size = 0
                    with archive.open(member) as incoming, target.open("xb") as outgoing:
                        while chunk := incoming.read(1024 * 1024):
                            size += len(chunk)
                            total += len(chunk)
                            if size > self.limits.max_file_bytes or total > self.limits.max_bytes:
                                raise LocalModPackageError("Expanded ZIP content exceeds the import limit.")
                            outgoing.write(chunk)

    def _standalone_files(self, folder: Path, *, source_provider: bool = False) -> None:
        entries = _folder_entries(folder, self.limits)
        descriptor = folder / MANIFEST_FILENAME
        if descriptor.exists():
            if descriptor.stat().st_size > MAX_API_MANIFEST_BYTES:
                raise LocalModPackageError("The package descriptor is too large.")
            metadata = _json(descriptor.read_bytes())
            if not source_provider:
                self._package_identity(metadata, folder.name, any((folder / name).is_file() for name in _LOADERS))
        for name, _, directory in entries:
            leaf = PurePosixPath(name).name.casefold()
            if not directory and (leaf in {"unins000.exe", "unins000.dat"}
                                  or leaf.endswith("-removal-inventory.json")):
                raise LocalModPackageError("This package contains installer ownership records; use its removal provider.")

    def _collision(self, folder_name: str, mod_id: str, *, exclude: Path | None = None,
                   allow_duplicate_id: bool = False) -> None:
        mods_dir = self._path("mods")
        if mods_dir.exists():
            for entry in mods_dir.iterdir():
                if exclude is not None and entry == exclude:
                    continue
                if entry.name.casefold() == folder_name.casefold():
                    raise LocalModPackageError("A mod folder with this name already exists; nothing was replaced.")
        if allow_duplicate_id:
            return
        for key, row in self._load()["records"].items():
            record = self._record(key, row)
            if record.status != "installed" or (exclude is not None and self.root / record.relative_path == exclude):
                continue
            if record.mod_id.casefold() == mod_id.casefold():
                raise LocalModPackageError("Another installed package already uses this identity.")
        for mod in scan_mods(self.root):
            if exclude is not None and mod.path == exclude:
                continue
            if mod.id.casefold() == mod_id.casefold():
                raise LocalModPackageError("Another installed mod already uses this identity.")

    def _existing(self, mod: Mod, registry: dict) -> LocalModRecord | None:
        self.mod_key(mod)
        return next((self._record(key, row) for key, row in registry["records"].items()
                     if row["relative_path"].casefold() == mod.path.relative_to(self.root).as_posix().casefold()
                     and row["status"] == "installed"), None)

    def _standalone_mod(self, mod: Mod, registry: dict) -> LocalModRecord | None:
        self.mod_key(mod)
        relative = mod.path.relative_to(self.root)
        record = self._existing(mod, registry)
        descriptor = mod.api_descriptor
        source_provider = (descriptor is not None and descriptor.kind == "source-integrated"
            and descriptor.launcher_api is not None
            and {"install", "prepare_remove"} <= set(descriptor.launcher_api.capabilities))
        public_package = ((record is not None and record.package_kind != "loader")
            or (descriptor is not None and descriptor.kind in {"settings", "client-package"})
            or source_provider)
        if (len(relative.parts) != 2 or relative.parts[0] != "mods"
                or (not public_package and (mod.activation_kind is not ActivationKind.LOADER_RENAME
                    or mod.manager_path is not None or mod.config_path is not None))):
            raise LocalModPackageError("This mod is not a private loader folder; use its removal provider.")
        folder = self._path(relative)
        if source_provider and read_api_manifest(self.root, folder) != descriptor:
            raise LocalModPackageError("The mod declaration changed. Refresh Mods before retrying.")
        self._standalone_files(folder, source_provider=source_provider)
        if record and record.mod_id.casefold() != mod.id.casefold():
            raise LocalModPackageError("The folder now belongs to a different mod identity.")
        self._collision(folder.name, mod.id, exclude=folder,
                        allow_duplicate_id=mod.api_descriptor is not None or bool(record and record.public_descriptor))
        # Do not turn an installer-managed mod into a plain deletable folder,
        # including a damaged enrollment whose validation currently fails.
        from .mod_management import ModManagementError, ModNotManagedError, read_managed_mod_registration
        try:
            read_managed_mod_registration(replace(mod, valid=True))
        except ModNotManagedError:
            pass
        except ModManagementError as exc:
            raise LocalModPackageError("Installer ownership needs its existing removal provider.") from exc
        else:
            raise LocalModPackageError("This mod is installer managed; use its removal provider.")
        if record is None and not public_package and not any((folder / name).is_file() for name in _LOADERS):
            raise LocalModPackageError("An unregistered folder must contain a recognized loader.")
        return record

    def _new_record(self, mod: Mod) -> LocalModRecord:
        kind = mod.api_descriptor.kind if mod.api_descriptor is not None else "loader"
        return LocalModRecord(uuid.uuid4().hex, mod.id, mod.path.relative_to(self.root).as_posix(),
                              "installed", _fingerprint(mod.path, self.limits), package_kind=kind,
                              enabled=mod.active if kind != "loader" else None,
                              public_descriptor=mod.api_descriptor is not None)

    def adopt(self, mod: Mod, *, effective_order: Iterable[Mod] | None = None) -> LocalModRecord:
        """Record an explicitly selected legacy folder without altering its files."""
        with acquire_mod_lifecycle_lock(self.root):
            registry = self._load()
            self._no_pending(registry)
            record = self._standalone_mod(mod, registry) or self._new_record(mod)
            self._remember(registry, scan_mods(self.root) if effective_order is None else effective_order)
            self._put(registry, record)
            self._save(registry)
            return record

    def import_package(self, source: str | Path, *, folder_name: str | None = None,
                       enabled: bool = False, effective_order: Iterable[Mod] | None = None) -> LocalModRecord:
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean.")
        preview = self.inspect(source, folder_name=folder_name)
        with acquire_mod_lifecycle_lock(self.root):
            registry = self._load()
            self._no_pending(registry)
            self._collision(preview.folder_name, preview.mod_id, allow_duplicate_id=preview.public_descriptor)
            self._remember(registry, scan_mods(self.root) if effective_order is None else effective_order)
            key = uuid.uuid4().hex
            stage = self._path(REGISTRY_DIRECTORY / "staging" / key)
            stage.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._copy_package(preview, stage)
                self._standalone_files(stage)
                staged_preview = self.inspect(stage, folder_name=preview.folder_name)
                if (staged_preview.mod_id, staged_preview.package_kind, staged_preview.has_loader, staged_preview.public_descriptor) != (
                        preview.mod_id, preview.package_kind, preview.has_loader, preview.public_descriptor):
                    raise LocalModPackageError("The package identity changed during import; retry.")
                self._collision(preview.folder_name, preview.mod_id, allow_duplicate_id=preview.public_descriptor)
                # Only the staged copy is normalized; the user's source is untouched.
                if preview.has_loader:
                    staged = Mod(preview.folder_name, stage, preview.active, evejs_root=self.root)
                    set_mod_active_locked(staged, enabled)
                destination = self._path(Path("mods") / preview.folder_name)
                destination.parent.mkdir(exist_ok=True)
                mod = Mod(preview.folder_name, destination, enabled, id=preview.mod_id, evejs_root=self.root)
                record = LocalModRecord(key, mod.id, destination.relative_to(self.root).as_posix(),
                                        "installed", _fingerprint(stage, self.limits),
                                        package_kind=preview.package_kind,
                                        enabled=enabled if preview.package_kind != "loader" else None,
                                        public_descriptor=preview.public_descriptor)
                self._remember(registry, [mod])
                return self._move(registry, record, "import", stage, destination, "installed")
            finally:
                # Pending recovery owns its staging directory; never discard it.
                if stage.exists() and not any(row.get("transaction") for row in self._load()["records"].values()):
                    self._discard_stage(stage)

    def _cleanup(self, mod: Mod, registry: dict, action: str, gate: CleanupGate | None,
                 record: LocalModRecord | None, *, validate_dependencies: bool = True) -> LocalModRecord | None:
        from .mod_relationships import validate_mod_change
        if validate_dependencies:
            validate_mod_change(mod, False)
        if gate is None:
            if record and record.cleanup and record.cleanup.get("ready") is False:
                raise LocalModCleanupPending(CleanupDecision(False, str(record.cleanup.get("state", "cleanup_pending")),
                    str(record.cleanup.get("message", "Cleanup is not complete.")),
                    bool(record.cleanup.get("restartRequired"))))
            if record and record.package_kind != "loader":
                raise LocalModPackageError("This public package requires its cleanup provider before disable/removal.")
            return record
        decision = gate(CleanupRequest(self.root, action, mod, record, uuid.uuid4().hex))
        if not isinstance(decision, CleanupDecision) or type(decision.ready) is not bool:
            raise LocalModPackageError("The cleanup provider returned an invalid result.")
        record = record or self._new_record(mod)
        record = replace(record, cleanup={"action": action, "state": decision.state,
            "message": decision.message, "ready": decision.ready, "restartRequired": decision.restart_required})
        self._put(registry, record)
        self._save(registry)
        if not decision.ready:
            raise LocalModCleanupPending(decision)
        return record

    def disable(self, mod: Mod, *, cleanup_gate: CleanupGate | None = None, for_update: bool = False) -> bool:
        """Keep other mods unchanged; a pending cleanup keeps this loader present."""
        with acquire_mod_lifecycle_lock(self.root):
            registry = self._load()
            self._no_pending(registry)
            self.mod_key(mod)
            record = self._existing(mod, registry)
            if cleanup_gate is not None:
                record = self._standalone_mod(mod, registry)
            record = self._cleanup(mod, registry, "disable", cleanup_gate, record, validate_dependencies=not for_update)
            if record is not None and mod.activation_kind in {ActivationKind.CLIENT_PACKAGE, ActivationKind.PACKAGE}:
                self._put(registry, replace(record, enabled=False))
                self._save(registry)
                return False
            prepare_mod_activation(mod, False)
            try:
                configured = set_mod_active_locked(mod, False)
                if configured is not False:
                    raise LocalModPackageError("The mod did not become disabled.")
            except Exception:
                fail_mod_activation(mod, False, "activation-mutation-failed")
                raise
            mark_mod_activation_pending(mod, False)
            return configured

    def set_enabled(self, record_id: str, enabled: bool, *, cleanup_gate: CleanupGate | None = None) -> LocalModRecord:
        """Record a public package's configured request, not runtime preparation.

        Public activation calls this after its provider transaction. Descriptors
        remain immutable package data; a true value is not execution evidence.
        """
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean.")
        with acquire_mod_lifecycle_lock(self.root):
            return self.set_enabled_locked(record_id, enabled, cleanup_gate=cleanup_gate)

    def set_enabled_locked(self, record_id: str, enabled: bool, *, cleanup_gate: CleanupGate | None = None) -> LocalModRecord:
        """Same request commit while a caller already owns the lifecycle lock."""
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean.")
        registry = self._load()
        self._no_pending(registry)
        try:
            record = self._record(record_id, registry["records"][record_id])
        except KeyError as exc:
            raise LocalModPackageError("The public package record does not exist.") from exc
        if record.status != "installed" or record.package_kind == "loader":
            raise LocalModPackageError("Use normal loader activation for legacy folders.")
        folder = self._path(record.relative_path)
        _ordinary(folder, directory=True)
        mod = Mod(folder.name, folder, bool(record.enabled), id=record.mod_id, evejs_root=self.root,
                  activation_kind=ActivationKind.CLIENT_PACKAGE)
        if not enabled:
            record = self._cleanup(mod, registry, "disable", cleanup_gate, record) or record
        elif record.cleanup and record.cleanup.get("ready") is False:
            raise LocalModPackageError("Resolve the pending cleanup before enabling this package.")
        committed = replace(record, enabled=enabled)
        self._put(registry, committed)
        self._save(registry)
        return committed

    def remove(self, mod: Mod, *, cleanup_gate: CleanupGate | None = None) -> LocalModRecord:
        with acquire_mod_lifecycle_lock(self.root):
            registry = self._load()
            self._no_pending(registry)
            record = self._standalone_mod(mod, registry)
            self._remember(registry, scan_mods(self.root))
            record = self._cleanup(mod, registry, "remove", cleanup_gate, record) or self._new_record(mod)
            if record.package_kind == "source-integrated":
                if mod.activation_kind is ActivationKind.JSON_BOOLEAN:
                    set_mod_active_locked(mod, False)
            if record.package_kind != "loader":
                record = replace(record, enabled=False)
            archive_relative = (REGISTRY_DIRECTORY / "quarantine" / record.record_id).as_posix()
            record = replace(record, fingerprint=_fingerprint(mod.path, self.limits), archive_path=archive_relative)
            archive = self._path(archive_relative)
            archive.parent.mkdir(parents=True, exist_ok=True)
            return self._move(registry, record, "remove", mod.path, archive, "quarantined")

    def restore(self, record_id: str) -> LocalModRecord:
        with acquire_mod_lifecycle_lock(self.root):
            registry = self._load()
            self._no_pending(registry)
            try:
                record = self._record(record_id, registry["records"][record_id])
            except KeyError as exc:
                raise LocalModPackageError("The quarantine record does not exist in this EveJS root.") from exc
            if record.status != "quarantined":
                raise LocalModPackageError("This mod is not in quarantine.")
            destination = self._path(record.relative_path)
            self._collision(destination.name, record.mod_id, allow_duplicate_id=record.public_descriptor)
            archive = self._path(record.archive_path)
            if _fingerprint(archive, self.limits) != record.fingerprint:
                raise LocalModPackageError("The archived package changed; keep it for manual recovery.")
            if record.package_kind == "source-integrated":
                descriptor = read_api_manifest(self.root, archive)
                config_path = descriptor.config_path
                if config_path is not None:
                    if config_path.is_relative_to(destination):
                        config_path = archive / config_path.relative_to(destination)
                    config_path = self._path(config_path.relative_to(self.root))
                    _ordinary(config_path, directory=False)
                    if config_path.stat().st_size > MAX_CONFIG_BYTES:
                        raise LocalModPackageError("The mod configuration is too large.")
                    configured = _json(config_path.read_bytes())
                    for key in descriptor.config_key:
                        configured = configured.get(key) if isinstance(configured, dict) else None
                    if configured is not False:
                        raise LocalModPackageError("The removed mod's activation setting changed. Disable it before restoring its folder.")
            # Cleanup already removed this package's runtime contribution. Restoring
            # its folder must not reactivate it, including older quarantine records.
            if record.package_kind != "loader":
                record = replace(record, enabled=False)
            return self._move(registry, record, "restore", archive, destination, "installed")

    def _move(self, registry: dict, record: LocalModRecord, action: str,
              source: Path, destination: Path, status: str) -> LocalModRecord:
        before = json.loads(json.dumps(registry))
        tx = {"action": action, "source": source.relative_to(self.root).as_posix(),
              "destination": destination.relative_to(self.root).as_posix(), "status": status,
              "fingerprint": record.fingerprint}
        pending = replace(record, transaction=tx)
        self._put(registry, pending)
        self._save(registry)
        moved = False
        try:
            source = self._path(tx["source"])
            destination = self._path(tx["destination"])
            if destination.exists():
                raise LocalModPackageError("The destination already exists; nothing was replaced.")
            if _fingerprint(source, self.limits) != record.fingerprint:
                raise LocalModPackageError("The package changed before the operation; retry.")
            source.rename(destination)
            moved = True
            if _fingerprint(destination, self.limits) != record.fingerprint:
                raise LocalModPackageError("The package changed during the operation; restoring its folder.")
            committed = replace(record, status=status, transaction=None)
            self._put(registry, committed)
            self._save(registry)
            return committed
        except Exception as exc:
            try:
                if moved:
                    source = self._path(tx["source"])
                    destination = self._path(tx["destination"])
                    if source.exists():
                        raise LocalModPackageError("Rollback destination is occupied.")
                    destination.rename(source)
                self._save(before)
            except Exception as rollback_exc:
                raise LocalModPackageError("The operation needs recovery; its folder and pending registry record were retained.") from rollback_exc
            raise LocalModPackageError(f"The operation was rolled back: {exc}") from exc

    @staticmethod
    def _no_pending(registry: dict) -> None:
        if any(row.get("transaction") for row in registry["records"].values()):
            raise LocalModPackageError("Recover the interrupted package operation before another mutation.")

    def recover_pending(self) -> tuple[LocalModRecord, ...]:
        """Finish only a recorded, unchanged move; never guess ownership or overwrite."""
        with acquire_mod_lifecycle_lock(self.root):
            registry = self._load()
            recovered = []
            for key, row in list(registry["records"].items()):
                record = self._record(key, row)
                if record.transaction is None:
                    continue
                tx = record.transaction
                source, destination = self._path(tx["source"]), self._path(tx["destination"])
                if source.exists() == destination.exists():
                    raise LocalModPackageError("Recovery found both or neither folder; preserve both locations for review.")
                current = source if source.exists() else destination
                if _fingerprint(current, self.limits) != record.fingerprint:
                    raise LocalModPackageError("Recovery found changed package content; no files were moved.")
                if source.exists():
                    if tx["action"] != "remove":
                        # This pending record already claims the destination;
                        # check other owners without colliding with ourselves.
                        self._collision(destination.name, record.mod_id, exclude=destination,
                                        allow_duplicate_id=record.public_descriptor)
                    source.rename(destination)
                committed = replace(record, status=str(tx["status"]), transaction=None)
                self._put(registry, committed)
                self._save(registry)
                recovered.append(committed)
            return tuple(recovered)

    def _discard_stage(self, stage: Path) -> None:
        safe = self._path(stage.relative_to(self.root))
        if safe.parent != self.root / REGISTRY_DIRECTORY / "staging":
            raise LocalModPackageError("Only this operation's staging folder may be discarded.")
        _folder_entries(safe, self.limits)
        shutil.rmtree(safe)
