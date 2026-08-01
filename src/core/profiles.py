"""Profile management via directory junctions.

Each account gets a junction pointing to the real EVE client install.
This gives each account a unique path → unique settings folder on first launch.
"""
import re
import ipaddress
import os
from pathlib import Path
import tempfile

from ..config import CONFIG_DIR
from .platform import create_directory_link, get_eve_settings_path, remove_directory_link

PROFILES_ROOT = CONFIG_DIR / "Profiles"


def get_settings_key(client_path: str) -> str:
    """Replicate settingsKey derivation from PrepareClientSettings.ps1.

    The settings folder is keyed by the executable's install path.
    Each junction has a unique path → unique settings.
    """
    key = client_path.lower()
    key = key.replace(":", "")
    key = key.replace("\\", "_").replace("/", "_")
    # Keep only alphanumeric, dots, underscores, hyphens
    key = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    key = key.strip("_")
    return f"{key}_127.0.0.1"


def create_profile(username: str, real_client_path: str) -> Path:
    """Create a junction profile for the given account.

    Bootstraps only safe text settings. Binary account caches and browser state
    remain isolated to the account that created them.

    Args:
        username: Account username (used as profile folder name).
        real_client_path: Path to the real EVE client's tq folder.

    Returns:
        Path to the profile directory.
    """
    profile_dir = PROFILES_ROOT / username
    profile_dir.mkdir(parents=True, exist_ok=True)

    junction = profile_dir / "tq"
    if not junction.exists():
        create_directory_link(Path(real_client_path), junction)

    # ── Bootstrap EVE settings from real client (or template fallback) ──
    try:
        _bootstrap_settings(username, real_client_path)
    except Exception:
        pass  # non-fatal — username pre-fill will still run

    return profile_dir


def _bootstrap_settings(username: str, real_client_path: str = "") -> None:
    """Bootstrap safe settings without copying account-private cache state."""
    import shutil

    try:
        dst_dir = get_profile_settings_path(username)
    except FileNotFoundError:
        return

    dst_dir.mkdir(parents=True, exist_ok=True)

    # prefs.ini contains renderer/bootstrap settings but no account cache.
    if real_client_path:
        real_settings = get_eve_settings_path(real_client_path)
        if real_settings.exists():
            src = real_settings / "prefs.ini"
            dst = dst_dir / "prefs.ini"
            if src.is_file() and not dst.exists():
                shutil.copy2(src, dst)

    # Fill anything still missing from generic launcher-owned templates.
    template_dir = Path(__file__).resolve().parent / "template_settings"
    if not template_dir.exists():
        return

    for name in ("prefs.ini", "core_public__.yaml"):
        src = template_dir / name
        dst = dst_dir / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)


def delete_profile(username: str) -> None:
    """Remove the profile junction. Settings in LOCALAPPDATA are NOT deleted."""
    profile_dir = PROFILES_ROOT / username
    junction = profile_dir / "tq"

    if junction.exists():
        remove_directory_link(junction)

    # Remove empty profile dir (only if empty after junction removal)
    if profile_dir.exists():
        try:
            profile_dir.rmdir()
        except OSError:
            pass  # directory not empty, leave it


def profile_exists(username: str) -> bool:
    """Check if a profile junction exists for this account."""
    junction = PROFILES_ROOT / username / "tq"
    return junction.exists()


def get_profile_client_path(username: str) -> Path | None:
    """Get the junction path used to launch the client for this profile."""
    junction = PROFILES_ROOT / username / "tq"
    return junction if junction.exists() else None


def get_profile_settings_path(username: str) -> Path:
    """Get the expected EVE settings folder for this profile."""
    junction = PROFILES_ROOT / username / "tq"
    if not junction.exists():
        raise FileNotFoundError(f"Profile junction does not exist: {username}")
    return get_eve_settings_path(str(junction))


def prefill_username(username: str) -> None:
    """Write the username to the EVE client settings so it's pre-filled on
    the login screen, even for accounts that have never been launched before.

    Also ensures ``newbie=0`` in ``prefs.ini`` and bootstraps a complete
    ``core_public__.yaml`` with ``audio:``, ``device:``, and ``ui:`` sections
    so the EVE client renders the full login screen correctly.
    """
    try:
        settings_dir = get_profile_settings_path(username)
    except FileNotFoundError:
        return  # profile not created yet

    settings_dir.mkdir(parents=True, exist_ok=True)

    # ── prefs.ini: ensure newbie=0 so the client skips the setup wizard ──
    prefs_path = settings_dir / "prefs.ini"
    if prefs_path.exists():
        prefs_text = prefs_path.read_text(encoding="utf-8", errors="replace")
    else:
        prefs_text = ""
    if "newbie=1" in prefs_text:
        prefs_text = prefs_text.replace("newbie=1", "newbie=0")
        prefs_path.write_text(prefs_text, encoding="utf-8")
    elif "newbie=" not in prefs_text:
        prefs_text += "\nnewbie=0\n"
        prefs_path.write_text(prefs_text, encoding="utf-8")

    # ── core_public__.yaml: bootstrap from template, then set username ──
    yaml_path = settings_dir / "core_public__.yaml"
    template_dir = Path(__file__).resolve().parent / "template_settings"
    template_yaml = template_dir / "core_public__.yaml"

    if not yaml_path.exists() and template_yaml.exists():
        # First launch — copy the complete template (audio + device + ui sections)
        import shutil
        shutil.copy2(template_yaml, yaml_path)

    import time
    ts = int(time.time() * 10_000_000)  # EVE uses 100-nanosecond intervals

    # Patch the username under the ``ui:`` section
    if yaml_path.exists():
        text = yaml_path.read_text(encoding="utf-8", errors="replace")
        # Replace placeholder or existing username value
        text = re.sub(
            r"(  username: )\[.*?\]",
            f"\\1[{ts}, {username}]",
            text,
        )
        # Replace placeholder or existing usernames block
        text = re.sub(
            r"(  usernames:\n)(  - .*\n  - .*\n)?",
            f"\\1  - {ts}\n  - [{username}]\n",
            text,
        )
        yaml_path.write_text(text, encoding="utf-8")


