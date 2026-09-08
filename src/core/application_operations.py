"""One view of retained mutation ownership, including queued continuations.

An OS thread being finished is insufficient: its terminal signal, dialog or
follow-up may still own the operation. Keep references until teardown finishes.
"""
from __future__ import annotations


MUTATION_FIELDS = {
    "services": ("_lifecycle_thread", "_mod_lifecycle_lease"),
    "character_create": ("_character_creation_thread", "_character_creation_request", "_docker_character_request"),
    "character_delete": ("_character_deletion_thread", "_character_deletion_request"),
    "overview": ("_overview_patch_thread",),
    "client_launch": ("_client_launch_thread", "_client_launch_request", "_launch_queue"),
    "docker": ("_docker_preflight_thread", "_docker_tool_request"),
    "market_preflight": ("_market_preflight_thread", "_market_preflight_request"),
    "mod_operation": ("_mod_operation_thread", "_mod_operation_request"),
    "mod_settings": ("_mod_settings_thread", "_mod_settings_request"),
}


def update_active(owner: object) -> bool:
    state = vars(owner)
    return state.get("_update_install_worker") is not None or bool(state.get("_update_handoff_pending"))


def active_mutations(owner: object, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    state = vars(owner)
    active = tuple(
        name for name, fields in MUTATION_FIELDS.items()
        if name not in exclude and any(state.get(field) is not None for field in fields)
    )
    return active + (("update",) if "update" not in exclude and update_active(owner) else ())


def update_blocked(owner: object) -> bool:
    return bool(vars(owner).get("_close_in_progress") or active_mutations(owner))
