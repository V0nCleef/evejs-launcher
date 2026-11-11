"""Versioned, package-neutral declarations for container helpers and images."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

ACTIONS = frozenset({"install", "verify", "prepare_host", "prepare_profile", "prepare_server_start",
    "prepare_disable", "prepare_remove", "recover", "prepare_update", "apply_update",
    "commit_update", "rollback_update"})


@dataclass(frozen=True)
class DockerImageSpec:
    service: str
    recipe: str
    source_directory: Path
    manifest: str
    binary: str
    destination: str
    toolchain: str
    overlays: tuple[tuple[str, str, str, str], ...] = ()


@dataclass(frozen=True)
class DockerModSpec:
    data_directory: str
    helper_path: Path
    server_actions: tuple[str, ...]
    host_actions: tuple[str, ...]
    image: DockerImageSpec | None = None


def _relative(value):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Docker package paths must be safe relative paths.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("Docker package paths must remain inside the package.")
    return path


def _package_path(folder, value, *, file):
    relative = _relative(value)
    path = Path(folder)
    for part in relative.parts:
        path = path / part
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Docker package paths cannot traverse links.")
    path = path.resolve(strict=True)
    path.relative_to(Path(folder).resolve(strict=True))
    if not (path.is_file() if file else path.is_dir()):
        raise ValueError("Docker package input has the wrong type.")
    return path


def parse_docker_mod_spec(payload, folder) -> DockerModSpec:
    if (not isinstance(payload, dict) or not {"schemaVersion", "dataDirectory", "serverHelper", "hostActions"} <= set(payload)
            or set(payload) - {"schemaVersion", "dataDirectory", "serverHelper", "hostActions", "image"}
            or type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1):
        raise ValueError("Unsupported Docker mod contract.")
    data = str(_relative(payload["dataDirectory"]))
    if not re.fullmatch(r"[a-z0-9][a-z0-9_./-]{0,127}", data):
        raise ValueError("Docker dataDirectory must be a bounded stable directory.")
    helper = payload["serverHelper"]
    if not isinstance(helper, dict) or set(helper) != {"runtime", "path", "actions"} or helper["runtime"] != "node":
        raise ValueError("Docker serverHelper requires a Node helper and explicit actions.")
    groups = (helper["actions"], payload["hostActions"])
    for values in groups:
        if not isinstance(values, list) or len(values) > len(ACTIONS) or any(type(v) is not str or v not in ACTIONS for v in values) or len(set(values)) != len(values):
            raise ValueError("Docker helper actions must be distinct supported lifecycle actions.")
    if set(groups[0]) & set(groups[1]):
        raise ValueError("A Docker helper action must have exactly one execution location.")
    helper_path = _package_path(folder, helper["path"], file=True)
    if helper_path.suffix not in {".js", ".cjs"} or helper_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Docker helper must be a bounded JavaScript package file.")
    image = None
    if "image" in payload:
        raw = payload["image"]
        if isinstance(raw, dict) and raw.get("recipe") == "source-overlay-v1":
            if set(raw) != {"service", "recipe", "sourceDirectory", "files"} or raw["service"] != "server":
                raise ValueError("Source overlays require an explicit server contribution.")
            source = _package_path(folder, raw["sourceDirectory"], file=False)
            files = raw["files"]
            if not isinstance(files, list) or not 1 <= len(files) <= 64:
                raise ValueError("Source overlays require 1-64 version-checked files.")
            overlays, destinations = [], set()
            for entry in files:
                if (not isinstance(entry, dict) or set(entry) != {"source", "destination", "baseSha256", "sha256"}
                        or not isinstance(entry["destination"], str) or not entry["destination"].startswith("/app/server/")
                        or any(not isinstance(entry[k], str) or not re.fullmatch(r"[0-9a-f]{64}", entry[k]) for k in ("baseSha256", "sha256"))):
                    raise ValueError("Each overlay needs an explicit source, server destination and before/after SHA-256.")
                _relative(entry["destination"][len("/app/"):])
                if entry["destination"] in destinations:
                    raise ValueError("Overlay destinations must be unique.")
                destinations.add(entry["destination"])
                item = _package_path(source, entry["source"], file=True)
                import hashlib
                if item.stat().st_size > 2 * 1024 * 1024 or hashlib.sha256(item.read_bytes()).hexdigest() != entry["sha256"]:
                    raise ValueError("Source overlay bytes do not match their declared fingerprint.")
                overlays.append((str(_relative(entry["source"])), entry["destination"], entry["baseSha256"], entry["sha256"]))
            image = DockerImageSpec("server", "source-overlay-v1", source, "", "", "", "", tuple(overlays))
            return DockerModSpec(data, helper_path, tuple(groups[0]), tuple(groups[1]), image)
        keys = {"service", "recipe", "sourceDirectory", "manifest", "binary", "destination", "toolchain"}
        if not isinstance(raw, dict) or set(raw) != keys or raw["service"] not in {"server", "market"} or raw["recipe"] != "cargo-release-v1":
            raise ValueError("Unsupported Docker image contribution recipe or role.")
        if (not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", str(raw["binary"]))
                or raw["destination"] != "/usr/local/bin/" + raw["binary"]
                or not re.fullmatch(r"rust:[0-9]+\.[0-9]+(?:\.[0-9]+)?-bookworm", str(raw["toolchain"]))):
            raise ValueError("Docker image contribution has an invalid artifact or toolchain.")
        source = _package_path(folder, raw["sourceDirectory"], file=False)
        if not re.fullmatch(r"[A-Za-z0-9_./-]+\.toml", str(raw["manifest"])):
            raise ValueError("Docker Cargo manifest must be a safe relative TOML file.")
        _package_path(source, raw["manifest"], file=True)
        _package_path(source, "Cargo.lock", file=True)
        image = DockerImageSpec(raw["service"], raw["recipe"], source, str(_relative(raw["manifest"])),
                               raw["binary"], raw["destination"], raw["toolchain"])
    return DockerModSpec(data, helper_path, tuple(groups[0]), tuple(groups[1]), image)