def configure_profile_game_endpoint(
    username: str,
    profile_tq_path: Path,
    *,
    host: str,
    port: int,
) -> None:
    """Atomically apply one validated game endpoint immediately before launch."""
    _validate_game_endpoint(host, port)
    start_path = Path(profile_tq_path) / "start.ini"
    if not start_path.is_file():
        raise FileNotFoundError("EVE client start.ini is missing.")
    settings_dir = get_profile_settings_path(username)
    settings_dir.mkdir(parents=True, exist_ok=True)
    prefs_path = settings_dir / "prefs.ini"

    start_text = _read_preserving_newlines(start_path)
    prefs_text = _read_preserving_newlines(prefs_path) if prefs_path.exists() else ""
    updated_start = _patch_start_ini(start_text, host=host, port=port)
    updated_prefs = _patch_flat_key(prefs_text, "port", str(port))

    _atomic_write_text(start_path, updated_start)
    _atomic_write_text(prefs_path, updated_prefs)


def _validate_game_endpoint(host: str, port: int) -> None:
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("Game endpoint port is invalid.")
    if not isinstance(host, str) or not host or any(char in host for char in "\r\n\x00"):
        raise ValueError("Game endpoint host is invalid.")
    if host.casefold() == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Game endpoint must use a loopback host.") from exc
    if not address.is_loopback:
        raise ValueError("Game endpoint must use a loopback host.")


def _read_preserving_newlines(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        return handle.read()


def _newline_for(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _patch_start_ini(text: str, *, host: str, port: int) -> str:
    newline = _newline_for(text)
    had_final_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    main_start = next(
        (index for index, line in enumerate(lines) if line.strip().casefold() == "[main]"),
        None,
    )
    if main_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(("[main]", f"server={host}", f"port={port}"))
        return newline.join(lines) + newline

    main_end = next(
        (
            index
            for index in range(main_start + 1, len(lines))
            if lines[index].strip().startswith("[") and lines[index].strip().endswith("]")
        ),
        len(lines),
    )
    values = {"server": host, "port": str(port)}
    seen: set[str] = set()
    patched: list[str] = []
    for index, line in enumerate(lines):
        if main_start < index < main_end and "=" in line:
            raw_key = line.split("=", 1)[0]
            key = raw_key.strip().casefold()
            if key in values:
                if key in seen:
                    continue
                leading = raw_key[: len(raw_key) - len(raw_key.lstrip())]
                patched.append(f"{leading}{raw_key.strip()}={values[key]}")
                seen.add(key)
                continue
        patched.append(line)

    insertion = main_end - sum(
        1
        for line in lines[main_start + 1 : main_end]
        if "=" in line and line.split("=", 1)[0].strip().casefold() in values
        and line.split("=", 1)[0].strip().casefold() in seen
    ) + len(seen)
    missing = [f"{key}={values[key]}" for key in ("server", "port") if key not in seen]
    if missing:
        # Find the next section again after duplicate removal.
        insertion = next(
            (
                index
                for index in range(main_start + 1, len(patched))
                if patched[index].strip().startswith("[") and patched[index].strip().endswith("]")
            ),
            len(patched),
        )
        patched[insertion:insertion] = missing
    suffix = newline if had_final_newline or not text else ""
    return newline.join(patched) + suffix


def _patch_flat_key(text: str, key: str, value: str) -> str:
    newline = _newline_for(text)
    had_final_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    seen = False
    patched: list[str] = []
    for line in lines:
        if "=" in line and line.split("=", 1)[0].strip().casefold() == key.casefold():
            if seen:
                continue
            raw_key = line.split("=", 1)[0]
            leading = raw_key[: len(raw_key) - len(raw_key.lstrip())]
            patched.append(f"{leading}{raw_key.strip()}={value}")
            seen = True
        else:
            patched.append(line)
    if not seen:
        patched.append(f"{key}={value}")
    suffix = newline if had_final_newline or not text else ""
    return newline.join(patched) + suffix


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def list_profiles() -> list[str]:
    """List all profile names that have junctions."""
    if not PROFILES_ROOT.exists():
        return []
    return sorted([
        d.name for d in PROFILES_ROOT.iterdir()
        if d.is_dir() and (d / "tq").exists()
    ])
