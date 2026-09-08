"""Best-effort lifecycle observations for one captured client process."""
from __future__ import annotations

import logging
import threading
import uuid

from .mod_api_runtime import run_mod_helper

log = logging.getLogger("src.app")


def start_client_notifications(prepared, *, backend, process=None, error_type=""):
    """Deliver in order on a daemon thread; never delay or invalidate a spawn.

    An exit belongs to the original Popen handle, never a later process with a
    reused PID. Delivery ends when the launcher exits; there is no replay queue.
    """
    targets = tuple(getattr(prepared, "notifications", ()))
    if not targets:
        return None
    launch_id = str(uuid.uuid4())

    def deliver(action, event):
        for descriptor, context in targets:
            if action not in descriptor.launcher_api.capabilities:
                continue
            try:
                run_mod_helper(descriptor, action, context, backend=backend,
                               timeout=30, event=event).require_ready()
            except Exception as exc:
                # Never log helper output or the original spawn exception:
                # those can contain user data or rendered command arguments.
                log.warning("Mod notification failed mod=%s action=%s launch=%s error_type=%s",
                            descriptor.id, action, launch_id, type(exc).__name__)

    def observe():
        event = {"launchId": launch_id, "status": "started" if process is not None else "failed",
                 "pid": process.pid if process is not None else None,
                 "exitCode": None, "errorType": error_type}
        deliver("launch_result", event)
        if process is None or not any("client_exit" in item[0].launcher_api.capabilities for item in targets):
            return
        try:
            code = process.wait()
        except Exception as exc:
            log.warning("Mod exit observation failed launch=%s error_type=%s", launch_id, type(exc).__name__)
            return
        deliver("client_exit", {**event, "status": "exited", "exitCode": code, "errorType": ""})

    thread = threading.Thread(target=observe, name="mod-client-events", daemon=True)
    try:
        thread.start()
    except Exception as exc:
        log.warning("Mod notification dispatch failed error_type=%s", type(exc).__name__)
        return None
    return thread
