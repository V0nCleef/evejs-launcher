"""Focused diagnostics tests for tracked EVE client retirement."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from src.core import process_tracker as process_tracker_module
from src.core.process_tracker import ProcessTracker


class _MutableProcess:
    def __init__(self, pid: int, *, return_code: int = 0) -> None:
        self.pid = pid
        self.alive = True
        self.return_code = return_code

    def poll(self) -> int | None:
        return None if self.alive else self.return_code


def test_prune_logs_account_neutral_exit_diagnostics(caplog, monkeypatch) -> None:
    test_logger = logging.getLogger("tests.process_tracker_observability")
    monkeypatch.setattr(process_tracker_module, "log", test_logger)
    process = _MutableProcess(4242, return_code=17)
    tracker = ProcessTracker(window_probe=lambda _pid: False)
    client = tracker.add("private-account", "Private Character", process)
    client.started_at = datetime.now() - timedelta(seconds=12.3)
    client.has_seen_window = True
    process.alive = False

    with caplog.at_level(logging.INFO, logger=test_logger.name):
        assert tracker.prune_dead() == 1

    message = caplog.records[-1].getMessage()
    assert "pid=4242" in message
    assert "reason=process-exited" in message
    assert "uptime_seconds=12." in message
    assert "return_code=17" in message
    assert "window_seen=True" in message
    assert "private-account" not in message
    assert "Private Character" not in message


def test_prune_does_not_probe_or_retire_a_live_process_for_a_missing_window() -> None:
    probe_calls: list[int] = []
    process = _MutableProcess(4242)
    tracker = ProcessTracker(
        window_probe=lambda pid: probe_calls.append(pid) or False,
        window_close_grace_seconds=0,
    )
    client = tracker.add("private-account", "Private Character", process)
    client.started_at = datetime.now() - timedelta(seconds=23.4)
    client.has_seen_window = True

    assert tracker.prune_dead() == 0
    assert probe_calls == []
    assert tracker.get_running_character("private-account") == "Private Character"
