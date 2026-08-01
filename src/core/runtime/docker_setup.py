"""Immutable Docker setup drafts and shared Compose target construction."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from src.core.discovery import validate_docker_evejs_root
from src.core.runtime.docker_compose import ComposeTarget, PreflightReport


_CONTROL_POLICIES = frozenset({"connect_only", "managed"})


@dataclass(frozen=True)
class DockerSetupDraft:
    """Exact normalized values shared by Settings, wizard, and preflight."""

    evejs_root: str
    compose_file: str
    project_name: str
    control_policy: str
    keep_running_on_exit: bool
    client_path: str

    def __post_init__(self) -> None:
        for field_name in (
            "evejs_root",
            "compose_file",
            "project_name",
            "control_policy",
            "client_path",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise TypeError(f"{field_name} must be text.")
            object.__setattr__(self, field_name, value.strip())
        if self.control_policy not in _CONTROL_POLICIES:
            raise ValueError("Docker control policy is invalid.")
        if not isinstance(self.keep_running_on_exit, bool):
            raise TypeError("keep_running_on_exit must be a boolean.")


@dataclass(frozen=True)
class DockerPreflightRequest:
    """One exact draft and opaque request token sent to a worker."""

    token: int
    draft: DockerSetupDraft
    draft_fingerprint: str


@dataclass(frozen=True)
class DockerPreflightResult:
    """Private-safe preflight report attributed to one exact draft."""

    request_token: int
    draft_fingerprint: str
    report: PreflightReport


def docker_draft_fingerprint(draft: DockerSetupDraft) -> str:
    """Hash normalized persisted fields without logging private values."""
    payload = json.dumps(
        {
            "evejs_root": draft.evejs_root,
            "compose_file": draft.compose_file,
            "project_name": draft.project_name,
            "control_policy": draft.control_policy,
            "keep_running_on_exit": draft.keep_running_on_exit,
            "client_path": draft.client_path,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def create_preflight_request(
    draft: DockerSetupDraft,
    *,
    token: int,
) -> DockerPreflightRequest:
    """Create one immutable, fingerprinted worker request."""
    if not isinstance(token, int) or token < 1:
        raise ValueError("Docker preflight token must be a positive integer.")
    return DockerPreflightRequest(token, draft, docker_draft_fingerprint(draft))


def build_compose_target(draft: DockerSetupDraft) -> ComposeTarget:
    """Build the one authoritative explicit target for a validated draft."""
    valid, diagnostic = validate_docker_evejs_root(
        draft.evejs_root,
        draft.compose_file,
    )
    if not valid:
        raise ValueError(diagnostic)

    root = Path(draft.evejs_root)
    compose = Path(draft.compose_file) if draft.compose_file else root / "compose.yaml"
    project_name = draft.project_name or None
    return ComposeTarget(compose, root, project_name)
