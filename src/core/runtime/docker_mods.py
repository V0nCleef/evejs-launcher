"""Deterministic, launcher-owned Docker Compose bridge for EveJS mods.

The bridge bind-mounts only the selected EveJS root's ``mods`` directory and
preloads active ``loader.js`` files through container-side ``NODE_OPTIONS``.
It never invokes Docker; callers separately request an allowlisted server
recreation after a changed override is written.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from typing import Iterable

from src.core.runtime.docker_compose import ComposeTarget
from src.core.service_status import DockerControlPolicy


_OVERRIDE_DIRECTORY = ".evejs-launcher"
_OVERRIDE_FILENAME = "compose.mods.yaml"
_TRANSACTION_FILENAME = "compose.mods.transaction.json"
_OWNED_HEADER = "# Managed by EveJS Launcher. Manual edits will be replaced."
_CONTAINER_MODS_ROOT = "/app/mods"
_NODE_OPTIONS_PREFIX = "      NODE_OPTIONS: "
_LOADER_FILENAMES = (
    "loader.js",
    "loader.js.disabled",
    "loader.js.off",
    "loader.js.bak",
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
MAX_DOCKER_MOD_OVERRIDE_BYTES = 512 * 1024
MAX_DOCKER_LOADER_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_DOCKER_MOD_TRANSACTION_BYTES = 4 * 1024


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

    evejs_root: Path
    override_path: Path
    selected_mods: tuple[str, ...]
    content_hash: str
    changed: bool
    requires_recreation: bool
    previous_content: bytes | None = field(repr=False)
    previous_content_sha256: str | None = field(repr=False)
    committed_content: bytes = field(repr=False)
    transaction_token: str | None = field(repr=False)


@dataclass(frozen=True)
class _DockerModTransaction:
    token: str
    previous_sha256: str | None
    desired_sha256: str
    content: bytes = field(repr=False)


def docker_mod_override_path(evejs_root: str | Path) -> Path:
    """Return the single launcher-owned override path for an EveJS root."""
    return Path(evejs_root).resolve() / _OVERRIDE_DIRECTORY / _OVERRIDE_FILENAME


def docker_mod_transaction_path(evejs_root: str | Path) -> Path:
    return Path(evejs_root).resolve() / _OVERRIDE_DIRECTORY / _TRANSACTION_FILENAME


def build_docker_mod_override(
    evejs_root: str | Path,
    selected_mods: Iterable[str],
) -> DockerModOverride:
    """Render the final Compose override for an exact ordered preload chain.

    The empty chain is material too: the launcher-owned file remains last in
    the Compose chain and explicitly clears ``NODE_OPTIONS``.  Removing the
    file would allow a base or user override to keep preloading a mod while the
    launcher falsely reported that every loader was disabled.
    """
    selected = _normalize_selection(selected_mods)

    mods_source = (Path(evejs_root).resolve() / "mods").resolve().as_posix()
    preload_paths = tuple(
        f"{_CONTAINER_MODS_ROOT}/{name}/loader.js" for name in selected
    )
    node_options = " ".join(
        f"--require {json.dumps(path, ensure_ascii=False)}"
        for path in preload_paths
    )
    lines = [
        _OWNED_HEADER,
        "services:",
        "  server:",
        "    environment:",
        f"      NODE_OPTIONS: {_yaml_scalar(node_options)}",
    ]
    if selected:
        lines.extend(
            (
                "    volumes:",
                "      - type: bind",
                f"        source: {_yaml_scalar(mods_source)}",
                f"        target: {_yaml_scalar(_CONTAINER_MODS_ROOT)}",
                "        read_only: false",
            )
        )
    lines.append("")
    content = "\n".join(lines)
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
    root = _canonical_existing_root(evejs_root)
    override_path = docker_mod_override_path(root)
    pending_transaction = _read_mod_transaction(root)
    _validate_active_loaders(root, selected)
    desired = build_docker_mod_override(root, selected)
    desired_bytes = desired.content.encode("utf-8")
    current_bytes = _read_owned_override(
        root,
        override_path,
        require_active_loaders=False,
    )
    if pending_transaction is not None:
        current_sha256 = (
            None if current_bytes is None else hashlib.sha256(current_bytes).hexdigest()
        )
        if (
            current_bytes == desired_bytes
            and pending_transaction.desired_sha256 == desired.content_hash
        ):
            # A prior launcher/power failure may have happened after the exact
            # commit but before the authorized lifecycle target consumed its
            # marker. Explicit Apply safely resumes that same transaction;
            # ordinary lifecycle paths remain blocked in the meantime.
            return DockerModApplyResult(
                root,
                override_path,
                selected,
                desired.content_hash,
                True,
                True,
                None,
                pending_transaction.previous_sha256,
                desired_bytes,
                pending_transaction.token,
            )
        if current_sha256 == pending_transaction.previous_sha256:
            _clear_mod_transaction(root, pending_transaction)
        else:
            raise DockerModBridgeError(
                "A previous Docker mod transaction does not match either its "
                "prior or desired override. Lifecycle operations remain blocked "
                "until the artifact is repaired."
            )
    changed = current_bytes != desired_bytes
    transaction: _DockerModTransaction | None = None
    if changed:
        transaction = _create_mod_transaction(
            root,
            previous=current_bytes,
            desired=desired_bytes,
        )
        try:
            _atomic_write(
                root,
                override_path,
                desired_bytes,
                require_active_loaders=True,
            )
            committed = _read_owned_override(
                root,
                override_path,
                require_active_loaders=True,
            )
            if committed != desired_bytes:
                raise DockerModBridgeError(
                    "The Docker mod override did not match the committed selection."
                )
        except Exception as exc:
            try:
                _recover_failed_apply(
                    root,
                    override_path,
                    previous=current_bytes,
                    desired=desired_bytes,
                )
                _clear_mod_transaction(root, transaction)
            except Exception as rollback_exc:
                raise DockerModBridgeError(
                    "Writing the Docker mod override failed and its prior state "
                    "could not be restored safely. The override is blocked until "
                    "it is repaired."
                ) from rollback_exc
            if isinstance(exc, DockerModBridgeError):
                raise
            raise DockerModBridgeError(
                "The Docker mod override could not be written safely."
            ) from exc
    return DockerModApplyResult(
        root,
        override_path,
        selected,
        desired.content_hash,
        changed,
        changed,
        current_bytes,
        None if current_bytes is None else hashlib.sha256(current_bytes).hexdigest(),
        desired_bytes,
        None if transaction is None else transaction.token,
    )


def rollback_docker_mod_override(
    result: DockerModApplyResult,
    *,
    policy: DockerControlPolicy,
) -> None:
    """Compare-and-swap one unlaunched Apply back to its exact prior bytes."""

    if policy is not DockerControlPolicy.MANAGED:
        raise PermissionError(
            "Connect-only Docker mode cannot change mod or Compose state."
        )
    if not isinstance(result, DockerModApplyResult):
        raise TypeError("result must be a DockerModApplyResult.")
    if not result.changed:
        return
    root = _canonical_existing_root(result.evejs_root)
    expected_path = docker_mod_override_path(root)
    if result.override_path != expected_path:
        raise DockerModBridgeError(
            "The Docker mod rollback belongs to a different EveJS root."
        )
    transaction = _require_result_transaction(root, result)
    if (
        result.previous_content is None
        and result.previous_content_sha256 is not None
    ):
        raise DockerModBridgeError(
            "The resumed Docker mod transaction cannot reconstruct its prior "
            "override bytes. Retry Apply to consume the validated transaction."
        )
    _restore_override(
        root,
        expected_path,
        expected_current=result.committed_content,
        previous=result.previous_content,
    )
    _clear_mod_transaction(root, transaction)


def finalize_docker_mod_override(
    result: DockerModApplyResult,
    *,
    policy: DockerControlPolicy,
) -> None:
    """Publish one validated changed override for ordinary lifecycle use.

    The marker remains durable until the caller has successfully handed the
    exact plan to a lifecycle worker. A crash or rollback failure before this
    point leaves every fresh attach path blocked across launcher restarts.
    """

    if policy is not DockerControlPolicy.MANAGED:
        raise PermissionError(
            "Connect-only Docker mode cannot change mod or Compose state."
        )
    if not isinstance(result, DockerModApplyResult):
        raise TypeError("result must be a DockerModApplyResult.")
    if not result.changed:
        return
    root = _canonical_existing_root(result.evejs_root)
    transaction = _require_result_transaction(root, result)
    current = _read_owned_override(
        root,
        result.override_path,
        require_active_loaders=True,
    )
    if current != result.committed_content:
        raise DockerModBridgeError(
            "The Docker mod override changed before transaction finalization."
        )
    _clear_mod_transaction(root, transaction)


def has_pending_docker_mod_transaction(result: DockerModApplyResult) -> bool:
    """Return whether this exact changed Apply still owns a durable marker."""

    if not isinstance(result, DockerModApplyResult):
        raise TypeError("result must be a DockerModApplyResult.")
    if not result.changed:
        return False
    root = _canonical_existing_root(result.evejs_root)
    transaction = _read_mod_transaction(root)
    if transaction is None:
        return False
    _require_result_transaction(root, result)
    return True


def attach_docker_mod_override(
    target: ComposeTarget,
    *,
    transaction_token: str | None = None,
) -> ComposeTarget:
    """Append only a safe, exact launcher-rendered mod override.

    A present but invalid artifact fails closed. Silently ignoring it would let
    another lifecycle path attach different Compose semantics than the UI saw.
    """

    root = _canonical_existing_root(target.project_directory)
    override_path = docker_mod_override_path(root)
    transaction = _read_mod_transaction(root)
    content = _read_owned_override(
        root,
        override_path,
        require_active_loaders=True,
    )
    if transaction is None and transaction_token is not None:
        raise DockerModBridgeError(
            "The supplied Docker mod transaction authorization is stale."
        )
    if transaction is not None:
        if (
            type(transaction_token) is not str
            or not hmac.compare_digest(transaction.token, transaction_token)
        ):
            raise DockerModBridgeError(
                "The Docker mod override has an unfinished launcher transaction. "
                "Lifecycle attachment is blocked until it is finalized or rolled back."
            )
        if (
            content is None
            or hashlib.sha256(content).hexdigest() != transaction.desired_sha256
        ):
            raise DockerModBridgeError(
                "The authorized Docker mod override no longer matches its transaction."
            )
    if content is None:
        return target
    if override_path in target.override_files:
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
        name = raw_name
        if (
            not name
            or name != name.strip()
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
    for name in selected:
        candidates = _loader_candidates(root, name)
        if len(candidates) != 1 or candidates[0].name != "loader.js":
            raise DockerModBridgeError(
                "A selected mod must have exactly one active loader.js."
            )
        _read_stable_regular_file(
            candidates[0],
            root,
            maximum=MAX_DOCKER_LOADER_PAYLOAD_BYTES,
            label="selected Docker loader",
        )


def _loader_candidates(root: Path, name: str) -> tuple[Path, ...]:
    mods_root = root / "mods"
    try:
        _require_safe_directory(mods_root, root, "Docker mods directory")
    except DockerModBridgeError as exc:
        raise DockerModBridgeError(
            "The Docker loader root is unavailable or unsafe."
        ) from exc
    folder = mods_root / name
    try:
        _require_safe_directory(folder, root, "Docker mod directory")
    except DockerModBridgeError as exc:
        raise DockerModBridgeError(
            f"The Docker loader directory for {name!r} is unavailable or unsafe."
        ) from exc
    candidates: list[Path] = []
    for filename in _LOADER_FILENAMES:
        candidate = folder / filename
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DockerModBridgeError(
                "A Docker loader payload could not be inspected."
            ) from exc
        candidates.append(candidate)
    return tuple(candidates)


def _yaml_scalar(value: str) -> str:
    # Compose interpolates dollar signs even in YAML double-quoted strings.
    return json.dumps(value.replace("$", "$$"), ensure_ascii=False)


def _read_owned_override(
    root: Path,
    path: Path,
    *,
    require_active_loaders: bool,
) -> bytes | None:
    if path != docker_mod_override_path(root):
        raise DockerModBridgeError("The Docker mod override path is not canonical.")
    parent_exists = _require_safe_override_parent(root, create=False)
    try:
        path.lstat()
    except FileNotFoundError:
        if parent_exists:
            # lstat distinguishes a genuinely absent file from a broken link.
            return None
        return None
    except OSError as exc:
        raise DockerModBridgeError(
            "The launcher-owned Docker mod override could not be inspected."
        ) from exc
    content = _read_stable_regular_file(
        path,
        root,
        maximum=MAX_DOCKER_MOD_OVERRIDE_BYTES,
        label="Docker mod override",
    )
    rendered = _parse_owned_override(root, content)
    if require_active_loaders:
        _validate_active_loaders(root, rendered.selected_mods)
    return content


def _render_mod_transaction(
    *,
    token: str,
    previous_sha256: str | None,
    desired_sha256: str,
) -> bytes:
    payload = {
        "desiredSha256": desired_sha256,
        "previousSha256": previous_sha256,
        "schemaVersion": 1,
        "token": token,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def _parse_mod_transaction(content: bytes) -> _DockerModTransaction:
    try:
        text = content.decode("ascii", errors="strict")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DockerModBridgeError(
            "The Docker mod transaction marker is malformed."
        ) from exc
    if type(payload) is not dict or set(payload) != {
        "desiredSha256",
        "previousSha256",
        "schemaVersion",
        "token",
    }:
        raise DockerModBridgeError(
            "The Docker mod transaction marker has an invalid schema."
        )
    token = payload["token"]
    previous_sha256 = payload["previousSha256"]
    desired_sha256 = payload["desiredSha256"]
    if (
        payload["schemaVersion"] != 1
        or type(payload["schemaVersion"]) is not int
        or type(token) is not str
        or len(token) != 64
        or any(character not in "0123456789abcdef" for character in token)
        or (
            previous_sha256 is not None
            and not _is_sha256(previous_sha256)
        )
        or not _is_sha256(desired_sha256)
    ):
        raise DockerModBridgeError(
            "The Docker mod transaction marker has invalid values."
        )
    expected = _render_mod_transaction(
        token=token,
        previous_sha256=previous_sha256,
        desired_sha256=desired_sha256,
    )
    if content != expected:
        raise DockerModBridgeError(
            "The Docker mod transaction marker is not canonical."
        )
    return _DockerModTransaction(
        token=token,
        previous_sha256=previous_sha256,
        desired_sha256=desired_sha256,
        content=content,
    )


def _is_sha256(value: object) -> bool:
    return bool(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_mod_transaction(root: Path) -> _DockerModTransaction | None:
    path = docker_mod_transaction_path(root)
    parent_exists = _require_safe_override_parent(root, create=False)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DockerModBridgeError(
            "The Docker mod transaction marker could not be inspected."
        ) from exc
    if not parent_exists:
        raise DockerModBridgeError(
            "The Docker mod transaction marker has no safe parent directory."
        )
    content = _read_stable_regular_file(
        path,
        root,
        maximum=MAX_DOCKER_MOD_TRANSACTION_BYTES,
        label="Docker mod transaction marker",
    )
    return _parse_mod_transaction(content)


def _create_mod_transaction(
    root: Path,
    *,
    previous: bytes | None,
    desired: bytes,
) -> _DockerModTransaction:
    path = docker_mod_transaction_path(root)
    transaction = _DockerModTransaction(
        token=secrets.token_hex(32),
        previous_sha256=(
            None if previous is None else hashlib.sha256(previous).hexdigest()
        ),
        desired_sha256=hashlib.sha256(desired).hexdigest(),
        content=b"",
    )
    transaction = replace(
        transaction,
        content=_render_mod_transaction(
            token=transaction.token,
            previous_sha256=transaction.previous_sha256,
            desired_sha256=transaction.desired_sha256,
        ),
    )
    _require_safe_override_parent(root, create=True)
    try:
        with path.open("xb") as stream:
            stream.write(transaction.content)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise DockerModBridgeError(
            "A Docker mod transaction marker already exists."
        ) from exc
    except OSError as exc:
        # A partial marker deliberately remains fail-closed if exclusive
        # creation succeeded but the durable write did not finish.
        raise DockerModBridgeError(
            "The Docker mod transaction marker could not be written safely."
        ) from exc
    committed = _read_mod_transaction(root)
    if committed != transaction:
        raise DockerModBridgeError(
            "The Docker mod transaction marker changed during creation."
        )
    return transaction


def _clear_mod_transaction(
    root: Path,
    expected: _DockerModTransaction,
) -> None:
    current = _read_mod_transaction(root)
    if current != expected:
        raise DockerModBridgeError(
            "The Docker mod transaction marker changed before completion."
        )
    path = docker_mod_transaction_path(root)
    try:
        path.unlink()
    except OSError as exc:
        raise DockerModBridgeError(
            "The Docker mod transaction marker could not be cleared."
        ) from exc
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DockerModBridgeError(
            "Docker mod transaction cleanup could not verify marker absence."
        ) from exc
    raise DockerModBridgeError(
        "The Docker mod transaction marker remained after completion."
    )


def _require_result_transaction(
    root: Path,
    result: DockerModApplyResult,
) -> _DockerModTransaction:
    transaction = _read_mod_transaction(root)
    if transaction is None or type(result.transaction_token) is not str:
        raise DockerModBridgeError(
            "The Docker mod transaction marker is missing."
        )
    expected_previous = result.previous_content_sha256
    if result.previous_content is not None and expected_previous != hashlib.sha256(
        result.previous_content
    ).hexdigest():
        raise DockerModBridgeError(
            "The Apply result's prior override hash is inconsistent."
        )
    expected_desired = hashlib.sha256(result.committed_content).hexdigest()
    if (
        not hmac.compare_digest(transaction.token, result.transaction_token)
        or transaction.previous_sha256 != expected_previous
        or transaction.desired_sha256 != expected_desired
        or result.content_hash != expected_desired
    ):
        raise DockerModBridgeError(
            "The Docker mod transaction does not belong to this Apply result."
        )
    return transaction


def _parse_owned_override(root: Path, content: bytes) -> DockerModOverride:
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DockerModBridgeError(
            "The Docker mod override is not strict UTF-8 text."
        ) from exc
    lines = text.splitlines()
    if len(lines) < 5 or lines[0] != _OWNED_HEADER:
        raise DockerModBridgeError(
            "The Docker mod override is not an exact launcher-owned document."
        )
    node_options_line = lines[4]
    if not node_options_line.startswith(_NODE_OPTIONS_PREFIX):
        raise DockerModBridgeError(
            "The Docker mod override has no exact NODE_OPTIONS declaration."
        )
    scalar = node_options_line[len(_NODE_OPTIONS_PREFIX) :]
    try:
        encoded_node_options = json.loads(scalar)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise DockerModBridgeError(
            "The Docker mod override NODE_OPTIONS value is invalid."
        ) from exc
    if type(encoded_node_options) is not str:
        raise DockerModBridgeError(
            "The Docker mod override NODE_OPTIONS value must be text."
        )
    node_options = encoded_node_options.replace("$$", "$")
    selected = _selection_from_node_options(node_options)
    expected = build_docker_mod_override(root, selected)
    if content != expected.content.encode("utf-8"):
        raise DockerModBridgeError(
            "The Docker mod override differs from the exact launcher renderer."
        )
    return expected


def _selection_from_node_options(value: str) -> tuple[str, ...]:
    if value == "":
        return ()
    selected: list[str] = []
    decoder = json.JSONDecoder()
    cursor = 0
    prefix = "--require "
    while cursor < len(value):
        if not value.startswith(prefix, cursor):
            raise DockerModBridgeError(
                "The Docker mod override contains unsupported NODE_OPTIONS."
            )
        cursor += len(prefix)
        try:
            preload_path, end = decoder.raw_decode(value, cursor)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise DockerModBridgeError(
                "The Docker mod override contains an invalid preload path."
            ) from exc
        if type(preload_path) is not str:
            raise DockerModBridgeError(
                "The Docker mod override preload path must be text."
            )
        path_prefix = _CONTAINER_MODS_ROOT + "/"
        path_suffix = "/loader.js"
        if not (
            preload_path.startswith(path_prefix)
            and preload_path.endswith(path_suffix)
        ):
            raise DockerModBridgeError(
                "The Docker mod override preload path is outside /app/mods."
            )
        name = preload_path[len(path_prefix) : -len(path_suffix)]
        selected.append(name)
        cursor = end
        if cursor == len(value):
            break
        if value[cursor] != " ":
            raise DockerModBridgeError(
                "The Docker mod override preload chain is malformed."
            )
        cursor += 1
    return _normalize_selection(selected)


def _canonical_existing_root(value: str | Path) -> Path:
    try:
        root = Path(value).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise DockerModBridgeError("The selected EveJS root is unavailable.") from exc
    if not root.is_dir():
        raise DockerModBridgeError("The selected EveJS root is not a directory.")
    return root


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _require_safe_directory(path: Path, root: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DockerModBridgeError(f"The {label} is unavailable.") from exc
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata) or not stat.S_ISDIR(
        metadata.st_mode
    ):
        raise DockerModBridgeError(f"The {label} is unsafe.")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DockerModBridgeError(f"The {label} escapes the EveJS root.") from exc
    if resolved != path:
        raise DockerModBridgeError(f"The {label} is not canonical.")


def _require_safe_override_parent(root: Path, *, create: bool) -> bool:
    parent = root / _OVERRIDE_DIRECTORY
    try:
        parent.lstat()
    except FileNotFoundError:
        if not create:
            return False
        try:
            parent.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise DockerModBridgeError(
                "The Docker mod override directory could not be created."
            ) from exc
    except OSError as exc:
        raise DockerModBridgeError(
            "The Docker mod override directory could not be inspected."
        ) from exc
    _require_safe_directory(parent, root, "Docker mod override directory")
    return True


def _read_stable_regular_file(
    path: Path,
    root: Path,
    *,
    maximum: int,
    label: str,
) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise DockerModBridgeError(f"The {label} is unavailable.") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or _is_reparse(before)
        or not stat.S_ISREG(before.st_mode)
    ):
        raise DockerModBridgeError(f"The {label} is not a safe regular file.")
    if before.st_size > maximum:
        raise DockerModBridgeError(f"The {label} exceeds its size limit.")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise DockerModBridgeError(f"The {label} escapes the EveJS root.") from exc
    if resolved != path:
        raise DockerModBridgeError(f"The {label} is not canonical.")
    try:
        with path.open("rb") as stream:
            content = stream.read(maximum + 1)
            opened = os.fstat(stream.fileno())
        after = path.lstat()
    except OSError as exc:
        raise DockerModBridgeError(f"The {label} could not be read.") from exc
    if len(content) > maximum:
        raise DockerModBridgeError(f"The {label} exceeds its size limit.")
    if (
        stat.S_ISLNK(after.st_mode)
        or _is_reparse(after)
        or not stat.S_ISREG(after.st_mode)
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or opened.st_size != len(content)
        or after.st_size != len(content)
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise DockerModBridgeError(f"The {label} changed while it was read.")
    return content


def _recover_failed_apply(
    root: Path,
    path: Path,
    *,
    previous: bytes | None,
    desired: bytes,
) -> None:
    current = _read_owned_override(
        root,
        path,
        require_active_loaders=False,
    )
    if current == previous:
        return
    if current != desired:
        raise DockerModBridgeError(
            "The Docker mod override changed after the failed write."
        )
    _restore_override(
        root,
        path,
        expected_current=desired,
        previous=previous,
    )


def _restore_override(
    root: Path,
    path: Path,
    *,
    expected_current: bytes,
    previous: bytes | None,
) -> None:
    current = _read_owned_override(
        root,
        path,
        require_active_loaders=False,
    )
    if current != expected_current:
        raise DockerModBridgeError(
            "The Docker mod override changed before rollback."
        )
    if previous is None:
        try:
            path.unlink()
        except OSError as exc:
            raise DockerModBridgeError(
                "The new Docker mod override could not be withdrawn."
            ) from exc
        try:
            path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise DockerModBridgeError(
                "Docker mod rollback could not verify file absence."
            ) from exc
        raise DockerModBridgeError(
            "The new Docker mod override remained after rollback."
        )
    _atomic_write(
        root,
        path,
        previous,
        require_active_loaders=False,
    )
    if _read_owned_override(
        root,
        path,
        require_active_loaders=False,
    ) != previous:
        raise DockerModBridgeError(
            "The prior Docker mod override was not restored exactly."
        )


def _atomic_write(
    root: Path,
    path: Path,
    content: bytes,
    *,
    require_active_loaders: bool,
) -> None:
    if path != docker_mod_override_path(root):
        raise DockerModBridgeError("The Docker mod override path is not canonical.")
    _require_safe_override_parent(root, create=True)
    # Refuse to publish bytes that are not exactly one supported renderer output.
    rendered = _parse_owned_override(root, content)
    if require_active_loaders:
        _validate_active_loaders(root, rendered.selected_mods)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        _require_safe_override_parent(root, create=False)
        if _read_stable_regular_file(
            temporary_path,
            root,
            maximum=MAX_DOCKER_MOD_OVERRIDE_BYTES,
            label="temporary Docker mod override",
        ) != content:
            raise DockerModBridgeError(
                "The temporary Docker mod override changed before commit."
            )
        os.replace(temporary_path, path)
        temporary_path = None
        _require_safe_override_parent(root, create=False)
    finally:
        if temporary_path is not None:
            _remove_safe_temporary(temporary_path, root, path)


def _remove_safe_temporary(temporary: Path, root: Path, destination: Path) -> None:
    try:
        metadata = temporary.lstat()
    except FileNotFoundError:
        return
    except OSError:
        return
    if (
        temporary.parent != destination.parent
        or not temporary.name.startswith(f".{destination.name}.")
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or not stat.S_ISREG(metadata.st_mode)
    ):
        return
    try:
        temporary.resolve(strict=True).relative_to(root)
        temporary.unlink()
    except OSError:
        return
