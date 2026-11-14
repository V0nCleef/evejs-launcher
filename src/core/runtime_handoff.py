"""One-use, user-bound ownership transfer across a launcher self-update.

The checkpoint lives outside the replaceable application bundle. No secrets,
port-based process discovery, service commands, or container lifecycle commands
are used. A failed update leaves it available for a manual recovery launch.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import time
import uuid

from .. import config
from .process_identity_win import (
    ProcessIdentity, ReconnectedProcess, capture_process_identity,
    current_process_identity, identity_is_running, protect_checkpoint,
)

log = logging.getLogger(__name__)
_MAX_BYTES = 2 * 1024 * 1024
_TTL = 3600
_CONTEXT_KEYS = ("runtime_backend", "active_installation_profile", "game_port", "proxy_url",
                 "docker_project_name", "docker_control_policy", "client_settings_mode")
_CONTEXT_PATHS = ("evejs_root", "client_path", "docker_compose_file", "shared_settings_folder")


@dataclass(frozen=True)
class HandoffTicket:
    path: Path
    digest: str


@dataclass(frozen=True)
class ResumeResult:
    game: bool = False
    market: bool = False
    clients: int = 0
    skipped: int = 0


def _canonical(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve())) if path else ""


def _context(cfg: dict) -> dict:
    result = {key: cfg.get(key, config.DEFAULT_CONFIG.get(key, "default")) for key in _CONTEXT_KEYS}
    result.update({key: _canonical(cfg.get(key, "")) for key in _CONTEXT_PATHS})
    return result


def checkpoint_path(executable: str | Path) -> Path:
    identity = hashlib.sha256(_canonical(executable).encode("utf-8")).hexdigest()
    return config.CONFIG_DIR / "update-runtime" / (identity + ".bin")


def _native_receipt(process: object) -> dict | None:
    from .server_launcher import NativeModRuntimeLaunchReceipt
    receipt = getattr(process, "mod_runtime_receipt", None)
    if not isinstance(receipt, NativeModRuntimeLaunchReceipt):
        return None
    return {"plan": receipt.plan_sha256, "identity": receipt.runtime_identity,
            "status": str(receipt.status_log_path), "offset": receipt.console_start_offset}


def prepare_runtime_handoff(owner: object, executable: str | Path) -> HandoffTicket | None:
    """Freeze only live handles already registered to the outgoing window."""
    state = vars(owner)
    records = []
    cfg = state.get("_cfg", {})
    for kind, field in (("game", "_server_proc"), ("market", "_market_proc")):
        process = state.get(field)
        if process is None or process.poll() is not None:
            continue
        if cfg.get("runtime_backend", "native") != "native" or not cfg.get("evejs_root"):
            raise ValueError("Native ownership does not match the selected installation")
        records.append({"kind": kind, "process": capture_process_identity(process).payload(),
                        "receipt": _native_receipt(process) if kind == "game" else None})
    tracker = state.get("_tracker")
    for client in (tracker.running if tracker is not None else ()):
        records.append({"kind": "client", "process": capture_process_identity(client.process).payload(),
                        "username": client.username, "character": client.character_name,
                        "started": client.started_at.isoformat(), "seen_window": client.has_seen_window})
    if not records:
        return None
    if len(records) > 258:
        raise ValueError("Too many running processes for an update checkpoint")
    parent = current_process_identity()
    if any(record["process"]["pid"] == parent.pid for record in records):
        raise ValueError("A launcher cannot transfer ownership of itself")
    internal = Path(executable).resolve().parent / "_internal"
    if any(Path(record["process"]["image"]).is_relative_to(internal) for record in records):
        raise ValueError("A running process depends on replaceable launcher files")
    payload = {"schema": 1, "time": time.time(), "executable": _canonical(executable),
               "context": _context(cfg), "parent": parent.payload(), "records": records}
    encrypted = protect_checkpoint(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if len(encrypted) > _MAX_BYTES:
        raise ValueError("Update checkpoint is too large")
    path = checkpoint_path(executable)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Rename publishes complete bytes atomically; on Windows it refuses to
    # overwrite another in-flight launcher's checkpoint.
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return HandoffTicket(path, hashlib.sha256(encrypted).hexdigest())


def cancel_runtime_handoff(ticket: HandoffTicket | None) -> None:
    """Preparation failed: revoke only the exact checkpoint this window wrote."""
    if ticket is None:
        return
    try:
        with ticket.path.open("rb") as stream:
            content = stream.read(_MAX_BYTES + 1)
        if hashlib.sha256(content).hexdigest() == ticket.digest:
            ticket.path.unlink()
    except OSError:
        log.warning("Could not revoke the update runtime checkpoint")


def _validate_records(records: object) -> list[dict]:
    if not isinstance(records, list) or not 1 <= len(records) <= 258:
        raise ValueError("Invalid process records")
    seen = set()
    services = set()
    for record in records:
        identity = ProcessIdentity.parse(record["process"])
        kind = record["kind"]
        if identity.pid in seen or kind not in ("game", "market", "client"):
            raise ValueError("Duplicate or invalid process record")
        seen.add(identity.pid)
        if kind != "client":
            if kind in services:
                raise ValueError("Duplicate service ownership")
            services.add(kind)
        else:
            for key in ("username", "character", "started"):
                if not isinstance(record[key], str) or len(record[key]) > 1024:
                    raise ValueError("Invalid client metadata")
            datetime.fromisoformat(record["started"])
            if type(record["seen_window"]) is not bool:
                raise ValueError("Invalid client window metadata")
    return records


def _restore_native_evidence(owner: object, process: ReconnectedProcess, receipt: dict | None) -> None:
    if not isinstance(receipt, dict):
        return
    from .mod_runtime_state import NATIVE_BACKEND, read_mod_runtime_snapshot
    from .server_launcher import NativeModRuntimeLaunchReceipt, resume_native_console_evidence, get_native_mod_status_log
    root = vars(owner)["_cfg"]["evejs_root"]
    if _canonical(receipt["status"]) != _canonical(get_native_mod_status_log(root)):
        raise ValueError("Native evidence belongs to another installation")
    process.mod_runtime_receipt = NativeModRuntimeLaunchReceipt(
        receipt["plan"], receipt["identity"], Path(receipt["status"]), receipt["offset"],
    )
    resume_native_console_evidence(process)
    snapshot = read_mod_runtime_snapshot(root, backend=NATIVE_BACKEND,
                                         runtime_identity=receipt["identity"], pid=process.pid)
    if snapshot is not None and snapshot.plan_sha256 == receipt["plan"]:
        owner._publish_mod_runtime_snapshot(snapshot)


def resume_runtime_handoff(owner: object, executable: str | Path) -> ResumeResult:
    """Consume once after the old GUI exits; never infer Online from a receipt."""
    path = checkpoint_path(executable)
    claimed = None
    content = None
    stage = "read"
    try:
        if not path.exists():
            return ResumeResult()
        if path.is_symlink() or path.is_junction():
            raise ValueError("Redirected update checkpoint")
        with path.open("rb") as stream:
            content = stream.read(_MAX_BYTES + 1)
        if len(content) > _MAX_BYTES:
            raise ValueError("Oversized update checkpoint")
        stage = "decrypt"
        data = json.loads(protect_checkpoint(content, decrypt=True))
        stage = "schema"
        if data["schema"] != 1 or data["executable"] != _canonical(executable):
            raise ValueError("Update checkpoint names another launcher")
        parent = ProcessIdentity.parse(data["parent"])
        stage = "parent"
        if identity_is_running(parent):
            # Another normal launch must not steal control while the old GUI
            # still owns the runtime, including while it downloads the update.
            return ResumeResult()
        claimed = path.with_suffix(".claim-" + uuid.uuid4().hex)
        stage = "claim"
        os.rename(path, claimed)
        # Verify the atomic claim contains the bytes inspected above.
        if claimed.read_bytes() != content:
            raise ValueError("Update checkpoint changed during claim")
        stage = "context"
        age = time.time() - data["time"]
        if not 0 <= age <= _TTL or data["context"] != _context(vars(owner).get("_cfg", {})):
            raise ValueError("Expired update checkpoint or changed installation")
        stage = "records"
        records = _validate_records(data["records"])
        stage = "adopt"
        result = {"game": False, "market": False, "clients": 0, "skipped": 0}
        for record in records:
            kind = record["kind"]
            # A concurrent ordinary launch must not overwrite an owned handle.
            field = "_server_proc" if kind == "game" else "_market_proc"
            if kind != "client" and vars(owner).get(field) is not None:
                result["skipped"] += 1
                continue
            try:
                process = ReconnectedProcess(ProcessIdentity.parse(record["process"]))
            except (OSError, ValueError):
                result["skipped"] += 1
                continue
            if kind == "client":
                if owner._tracker.is_account_running(record["username"]):
                    result["skipped"] += 1
                    continue
                client = owner._tracker.add(record["username"], record["character"], process)
                client.started_at = datetime.fromisoformat(record["started"])
                client.has_seen_window = record["seen_window"]
                result["clients"] += 1
            else:
                if vars(owner)["_cfg"].get("runtime_backend", "native") != "native":
                    raise ValueError("Cannot transfer Native ownership into Docker")
                setattr(owner, field, process)
                result[kind] = True
                if kind == "game":
                    try:
                        _restore_native_evidence(owner, process, record.get("receipt"))
                    except (OSError, ValueError, KeyError, TypeError):
                        log.warning("Native process recovered without optional mod evidence")
        log.info("Update runtime recovered game=%s market=%s clients=%d skipped=%d", *result.values())
        return ResumeResult(**result)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # Leave services alive. Existing external-service UI remains read-only
        # when a process cannot be proved to be ours.
        log.warning("Update runtime checkpoint was not accepted stage=%s", stage)
        # Corrupt ciphertext cannot be an ownership grant. Retire only the
        # unchanged file we inspected so it cannot permanently block updates.
        if claimed is None and content is not None and not (stage == "parent" and isinstance(exc, OSError)):
            try:
                with path.open("rb") as stream:
                    unchanged = stream.read(_MAX_BYTES + 1) == content
                if unchanged:
                    claimed = path.with_suffix(".claim-" + uuid.uuid4().hex)
                    os.rename(path, claimed)
            except OSError:
                claimed = None
        return ResumeResult()
    finally:
        if claimed is not None:
            try:
                claimed.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not remove the consumed update runtime checkpoint")
