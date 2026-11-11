"""Frozen, opt-in client menu delivery. No mod discovery or client archive edits.

The preflight reads only explicitly selected loader folders. The immutable
material is part of the runtime plan; Node delivers those captured bytes through
EveJS's existing signed login function in both Native and Docker.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .mod_api_manifest import API_MANIFEST_FILENAME, ModApiManifestError, read_api_manifest, _object

MAX_ENTRY_BYTES = 128 * 1024
MAX_DELIVERY_BYTES = 1024 * 1024
MAX_INITIALIZER_BYTES = 256 * 1024
_BOOTSTRAP_TEMPLATE = "try:\n import sys, types, base64, json\n _delivery = json.loads(base64.b64decode('__PAYLOAD__'))\n _module = sys.modules.get('evejs_mod_menu')\n if _module is not None and getattr(_module, '_build_digest', None) != _delivery['frameworkDigest']:\n  _module.shutdown()\n  _module = None\n if _module is None:\n  _module = types.ModuleType('evejs_mod_menu')\n  _module.__dict__.update(sm=sm, session=session)\n  sys.modules['evejs_mod_menu'] = _module\n  eval(compile(base64.b64decode(_delivery['framework']), '<evejs-mod-menu-v1>', 'exec'), _module.__dict__)\n  _module._build_digest = _delivery['frameworkDigest']\n _module.bootstrap(_delivery['entries'])\nexcept:\n import traceback\n traceback.print_exc()\n print 'EVEJS_MOD_MENU:BOOTSTRAP_FAILED'\n raise\n"
HANDSHAKE_RELATIVE = "server/src/network/tcp/handshake.js"
_DIGEST = re.compile(r"[0-9a-f]{64}")
CONTAINER_PREFIX = "/app/.evejs-launcher/shared-menu/"


@dataclass(frozen=True)
class MenuParticipant:
    folder: str
    mod_id: str
    relative_entrypoint: str
    source: bytes
    manifest_sha256: str


@dataclass(frozen=True)
class SharedMenuMaterial:
    participants: tuple[MenuParticipant, ...]
    handshake_sha256: str
    loader_content: bytes

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.loader_content).hexdigest()


def _read_safe(root: Path, path: Path, limit: int) -> bytes:
    relative = path.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor /= part
        meta = cursor.lstat()
        if stat.S_ISLNK(meta.st_mode) or getattr(meta, "st_file_attributes", 0) & 0x400:
            raise ValueError("Shared menu paths cannot contain links or junctions.")
    if not stat.S_ISREG(meta.st_mode) or meta.st_size > limit:
        raise ValueError("Shared menu input is not a bounded regular file.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    after = path.lstat()
    if len(data) > limit or (meta.st_ino, meta.st_size, meta.st_mtime_ns) != (
        after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise ValueError("Shared menu input changed while being read.")
    return data


def capture_shared_menu(root: str | Path, selected: tuple[str, ...]) -> SharedMenuMaterial | None:
    """Preflight only the frozen selection, never scan the mods directory."""
    root = Path(root).resolve(strict=True)
    participants = []
    for name in selected:
        if not name or name in {".", ".."} or any(c in name for c in '/\\:\0'):
            raise ValueError("Unsafe selected menu loader folder.")
        folder = root / "mods" / name
        manifest = folder / API_MANIFEST_FILENAME
        if not manifest.exists():
            continue  # Legacy loaders keep their existing behavior.
        raw = _read_safe(root, manifest, 1024 * 1024)
        try:
            def reject_constant(_):
                raise ModApiManifestError("Non-finite manifest value")
            declaration = json.loads(raw, object_pairs_hook=_object, parse_constant=reject_constant)
            descriptor = read_api_manifest(root, folder, declaration)
        except (ModApiManifestError, ValueError, RecursionError):
            # Discovery exposes descriptor_error and retains a legacy loader.
            # Never interpret invalid metadata or change that legacy behavior.
            continue
        if descriptor.client_menu is None:
            continue
        source = _read_safe(root, descriptor.client_menu.entrypoint, MAX_ENTRY_BYTES)
        source.decode("utf-8")
        participants.append(MenuParticipant(name, descriptor.id.lower(),
            descriptor.client_menu.entrypoint.relative_to(folder).as_posix(), source,
            hashlib.sha256(raw).hexdigest()))
    if not participants:
        return None
    ids = [entry.mod_id for entry in participants]
    if len(set(ids)) != len(ids):
        raise ValueError("Shared menu mod IDs must be unique.")
    handshake = _read_safe(root, root / HANDSHAKE_RELATIVE, 2 * 1024 * 1024)
    # Hashes freeze inputs against launch-time drift, not other mods' source edits.
    # Actual exported class/codec capabilities are checked by the runtime adapter.
    handshake.decode("utf-8")
    framework = (Path(__file__).parent / "client" / "shared_menu.py").read_bytes()
    template = (Path(__file__).parent / "client" / "shared_menu_loader.cjs").read_text(encoding="utf-8")
    payload = {
        "framework": base64.b64encode(framework).decode("ascii"),
        "frameworkDigest": hashlib.sha256(framework).hexdigest(),
        "entries": [{"id": item.mod_id, "source": base64.b64encode(item.source).decode("ascii")}
                    for item in participants],
        "handshakeSha256": hashlib.sha256(handshake).hexdigest(),
    }
    encoded = base64.b64encode(json.dumps(payload,separators=(",", ":"),ensure_ascii=True).encode("utf-8")).decode("ascii")
    payload["bootstrap"] = _BOOTSTRAP_TEMPLATE.replace("__PAYLOAD__",encoded)
    if len(payload["bootstrap"].encode("utf-8")) > MAX_INITIALIZER_BYTES:
        raise ValueError("Shared Mods menu initializer exceeds the login delivery limit of 256 KiB. Reduce the selected client menu entrypoints.")
    runtime = (Path(__file__).parent / "client" / "login_delivery.cjs").read_text(encoding="utf-8")
    loader = template.replace("/*__LOGIN_DELIVERY_RUNTIME__*/", runtime).replace(
        "/*__FROZEN_PAYLOAD__*/", json.dumps(payload, sort_keys=True, ensure_ascii=True)).encode("utf-8")
    if len(loader) > MAX_DELIVERY_BYTES:
        raise ValueError("Shared menu delivery exceeds 1 MiB; keep entrypoints small.")
    return SharedMenuMaterial(tuple(participants), payload["handshakeSha256"], loader)


def shared_menu_path(root: str | Path, digest: str) -> Path:
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError("Invalid shared menu artifact digest.")
    return Path(root) / ".evejs-launcher" / "shared-menu" / digest / "loader.cjs"


def container_menu_path(digest: str) -> str:
    shared_menu_path(Path("."), digest)
    return CONTAINER_PREFIX + digest + "/loader.cjs"


def stage_shared_menu(root: str | Path, material: SharedMenuMaterial | None) -> None:
    """Stage immutable Launcher-owned files inside the selected server root."""
    if material is None:
        return
    root = Path(root).resolve(strict=True)
    if not isinstance(material, SharedMenuMaterial):
        raise ValueError("Frozen shared menu material is required.")
    for item in material.participants:
        folder = root / "mods" / item.folder
        if _read_safe(root, folder / item.relative_entrypoint, MAX_ENTRY_BYTES) != item.source:
            raise ValueError("A selected client menu entrypoint changed after planning. Retry startup.")
        if hashlib.sha256(_read_safe(root, folder / API_MANIFEST_FILENAME, 1024 * 1024)).hexdigest() != item.manifest_sha256:
            raise ValueError("A selected client menu declaration changed after planning. Retry startup.")
    if hashlib.sha256(_read_safe(root, root / HANDSHAKE_RELATIVE, 2 * 1024 * 1024)).hexdigest() != material.handshake_sha256:
        raise ValueError("The EveJS login builder changed after planning. Retry startup.")
    destination = shared_menu_path(root, material.digest)
    cursor = root
    for part in destination.parent.relative_to(root).parts:
        cursor /= part
        cursor.mkdir(exist_ok=True)
        meta = cursor.lstat()
        if (not stat.S_ISDIR(meta.st_mode) or stat.S_ISLNK(meta.st_mode)
                or getattr(meta, "st_file_attributes", 0) & 0x400):
            raise ValueError("The shared menu staging directory is unsafe.")
    try:
        with destination.open("xb") as stream:
            stream.write(material.loader_content)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
    if _read_safe(root, destination, MAX_DELIVERY_BYTES) != material.loader_content:
        raise ValueError("The immutable shared menu delivery artifact was modified.")
