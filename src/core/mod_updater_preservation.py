"""Protect installed community-mod state using the bundled updater's policy."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile

from .mod_inventory import load_mod_inventory
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_settings_schema import parse_settings_schema

log = logging.getLogger(__name__)
AUTOMINING_LEGACY = tuple(f"config/autoMining.{name}.json" for name in ("players", "statistics", "fleetGroups"))
NPC_MARKET_CONFIG = "externalservices/market-server/config/market-server.local.toml"
POLICY = Path("tools/Updater/update-policy.json")


def _plain(path: Path, boundary: Path) -> None:
    if not path.resolve(strict=True).is_relative_to(boundary):
        raise ValueError("Updater preservation path leaves the selected EveJS folder.")
    cursor = path
    while cursor != boundary:
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError("Updater preservation cannot use linked files or folders.")
        cursor = cursor.parent


def _atomic(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def protect_updater_settings(root, mods) -> bool:
    """Add bounded keep rules; disabled installed mods are included. No helper runs."""
    installed = tuple(mod for mod in mods if mod.valid)
    ids = {mod.id.casefold() for mod in installed}
    if not installed:
        return False
    boundary = Path(root).resolve(strict=True)
    policy = boundary / POLICY
    if not policy.exists():
        return False  # Older releases have no bundled updater to configure.
    _plain(policy, boundary)
    with acquire_mod_lifecycle_lock(boundary):
        original = policy.read_bytes()
        if len(original) > 128 * 1024:
            raise ValueError("The EveJS updater policy exceeds the supported size.")
        data = json.loads(original.decode("utf-8-sig"))
        if not isinstance(data, dict) or data.get("schema") != "evejs.updater-policy.v1":
            raise ValueError("The EveJS updater uses an unrecognised preservation policy.")
        for key in ("keepTrees", "keepMine"):
            rows = data.get(key)
            if not isinstance(rows, list) or len(rows) > 512 or any(not isinstance(row, str) for row in rows):
                raise ValueError("The EveJS updater has an invalid preservation list.")
        changed = False
        settings = AUTOMINING_LEGACY if "automining" in ids else ()
        if "npc-market-buyers" in ids:
            settings += (NPC_MARKET_CONFIG,)
        # Public settings declarations already validate exact text paths under
        # config/. No author allowlist and no extra manifest dialect is needed.
        # The named rules above only bridge older packages' undeclared state.
        for mod in installed:
            descriptor = getattr(mod, "api_descriptor", None)
            if descriptor is not None:
                if descriptor.settings is not None:
                    schema = parse_settings_schema(descriptor.settings)
                    settings += tuple(file.path for file in schema.files if file.base == "evejs")
                if descriptor.config_path is not None:
                    settings += (descriptor.config_path.relative_to(boundary).as_posix(),)
        settings = tuple(dict.fromkeys(settings))
        additions = {"keepTrees": ("mods",), "keepMine": settings}
        for key, rows in additions.items():
            existing = {row.replace("\\", "/").strip("/").casefold() for row in data[key]}
            for row in rows:
                if row.casefold() not in existing:
                    data[key].append(row)
                    changed = True
        if not changed:
            return False
        # Retain the exact incoming policy, including unknown future fields.
        state = boundary / "_local/launcher-updater-preservation"
        _plain(state.parent, boundary)
        state.mkdir(parents=True, exist_ok=True)
        _plain(state, boundary)
        backup = state / (hashlib.sha256(original).hexdigest() + ".json")
        if backup.exists():
            _plain(backup, boundary)
            if backup.read_bytes() != original:
                raise ValueError("Updater policy backup does not match its recorded identity.")
        else:
            _atomic(backup, original)
        if policy.read_bytes() != original:
            raise ValueError("The EveJS updater policy changed while preparing preservation.")
        _atomic(policy, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        log.info("Prepared EveJS updater to preserve installed mod folders and declared settings: %s", boundary)
        return True


def load_inventory_with_updater_protection(root: str, *, native: bool):
    inventory = load_mod_inventory(root)
    if native and not inventory.registry_error and inventory.relocation_preview is None:
        try:
            protect_updater_settings(root, inventory.mods)
        except (OSError, ValueError, RuntimeError) as exc:
            log.warning("Could not prepare EveJS updater settings preservation: %s", exc)
            inventory = replace(inventory, updater_warning=f"Mod settings protection for the EveJS updater could not be prepared: {exc}")
    return inventory
