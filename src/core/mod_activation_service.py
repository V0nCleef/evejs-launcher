"""Atomic launcher transaction for one requested mod activation state.

The lifecycle lock deliberately spans all three durable boundaries:

``prepared intent -> mod filesystem/config mutation -> pending restart``.

Keeping that ownership in a small core service prevents the Qt page from
accidentally releasing the installer-compatible lock between those writes.
"""
from __future__ import annotations

from .mod_activation_state import (
    fail_mod_activation,
    mark_mod_activation_pending,
    prepare_mod_activation,
)
from .mod_lifecycle_lock import ModLifecycleBusyError, acquire_mod_lifecycle_lock
from .mod_manifest import Mod, ModActivationError, set_mod_active_locked


def request_mod_activation(mod: Mod, desired: bool) -> bool:
    """Durably request ``desired`` and return the verified configured state.

    This confirms only the on-disk configuration transaction.  The journal is
    intentionally left at ``pending_restart`` until a later Game lifecycle
    publishes matching runtime evidence.
    """

    if not isinstance(mod, Mod):
        raise TypeError("mod must be a Mod instance.")
    if type(desired) is not bool:
        raise TypeError("The requested mod state must be a boolean.")
    if mod.evejs_root is None:
        raise ModActivationError(
            f"Cannot change '{mod.name}': the mod is not bound to an EveJS root."
        )

    try:
        with acquire_mod_lifecycle_lock(mod.evejs_root):
            prepare_mod_activation(mod, desired)
            try:
                configured = set_mod_active_locked(mod, desired)
            except Exception as exc:
                try:
                    fail_mod_activation(
                        mod,
                        desired,
                        "activation-mutation-failed",
                    )
                except Exception as journal_exc:
                    exc.add_note(
                        "The activation failure could not be recorded in the "
                        f"durable journal: {journal_exc}"
                    )
                raise

            if configured is not desired:
                failure = ModActivationError(
                    f"Changing '{mod.name}' returned an unexpected configured state."
                )
                try:
                    fail_mod_activation(
                        mod,
                        desired,
                        "activation-state-mismatch",
                    )
                except Exception as journal_exc:
                    failure.add_note(
                        "The activation mismatch could not be recorded in the "
                        f"durable journal: {journal_exc}"
                    )
                raise failure

            # If this final journal write fails, the prepared record remains.
            # Recovery projects prepared + matching config as restart-required.
            mark_mod_activation_pending(mod, desired)
            return configured
    except ModLifecycleBusyError as exc:
        raise ModActivationError(f"Cannot change '{mod.name}': {exc}") from exc


__all__ = ["request_mod_activation"]
