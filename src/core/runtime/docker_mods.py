"""Deterministic, launcher-owned Docker Compose bridge for EveJS mods.

The bridge bind-mounts only the selected EveJS root's ``mods`` directory and
preloads active ``loader.js`` files through container-side ``NODE_OPTIONS``.
It never invokes Docker; callers separately request an allowlisted server
recreation after a changed override is written.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable

from src.core.runtime.docker_compose import ComposeTarget
from src.core.service_status import DockerControlPolicy


_OVERRIDE_DIRECTORY = ".evejs-launcher"
_OVERRIDE_FILENAME = "compose.mods.yaml"
_OWNED_HEADER = "# Managed by EveJS Launcher. Manual edits will be replaced."
_CONTAINER_MODS_ROOT = "/app/mods"


class DockerModBridgeError(ValueError):
    """A selected mod cannot be represented by the safe preload bridge."""


@dataclass(frozen=True)
class DockerModOverride:
    """Pure deterministic override material for one ordered mod selection."""

    selected_mods: tuple[str, ...]
    node_options: str
    content: str
    content_hash: str


@dataclass(frozen=True)
class DockerModApplyResult:
    """Filesystem outcome and whether the server must be recreated."""

    override_path: Path
    selected_mods: tuple[str, ...]
    content_hash: str | None
    changed: bool
    requires_recreation: bool


def docker_mod_override_path(evejs_root: str | Path) -> Path:
    """Return the single launcher-owned override path for an EveJS root."""
    return Path(evejs_root).resolve() / _OVERRIDE_DIRECTORY / _OVERRIDE_FILENAME


def build_docker_mod_override(
    evejs_root: str | Path,
    selected_mods: Iterable[str],
) -> DockerModOverride:
    """Render a stable Compose override while preserving selected preload order."""
    selected = _normalize_selection(selected_mods)
    if not selected:
        raise DockerModBridgeError("At least one selected mod is required.")

    mods_source = (Path(evejs_root).resolve() / "mods").resolve().as_posix()
    preload_paths = tuple(
        f"{_CONTAINER_MODS_ROOT}/{name}/loader.js" for name in selected
    )
    node_options = " ".join(
        f"--require {json.dumps(path, ensure_ascii=False)}"
        for path in preload_paths
    )
    content = "\n".join(
        (
            _OWNED_HEADER,
            "services:",
            "  server:",
            "    environment:",
            f"      NODE_OPTIONS: {_yaml_scalar(node_options)}",
            "    volumes:",
            "      - type: bind",
            f"        source: {_yaml_scalar(mods_source)}",
            f"        target: {_yaml_scalar(_CONTAINER_MODS_ROOT)}",
            "        read_only: false",
            "",
        )
    )
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return DockerModOverride(selected, node_options, content, content_hash)


def apply_docker_mod_override(
    evejs_root: str | Path,
    selected_mods: Iterable[str],
    *,
    policy: DockerControlPolicy,
) -> DockerModApplyResult:
    """Atomically write/remove the owned override under managed policy only."""
    if policy is not DockerControlPolicy.MANAGED:
        raise PermissionError(
            "Connect-only Docker mode cannot change mod or Compose state."
        )

    selected = _normalize_selection(selected_mods)
    root = Path(evejs_root).resolve()
    override_path = docker_mod_override_path(root)
    if selected:
        _validate_active_loaders(root, selected)
        desired = build_docker_mod_override(root, selected)
        desired_bytes = desired.content.encode("utf-8")
        current_bytes = _read_owned_override(override_path)
        changed = current_bytes != desired_bytes
        if changed:
            _atomic_write(override_path, desired.content)
        return DockerModApplyResult(
            override_path,
            selected,
            desired.content_hash,
            changed,
            changed,
        )

    changed = False
    if override_path.exists():
        _read_owned_override(override_path)
        override_path.unlink()
        changed = True
    return DockerModApplyResult(
        override_path,
        (),
        None,
        changed,
        changed,
    )


def attach_docker_mod_override(target: ComposeTarget) -> ComposeTarget:
    """Append the owned mod override after every user-selected Compose file."""
    override_path = docker_mod_override_path(target.project_directory)
    if not override_path.is_file() or override_path in target.override_files:
        return target
    return replace(
        target,
        override_files=(*target.override_files, override_path),
    )


def _normalize_selection(selected_mods: Iterable[str]) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    for raw_name in selected_mods:
        if not isinstance(raw_name, str):
            raise DockerModBridgeError("Selected mod names must be text.")
        name = raw_name.strip()
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\0" in name
            or any(ord(character) < 32 for character in name)
        ):
            raise DockerModBridgeError(
                "A selected mod name cannot be represented safely."
            )
        key = name.casefold()
        if key in seen:
            raise DockerModBridgeError("Selected mods must be unique.")
        seen.add(key)
        selected.append(name)
    return tuple(selected)


def _validate_active_loaders(root: Path, selected: tuple[str, ...]) -> None:
    mods_root = root / "mods"
    for name in selected:
        loader = mods_root / name / "loader.js"
        if not loader.is_file():
            raise DockerModBridgeError(
                "A selected mod does not have an active loader.js."
            )


def _yaml_scalar(value: str) -> str:
    # Compose interpolates dollar signs even in YAML double-quoted strings.
    return json.dumps(value.replace("$", "$$"), ensure_ascii=False)


def _read_owned_override(path: Path) -> bytes | None:
    if not path.exists():
        return None
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise DockerModBridgeError(
            "The launcher-owned Docker mod override could not be read."
        ) from exc
    if not content.startswith(_OWNED_HEADER.encode("utf-8")):
        raise DockerModBridgeError(
            "The Docker mod override path contains a non-launcher file."
        )
    return content


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
