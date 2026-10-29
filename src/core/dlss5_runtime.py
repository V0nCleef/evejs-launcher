"""Use an installed client renderer without consulting an EveJS installation.

Installation journals belong to explicit install/repair/remove operations. They
are never prerequisites for starting a character with already installed DLLs.
"""
from __future__ import annotations

import configparser
import logging
import os
from pathlib import Path
import re

from .dlss5 import (
    _get_ini_value, _is_reparse_point, _path_entry_exists, _read_ini,
    _set_ini_value, _write_text_atomic,
)

log = logging.getLogger(__name__)
_FILES = ("dxgi.dll", "renodx-dlss5.addon64", "nvngx_dlssnr.dll", "sl.dlss_nr.dll")
_MAX_INI_BYTES = 1024 * 1024


def _plain(path: Path) -> None:
    if _path_entry_exists(path) and _is_reparse_point(path):
        raise ValueError("Renderer preferences must not use redirected paths.")


def _read(path: Path) -> str:
    _plain(path)
    if not path.is_file():
        return ""
    if path.stat().st_size > _MAX_INI_BYTES:
        raise ValueError("Renderer preferences exceed the import limit.")
    text = _read_ini(path)
    _parse(text)
    return text


def _parse(text: str) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read_string(text)
    # ReShade treats keys/sections without regard to case.
    seen = set()
    for section in parser.sections():
        identity = section.casefold()
        if identity in seen:
            raise ValueError("Duplicate renderer preference section.")
        seen.add(identity)
        keys = [key.casefold() for key in parser[section]]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate renderer preference key.")
    return parser


def _merge(base: str, overlay: str) -> str:
    parsed = _parse(overlay)
    for section in parsed.sections():
        for key, value in parsed.items(section):
            base = _set_ini_value(base, section, key, value)
    return base


def _rebase(text: str, source: Path) -> str:
    for key in ("EffectSearchPaths", "TextureSearchPaths", "PresetPath", "StartupPresetPath"):
        value = _get_ini_value(text, "GENERAL", key)
        if not value:
            continue
        # ReShade escapes literal commas as doubled commas.
        paths = value.replace(",,", "\0").split(",")
        paths = [entry.replace("\0", ",").strip() for entry in paths]
        rebased = []
        for entry in paths:
            if entry and not os.path.isabs(entry):
                entry = os.path.normpath(str(source / entry))
            rebased.append(entry.replace(",", ",,"))
        text = _set_ini_value(text, "GENERAL", key, ",".join(rebased))
    return text


def _previous_public_config(profile: Path) -> Path | None:
    """Import old server-scoped preferences once, without needing that server."""
    folder = profile / "mods"
    _plain(folder)
    if not folder.is_dir():
        return None
    candidates = []
    for count, entry in enumerate(folder.iterdir()):
        if count >= 512:
            break
        if not re.fullmatch(r"[0-9a-f]{24}", entry.name) or _is_reparse_point(entry):
            continue
        config = entry / "ReShade.ini"
        try:
            text = _read(config)
            if _get_ini_value(text, "RenoDX.DLSS5", "NeuralUplift") in ("0", "1"):
                candidates.append(config)
        except (OSError, ValueError, RuntimeError, configparser.Error):
            continue
    return max(candidates, key=lambda path: path.stat().st_mtime_ns, default=None)


def _prepare_preferences(profile_tq: Path, client: Path) -> Path:
    profile = profile_tq.parent
    _plain(profile)
    base = profile / "DLSS5"
    _plain(base)
    base.mkdir(exist_ok=True)
    destination = base / "ReShade.ini"
    current = _read(destination)
    # A settings form may have saved only NeuralUplift before first launch.
    # Import the rest once, with that explicit saved preference winning.
    addon_path = _get_ini_value(current, "ADDON", "AddonPath")
    if addon_path == str(client / "bin64"):
        text = current
    else:
        previous = _previous_public_config(profile)
        if addon_path and (previous is None or destination.stat().st_mtime_ns >= previous.stat().st_mtime_ns):
            source = destination
        else:
            source = previous or client / "bin64" / "ReShade.ini"
        text = _rebase(_read(source), source.parent)
        if not addon_path:
            text = _merge(text, current)
    if _get_ini_value(text, "INSTALL", "BasePath"):
        # A private copy must not redirect ReShade into another pilot's state.
        text = _set_ini_value(text, "INSTALL", "BasePath", "")
    text = _set_ini_value(text, "ADDON", "AddonPath", str(client / "bin64"))
    addons = _get_ini_value(text, "ADDON", "LoadFromDllMain") or ""
    if "renodx-dlss5.addon64" not in {entry.strip().casefold() for entry in addons.split(",")}:
        addons = (addons + "," if addons else "") + "renodx-dlss5.addon64"
    text = _set_ini_value(text, "ADDON", "LoadFromDllMain", addons)
    text = _set_ini_value(text, "RenoDX.DLSS5", "EnableHooks", "2")
    text = _set_ini_value(text, "RenoDX.DLSS5", "NeuralUplift", "1", preserve_existing=True)
    if text != current:
        _write_text_atomic(destination, text)
    return base


def prepare_installed_dlss5(profile_tq: Path, client_path: str | Path) -> dict[str, str]:
    """Return optional graphics settings; never turn metadata into a launch gate.

    No server path, package, receipt, executable hash, manager, download, or
    version list participates. Shared client binaries are never changed here.
    """
    environment: dict[str, str] = {}
    try:
        client = Path(client_path).resolve(strict=True)
        binary = client / "bin64"
        if not all((binary / name).is_file() for name in _FILES):
            return environment
        environment = {"TRINITYPLATFORM": "dx12", "EVEJS_DLSS5": "on"}
        base = _prepare_preferences(Path(profile_tq), client)
        environment["RESHADE_BASE_PATH_OVERRIDE"] = str(base)
    except Exception as exc:
        # Optional graphics setup must never prevent the actual client spawn.
        # Retain DX12 when the installed payload was detected successfully.
        log.warning("DLSS5 profile preferences unavailable; continuing client launch: %s", exc)
    return environment
