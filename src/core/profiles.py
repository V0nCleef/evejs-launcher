"""Profile management via directory junctions.

Each account gets a junction pointing to the real EVE client install.
This gives each account a unique path → unique settings folder on first launch.
"""
import ipaddress
import json
import os
import re
import stat
from pathlib import Path
import uuid

from ..config import CONFIG_DIR
from .platform import create_directory_link, get_eve_settings_path, remove_directory_link

PROFILES_ROOT = CONFIG_DIR / "Profiles"
_REQUIRED_CORE_PUBLIC_SECTIONS = ("audio", "device", "generic", "ui")
_ATOMIC_TEMP_CREATE_ATTEMPTS = 8


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


def _path_entry_exists(path: Path) -> bool:
    """Return whether *path* exists without following a broken link."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _is_directory_link(path: Path) -> bool:
    """Return whether *path* is a symlink or Windows reparse-point link."""
    metadata = path.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


def _same_directory_target(link: Path, target: Path) -> bool:
    """Compare a directory link and target using filesystem identity."""
    try:
        return os.path.samefile(link, target)
    except OSError:
        return False


def _ensure_profile_junction(junction: Path, target: Path) -> None:
    """Create or safely rebind one launcher-owned profile junction."""
    if not target.is_dir():
        raise FileNotFoundError(f"EVE client directory does not exist: {target}")

    if not _path_entry_exists(junction):
        create_directory_link(target, junction)
        return

    if not _is_directory_link(junction):
        raise RuntimeError(
            f"Refusing to replace non-junction profile path: {junction}"
        )

    if _same_directory_target(junction, target):
        return

    previous_target: Path | None
    try:
        previous_target = junction.resolve(strict=True)
    except OSError:
        previous_target = None

    remove_directory_link(junction)
    try:
        create_directory_link(target, junction)
    except Exception as bind_error:
        if _path_entry_exists(junction):
            raise RuntimeError(
                f"Failed to rebind profile junction and an unexpected path remains: "
                f"{junction}"
            ) from bind_error
        if previous_target is None:
            raise RuntimeError(
                f"Failed to rebind profile junction to {target}; the previous "
                "junction target could not be resolved for rollback."
            ) from bind_error
        try:
            create_directory_link(previous_target, junction)
        except Exception as rollback_error:
            raise RuntimeError(
                f"Failed to rebind profile junction to {target}, and restoring "
                f"the previous target {previous_target} also failed: {rollback_error}"
            ) from bind_error
        raise RuntimeError(
            f"Failed to rebind profile junction to {target}; the previous target "
            f"{previous_target} was restored."
        ) from bind_error


def create_profile(
    username: str,
    real_client_path: str,
    profiles_root: Path | None = None,
) -> Path:
    """Create or rebind a junction profile for the given account.

    Bootstraps only safe text settings. Binary account caches and browser state
    remain isolated to the account that created them.

    Args:
        username: Account username (used as profile folder name).
        real_client_path: Path to the real EVE client's tq folder.
        profiles_root: Optional profile root captured by the launch request.

    Returns:
        Path to the profile directory.
    """
    root = PROFILES_ROOT if profiles_root is None else Path(profiles_root)
    profile_dir = root / username
    profile_dir.mkdir(parents=True, exist_ok=True)

    junction = profile_dir / "tq"
    _ensure_profile_junction(junction, Path(real_client_path))

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

    if _path_entry_exists(junction):
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

    if yaml_path.exists() and template_yaml.exists():
        existing_yaml = yaml_path.read_text(encoding="utf-8", errors="replace")
        template_text = template_yaml.read_text(encoding="utf-8", errors="replace")
        repaired_yaml = _repair_core_public_yaml(existing_yaml, template_text)
        if repaired_yaml != existing_yaml:
            # Older launcher releases could leave a minimal YAML document that
            # makes EVE omit the password field and Login button. Preserve the
            # original once, then add only missing launcher-owned defaults.
            backup = yaml_path.with_name(f"{yaml_path.name}.launcher-backup")
            if not backup.exists():
                import shutil
                shutil.copy2(yaml_path, backup)
            _atomic_write_text(yaml_path, repaired_yaml)

    import time
    ts = int(time.time() * 10_000_000)  # EVE uses 100-nanosecond intervals
    yaml_username = json.dumps(str(username), ensure_ascii=False)

    # Patch the username under the ``ui:`` section
    if yaml_path.exists():
        text = yaml_path.read_text(encoding="utf-8", errors="replace")
        text = _patch_core_public_login(text, ts, yaml_username)
        _atomic_write_text(yaml_path, text)


def _named_block_ranges(
    lines: list[str],
    pattern: re.Pattern[str],
    *,
    start: int = 0,
    end: int | None = None,
) -> dict[str, tuple[int, int]]:
    """Index first-occurrence YAML blocks matched at one exact indentation."""
    limit = len(lines) if end is None else end
    starts: list[tuple[str, int]] = []
    for index in range(start, limit):
        match = pattern.match(lines[index])
        if match:
            starts.append((match.group(1).strip(), index))
    ranges: dict[str, tuple[int, int]] = {}
    for position, (name, block_start) in enumerate(starts):
        block_end = starts[position + 1][1] if position + 1 < len(starts) else limit
        ranges.setdefault(name, (block_start, block_end))
    return ranges


_TOP_LEVEL_YAML_KEY = re.compile(r"^([^#\s][^:]*):(.*)$")
_CHILD_YAML_KEY = re.compile(r"^  ([^#\s-][^:]*):(.*)$")


def _join_yaml_lines(lines: list[str], *, final_newline: bool) -> str:
    text = "\n".join(lines)
    return text + "\n" if final_newline and text else text


def _repair_core_public_yaml(existing: str, template: str) -> str:
    """Merge missing required settings from the complete launcher template.

    Existing values and unknown keys are retained. A required section whose
    header is a scalar/list instead of a mapping is replaced with that section's
    template block because EVE cannot consume its child settings in that shape.
    """
    lines = existing.splitlines()
    template_lines = template.splitlines()
    template_sections = _named_block_ranges(template_lines, _TOP_LEVEL_YAML_KEY)
    if any(section not in template_sections for section in _REQUIRED_CORE_PUBLIC_SECTIONS):
        return existing

    for section in _REQUIRED_CORE_PUBLIC_SECTIONS:
        sections = _named_block_ranges(lines, _TOP_LEVEL_YAML_KEY)
        template_start, template_end = template_sections[section]
        template_block = template_lines[template_start:template_end]
        if section not in sections:
            lines.extend(template_block)
            continue

        section_start, section_end = sections[section]
        header_value = lines[section_start].split(":", 1)[1].strip()
        if header_value and not (section == "generic" and header_value == "{}"):
            lines[section_start:section_end] = template_block
            continue
        template_children = _named_block_ranges(
            template_lines,
            _CHILD_YAML_KEY,
            start=template_start + 1,
            end=template_end,
        )
        existing_children = _named_block_ranges(
            lines,
            _CHILD_YAML_KEY,
            start=section_start + 1,
            end=section_end,
        )
        if section == "generic":
            if header_value == "{}" or existing_children:
                continue
            lines[section_start:section_end] = template_block
            continue

        missing_blocks: list[str] = []
        for child, (child_start, child_end) in template_children.items():
            if child not in existing_children:
                missing_blocks.extend(template_lines[child_start:child_end])
        if missing_blocks:
            lines[section_end:section_end] = missing_blocks

    return _join_yaml_lines(
        lines,
        final_newline=existing.endswith(("\n", "\r")) or template.endswith(("\n", "\r")),
    )


def _patch_core_public_login(text: str, timestamp: int, yaml_username: str) -> str:
    """Upsert EVE's canonical username history fields inside the ``ui`` map."""
    lines = text.splitlines()
    sections = _named_block_ranges(lines, _TOP_LEVEL_YAML_KEY)
    ui_range = sections.get("ui")
    if ui_range is None:
        return text
    ui_start, ui_end = ui_range
    children = _named_block_ranges(
        lines,
        _CHILD_YAML_KEY,
        start=ui_start + 1,
        end=ui_end,
    )
    replacements = {
        "username": [f"  username: [{timestamp}, {yaml_username}]"],
        "usernames": [
            "  usernames:",
            f"  - {timestamp}",
            f"  - [{yaml_username}]",
        ],
    }
    existing_replacements = [
        (children[name][0], children[name][1], replacement)
        for name, replacement in replacements.items()
        if name in children
    ]
    for block_start, block_end, replacement in sorted(existing_replacements, reverse=True):
        lines[block_start:block_end] = replacement

    sections = _named_block_ranges(lines, _TOP_LEVEL_YAML_KEY)
    ui_start, ui_end = sections["ui"]
    children = _named_block_ranges(
        lines,
        _CHILD_YAML_KEY,
        start=ui_start + 1,
        end=ui_end,
    )
    for name, replacement in replacements.items():
        if name not in children:
            lines[ui_end:ui_end] = replacement
            ui_end += len(replacement)

    return _join_yaml_lines(lines, final_newline=text.endswith(("\n", "\r")))


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

    # Avoid touching the shared client installation on every launch when its
    # endpoint is already correct. Besides preserving timestamps, this lets a
    # correctly configured read-only install launch normally.
    if updated_start != start_text:
        _atomic_write_text(start_path, updated_start)
    if updated_prefs != prefs_text:
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


