from types import SimpleNamespace

from src.core import mod_client_notifications as notifications


def prepared(profile):
    descriptor = SimpleNamespace(id="example", launcher_api=SimpleNamespace(
        capabilities=("launch_result", "client_exit")))
    return SimpleNamespace(notifications=((descriptor, profile),))


def test_two_processes_keep_event_order_profile_identity_and_exit_codes(monkeypatch):
    received = []
    def helper(descriptor, action, context, **kwargs):
        received.append((context, action, kwargs["event"]))
        return SimpleNamespace(require_ready=lambda: None)
    monkeypatch.setattr(notifications, "run_mod_helper", helper)
    threads = [notifications.start_client_notifications(prepared(profile), backend="native",
        process=SimpleNamespace(pid=pid, wait=lambda code=code: code))
        for profile, pid, code in [("alpha", 101, 0), ("beta", 202, 17)]]
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    ids = []
    for profile, pid, code in [("alpha", 101, 0), ("beta", 202, 17)]:
        events = [(action, event) for owner, action, event in received if owner == profile]
        assert [action for action, _ in events] == ["launch_result", "client_exit"]
        assert [event["pid"] for _, event in events] == [pid, pid]
        assert events[1][1]["exitCode"] == code
        assert events[0][1]["launchId"] == events[1][1]["launchId"]
        ids.append(events[0][1]["launchId"])
    assert len(set(ids)) == 2


def test_failed_spawn_has_no_exit_and_callback_failure_is_contained(monkeypatch):
    received = []
    def helper(descriptor, action, context, **kwargs):
        received.append((action, kwargs["event"]))
        raise RuntimeError("Callback failed")
    monkeypatch.setattr(notifications, "run_mod_helper", helper)
    thread = notifications.start_client_notifications(prepared("alpha"), backend="native", error_type="OSError")
    thread.join(2)
    assert not thread.is_alive()
    assert len(received) == 1
    assert received[0][0] == "launch_result"
    assert received[0][1]["status"] == "failed"
    assert received[0][1]["pid"] is None
    assert received[0][1]["errorType"] == "OSError"
