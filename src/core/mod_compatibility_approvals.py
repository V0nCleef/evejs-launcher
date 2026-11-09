"""Local user choices; author support and runtime verification stay authoritative."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile

from .local_mod_packages import LocalModPackages, PackageLimits, _fingerprint, _ordinary, _json
from .mod_evejs_compatibility import installed_evejs_version, normalize_evejs_version
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_loader_state import resolve_loader_state
from .mod_manifest import ActivationKind, scan_mods
from .mod_runtime_state import mod_contract_sha256, mod_state_key
from .mod_settings_schema import parse_settings_schema

_RELATIVE = "_local/launcher-mods/compatibility-approvals.json"
_MAX_BYTES = 256 * 1024
_LIMITS = PackageLimits(max_files=2048, max_bytes=32 * 1024 * 1024, max_file_bytes=16 * 1024 * 1024)
log = logging.getLogger(__name__)


def backend_key(value):
    if value in {"docker", "docker_compose"}:
        return "docker"
    if value == "native":
        return value
    raise ValueError("Unknown approval backend.")


def payload_fingerprint(mod):
    """Reuse package hashing, omitting only declared mutable files and state names."""
    descriptor = mod.api_descriptor
    excluded = set(descriptor.updates.preserve_files if descriptor and descriptor.updates else ())
    if descriptor and descriptor.settings:
        excluded.update(item.path for item in parse_settings_schema(descriptor.settings).files if item.base == "mod")
    # A mutable declaration never excuses a descriptor or executable payload.
    excluded = {name for name in excluded if not Path(name).name.casefold().startswith("evejs-launcher.")}
    canonical = {}
    if mod.activation_kind is ActivationKind.LOADER_RENAME:
        selected = resolve_loader_state(mod.path, root=Path(mod.evejs_root)).selected_path
        if selected is None:
            raise ValueError("The mod loader is unavailable.")
        canonical[selected.name] = "loader.js"
        excluded.difference_update(canonical)
    contract = mod_contract_sha256(mod)
    payload = _fingerprint(mod.path, _LIMITS, exclude_files=excluded,
                           canonical_names=canonical, include_directories=False)
    return hashlib.sha256((contract + ":" + payload).encode("ascii")).hexdigest()


class LocalCompatibilityApprovals:
    def __init__(self, root):
        self.packages = LocalModPackages(root)
        self.root = self.packages.root

    def _empty(self):
        return {"schemaVersion": 1, "root": str(self.root), "records": {}}

    def _load(self):
        file = self.packages._path(_RELATIVE)
        if not file.exists():
            return self._empty()
        _ordinary(file, directory=False)
        if file.stat().st_size > _MAX_BYTES:
            raise ValueError("Local compatibility approvals are too large.")
        state = _json(file.read_bytes())
        if (not isinstance(state, dict) or set(state) != {"schemaVersion", "root", "records"}
                or type(state["schemaVersion"]) is not int or state["schemaVersion"] != 1
                or not isinstance(state["root"], str) or not isinstance(state["records"], dict)
                or len(state["records"]) > 256):
            raise ValueError("Local compatibility approvals are malformed.")
        if os.path.normcase(state["root"]) != os.path.normcase(str(self.root)):
            return self._empty()  # copied approval is never portable authority
        for key, row in state["records"].items():
            if (not isinstance(row, dict) or set(row) != {"modKey", "backend", "evejsVersion", "payloadSha256", "approvedAt"}
                    or not all(isinstance(value, str) for value in row.values())
                    or key != self._key(row["modKey"], row["backend"])
                    or row["backend"] not in {"native", "docker"}
                    or normalize_evejs_version(row["evejsVersion"]) != row["evejsVersion"]
                    or len(row["payloadSha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in row["payloadSha256"])):
                raise ValueError("A local compatibility approval is malformed.")
        return state

    @staticmethod
    def _key(mod_key, backend):
        return hashlib.sha256((backend + "\0" + mod_key).encode("utf-8")).hexdigest()

    def matching_fingerprint(self, mod, backend, version):
        try:
            version = normalize_evejs_version(version)
            backend = backend_key(backend)
            row = self._load()["records"].get(self._key(mod_state_key(mod), backend))
            if row and row["evejsVersion"] == version and row["payloadSha256"] == payload_fingerprint(mod):
                return row["payloadSha256"]
        except (OSError, ValueError, RuntimeError) as exc:
            log.warning("Local mod approval ignored: %s", exc)
        return None

    def approved(self, mod, backend, version):
        return self.matching_fingerprint(mod, backend, version) is not None

    def _write(self, state):
        destination = self.packages._path(_RELATIVE)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.packages._path(_RELATIVE)
        content = (json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        if len(content) > _MAX_BYTES or len(state["records"]) > 256:
            raise ValueError("Local compatibility approvals are too large.")
        descriptor, name = tempfile.mkstemp(prefix=".compatibility-", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self.packages._path(_RELATIVE)
            os.replace(name, destination)
        finally:
            Path(name).unlink(missing_ok=True)

    def remember(self, mods, backend, version, expected, *, lease=None):
        version, backend = normalize_evejs_version(version), backend_key(backend)
        if lease is not None and (lease.released or lease.root != self.root):
            raise ValueError("The approval lifecycle lease is no longer valid.")
        with nullcontext() if lease is not None else acquire_mod_lifecycle_lock(self.root):
            if installed_evejs_version(self.root) != version:
                raise ValueError("The EveJS build changed before approval.")
            current = {mod_state_key(mod): mod for mod in scan_mods(self.root)}
            state = self._load()
            for mod in mods:
                key = mod_state_key(mod)
                fresh = current.get(key)
                if (fresh is None or not fresh.valid or not fresh.active
                        or not fresh.supports_backend(backend)
                        or payload_fingerprint(fresh) != expected.get(key)):
                    raise ValueError("The mod payload changed before approval. Review it again.")
                state["records"][self._key(key, backend)] = {
                    "modKey": key, "backend": backend, "evejsVersion": version,
                    "payloadSha256": expected[key], "approvedAt": datetime.now(timezone.utc).isoformat(),
                }
            self._write(state)

    def forget(self):
        with acquire_mod_lifecycle_lock(self.root):
            self._write(self._empty())