def _create_atomic_temporary(path: Path) -> tuple[int, Path]:
    """Create one sibling temporary file without tempfile's Windows retry loop.

    CPython's ``tempfile._mkstemp_inner`` treats ``PermissionError`` as a
    possible name collision on Windows and can retry up to ``TMP_MAX`` times.
    A write-restricted EVE installation therefore used to peg one CPU core and
    leave the launcher stuck on LAUNCHING. Real permission errors must fail on
    the first attempt; only the vanishingly unlikely name collision is retried.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _attempt in range(_ATOMIC_TEMP_CREATE_ATTEMPTS):
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            return os.open(temporary, flags, 0o600), temporary
        except FileExistsError:
            continue
        except PermissionError as exc:
            raise PermissionError(
                f"Cannot update '{path.name}': Windows denied temporary-file "
                "creation beside it. Check that the copied client or profile "
                "folder is writable."
            ) from exc
    raise FileExistsError(
        f"Cannot update '{path.name}': unable to reserve a temporary file."
    )


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary = _create_atomic_temporary(path)
        with os.fdopen(
            descriptor,
            mode="w",
            encoding="utf-8",
            newline="",
        ) as handle:
            descriptor = None  # fdopen owns and closes it from here onward.
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                # Preserve the write/open failure that led us into cleanup.
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # The original failure is more useful than a cleanup failure.
                pass


def list_profiles() -> list[str]:
    """List all profile names that have junctions."""
    if not PROFILES_ROOT.exists():
        return []
    return sorted([
        d.name for d in PROFILES_ROOT.iterdir()
        if d.is_dir() and (d / "tq").exists()
    ])
