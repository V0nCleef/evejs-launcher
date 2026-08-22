"""Unit tests for background service lifecycle workers."""
from __future__ import annotations

import subprocess

from src.constants import Ports
from src.workers.server_worker import ServiceStartWorker, ServiceStopWorker


class FakeProcess:
    def __init__(self, pid: int, return_code: int | None = None) -> None:
        self.pid = pid
        self.return_code = return_code
        self.terminate_calls = 0

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.return_code = 0


def request_terminate(process: FakeProcess) -> bool:
    process.terminate()
    return True


def test_start_worker_waits_for_market_before_starting_game() -> None:
    events: list[str] = []
    probe_counts = {
        int(Ports.MARKET_HTTP): 0,
        int(Ports.MARKET_RPC): 0,
        int(Ports.GAME_TCP): 0,
    }

    def probe(port: int) -> bool:
        probe_counts[port] += 1
        events.append(f"probe:{port}")
        return probe_counts[port] > 1

    def start_market(_root: str) -> FakeProcess:
        events.append("start:market")
        return FakeProcess(1001)

    def start_game(_root: str, *, mode: str) -> FakeProcess:
        assert mode == "modded"
        events.append("start:game")
        return FakeProcess(1002)

    observed = []
    worker = ServiceStartWorker(
        "C:/Games/EveJS",
        mode="modded",
        start_market=True,
        start_game=True,
        readiness_timeout_sec=1,
        poll_interval_sec=0,
        probe=probe,
        start_market_fn=start_market,
        start_game_fn=start_game,
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert events.index("start:market") < events.index("start:game")
    assert events.count(f"probe:{int(Ports.MARKET_HTTP)}") >= 2
    assert events.count(f"probe:{int(Ports.MARKET_RPC)}") >= 2
    assert events.count(f"probe:{int(Ports.GAME_TCP)}") >= 2
    assert len(observed) == 1
    assert observed[0].succeeded is True
    assert observed[0].market_process.pid == 1001
    assert observed[0].game_process.pid == 1002


def test_start_worker_allows_slow_first_market_build_without_extending_game_timeout() -> None:
    now = [0.0]
    game_started_at: list[float] = []
    observed = []

    def probe(port: int) -> bool:
        if port in (int(Ports.MARKET_HTTP), int(Ports.MARKET_RPC)):
            return now[0] >= 70.0
        return False

    def sleep(seconds: float) -> None:
        now[0] += seconds

    def start_game(_root: str, *, mode: str) -> FakeProcess:
        assert mode == "vanilla"
        game_started_at.append(now[0])
        return FakeProcess(1002)

    worker = ServiceStartWorker(
        "C:/Games/EveJS",
        mode="vanilla",
        start_market=True,
        start_game=True,
        readiness_timeout_sec=60,
        market_readiness_timeout_sec=300,
        poll_interval_sec=10,
        probe=probe,
        start_market_fn=lambda _root: FakeProcess(1001),
        start_game_fn=start_game,
        sleep_fn=sleep,
        clock_fn=lambda: now[0],
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert game_started_at == [70.0]
    assert now[0] == 130.0
    assert len(observed) == 1
    assert observed[0].market_ready is True
    assert "Game service did not become ready" in str(observed[0].game_error)


def test_start_worker_does_not_start_game_after_market_exits_by_default() -> None:
    game_start_calls: list[str] = []
    observed = []
    worker = ServiceStartWorker(
        "C:/Games/EveJS",
        mode="vanilla",
        start_market=True,
        start_game=True,
        probe=lambda _port: False,
        start_market_fn=lambda _root: FakeProcess(1001, return_code=17),
        start_game_fn=lambda _root, *, mode: game_start_calls.append(mode),
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert game_start_calls == []
    assert len(observed) == 1
    assert observed[0].succeeded is False
    assert "17" in str(observed[0].market_error)
    assert "skipped" in str(observed[0].game_error).lower()


def test_start_worker_does_not_start_game_after_market_spawn_failure_by_default() -> None:
    game_start_calls: list[str] = []
    observed = []

    def fail_market_start(_root: str) -> FakeProcess:
        raise RuntimeError("market executable is unavailable")

    worker = ServiceStartWorker(
        "C:/Games/EveJS",
        mode="vanilla",
        start_market=True,
        start_game=True,
        start_market_fn=fail_market_start,
        start_game_fn=lambda _root, *, mode: game_start_calls.append(mode),
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert game_start_calls == []
    assert len(observed) == 1
    assert observed[0].market_process is None
    assert observed[0].market_ready is False
    assert "market executable is unavailable" in str(observed[0].market_error)
    assert "skipped" in str(observed[0].game_error).lower()


def test_start_worker_can_continue_game_after_market_spawn_failure() -> None:
    events: list[str] = []
    observed = []

    def fail_market_start(_root: str) -> FakeProcess:
        events.append("start:market")
        raise RuntimeError("market executable is unavailable")

    def start_game(_root: str, *, mode: str) -> FakeProcess:
        assert mode == "vanilla"
        events.append("start:game")
        return FakeProcess(1002)

    worker = ServiceStartWorker(
        "C:/Games/EveJS",
        mode="vanilla",
        start_market=True,
        start_game=True,
        continue_game_after_market_failure=True,
        probe=lambda port: port == int(Ports.GAME_TCP),
        start_market_fn=fail_market_start,
        start_game_fn=start_game,
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert events == ["start:market", "start:game"]
    assert len(observed) == 1
    assert observed[0].market_process is None
    assert observed[0].market_ready is False
    assert "market executable is unavailable" in str(observed[0].market_error)
    assert observed[0].game_process.pid == 1002
    assert observed[0].game_ready is True
    assert observed[0].game_error is None
    assert observed[0].succeeded is False


def test_start_worker_can_continue_game_after_market_exits_before_readiness() -> None:
    game_start_calls: list[str] = []
    observed = []
    market = FakeProcess(1001, return_code=17)

    def start_game(_root: str, *, mode: str) -> FakeProcess:
        game_start_calls.append(mode)
        return FakeProcess(1002)

    worker = ServiceStartWorker(
        "C:/Games/EveJS",
        mode="vanilla",
        start_market=True,
        start_game=True,
        continue_game_after_market_failure=True,
        probe=lambda port: port == int(Ports.GAME_TCP),
        start_market_fn=lambda _root: market,
        start_game_fn=start_game,
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert game_start_calls == ["vanilla"]
    assert len(observed) == 1
    assert observed[0].market_process is market
    assert observed[0].market_ready is False
    assert "17" in str(observed[0].market_error)
    assert observed[0].game_process.pid == 1002
    assert observed[0].game_ready is True
    assert observed[0].game_error is None
    assert observed[0].succeeded is False


def test_start_worker_reports_timeout_even_if_no_probe_iteration_runs() -> None:
    clock_values = iter((0.0, 1.0))
    observed = []
    worker = ServiceStartWorker(
        "C:/Games/EveJS",
        mode=None,
        start_market=True,
        start_game=False,
        readiness_timeout_sec=0.01,
        probe=lambda _port: False,
        start_market_fn=lambda _root: FakeProcess(1001),
        clock_fn=lambda: next(clock_values),
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert len(observed) == 1
    assert observed[0].succeeded is False
    assert "timeout" in str(observed[0].market_error).lower()


def test_start_worker_game_only_never_launches_or_probes_market() -> None:
    launched: list[tuple[str, str | None]] = []
    probed_ports: list[int] = []
    observed = []

    def start_game(_root: str, *, mode: str) -> FakeProcess:
        launched.append(("game", mode))
        return FakeProcess(1002)

    worker = ServiceStartWorker(
        "C:/Games/EveJS",
        mode="vanilla",
        start_market=False,
        start_game=True,
        probe=lambda port: probed_ports.append(port) is None,
        start_market_fn=lambda _root: (_ for _ in ()).throw(AssertionError("market launch")),
        start_game_fn=start_game,
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert launched == [("game", "vanilla")]
    assert probed_ports == [int(Ports.GAME_TCP)]
    assert len(observed) == 1
    assert observed[0].game_ready is True
    assert observed[0].market_process is None


def test_start_worker_market_only_uses_both_native_market_readiness_ports() -> None:
    launched: list[str] = []
    probed_ports: list[int] = []
    observed = []

    def start_market(_root: str) -> FakeProcess:
        launched.append("market")
        return FakeProcess(1001)

    worker = ServiceStartWorker(
        "C:/Games/EveJS",
        mode=None,
        start_market=True,
        start_game=False,
        probe=lambda port: probed_ports.append(port) is None,
        start_market_fn=start_market,
        start_game_fn=lambda _root, *, mode: (_ for _ in ()).throw(AssertionError("game launch")),
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert launched == ["market"]
    assert probed_ports == [int(Ports.MARKET_HTTP), int(Ports.MARKET_RPC)]
    assert len(observed) == 1
    assert observed[0].market_ready is True
    assert observed[0].game_process is None


def test_stop_worker_stops_game_before_market_without_blocking_callers() -> None:
    events: list[str] = []

    class OrderedProcess(FakeProcess):
        def __init__(self, pid: int, name: str) -> None:
            super().__init__(pid)
            self.name = name

        def terminate(self) -> None:
            events.append(self.name)
            super().terminate()

    game = OrderedProcess(1001, "game")
    market = OrderedProcess(1002, "market")
    observed = []
    worker = ServiceStopWorker(
        game,
        market,
        game_graceful_timeout_sec=1,
        market_graceful_timeout_sec=1,
        poll_interval_sec=0,
        graceful_game_stop_fn=request_terminate,
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert events == ["game", "market"]
    assert len(observed) == 1
    assert observed[0].game_stopped is True
    assert observed[0].market_stopped is True
    assert observed[0].succeeded is True


def test_stop_worker_skips_absent_and_dead_processes() -> None:
    dead_market = FakeProcess(1002, return_code=0)
    observed = []
    worker = ServiceStopWorker(None, dead_market)
    worker.completed.connect(observed.append)

    worker.run()

    assert dead_market.terminate_calls == 0
    assert len(observed) == 1
    assert observed[0].game_stopped is True
    assert observed[0].market_stopped is True


def test_stop_worker_force_kill_does_not_inherit_launcher_stdin(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess:
        observed.update(kwargs)
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert ServiceStopWorker._default_force_kill(FakeProcess(4242)) is True
    assert observed["stdin"] is subprocess.DEVNULL


def test_stop_worker_reports_one_result_for_injected_force_kill_success() -> None:
    game = FakeProcess(1001)
    forced: list[int] = []
    observed = []

    def terminate_without_exit() -> None:
        game.terminate_calls += 1

    game.terminate = terminate_without_exit

    def force_kill(process: FakeProcess) -> bool:
        forced.append(process.pid)
        process.return_code = 9
        return True

    worker = ServiceStopWorker(
        game,
        None,
        game_graceful_timeout_sec=0.01,
        poll_interval_sec=0,
        force_kill_fn=force_kill,
        graceful_game_stop_fn=request_terminate,
        clock_fn=iter((0.0, 1.0)).__next__,
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert forced == [1001]
    assert len(observed) == 1
    assert observed[0].game_stopped is True
    assert observed[0].game_error == (
        "Game service stopped without verified graceful cleanup; persisted state "
        "may be incomplete."
    )
    assert observed[0].succeeded is False


def test_stop_worker_reports_one_result_for_injected_force_kill_failure() -> None:
    game = FakeProcess(1001)
    observed = []

    def terminate_without_exit() -> None:
        game.terminate_calls += 1

    game.terminate = terminate_without_exit
    worker = ServiceStopWorker(
        game,
        None,
        game_graceful_timeout_sec=0.01,
        poll_interval_sec=0,
        force_kill_fn=lambda _process: False,
        graceful_game_stop_fn=request_terminate,
        clock_fn=iter((0.0, 1.0)).__next__,
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert len(observed) == 1
    assert observed[0].game_stopped is False
    assert observed[0].game_error == "Forced service shutdown failed."


def test_stop_worker_uses_graceful_request_only_for_game_process() -> None:
    events: list[str] = []

    class OrderedProcess(FakeProcess):
        def __init__(self, pid: int, name: str) -> None:
            super().__init__(pid)
            self.name = name

        def terminate(self) -> None:
            events.append(f"terminate:{self.name}")
            super().terminate()

    game = OrderedProcess(1001, "game")
    market = OrderedProcess(1002, "market")

    def request_game(process: FakeProcess) -> bool:
        events.append(f"ctrl-break:{process.pid}")
        process.return_code = 0
        return True

    worker = ServiceStopWorker(
        game,
        market,
        graceful_game_stop_fn=request_game,
    )
    worker.run()

    assert events == ["ctrl-break:1001", "terminate:market"]
    assert game.terminate_calls == 0
    assert market.terminate_calls == 1


def test_stop_worker_force_kills_exact_process_when_graceful_request_fails() -> None:
    game = FakeProcess(4242)
    forced: list[int] = []

    def force_kill(process: FakeProcess) -> bool:
        forced.append(process.pid)
        process.return_code = 9
        return True

    observed = []
    worker = ServiceStopWorker(
        game,
        None,
        graceful_game_stop_fn=lambda _process: False,
        force_kill_fn=force_kill,
    )
    worker.completed.connect(observed.append)
    worker.run()

    assert forced == [4242]
    assert game.terminate_calls == 0
    assert observed[0].game_stopped is True
    assert observed[0].game_error == (
        "Game service stopped without verified graceful cleanup; persisted state "
        "may be incomplete."
    )
    assert observed[0].succeeded is False


def test_stop_worker_allows_game_cleanup_to_finish_after_full_hook_budget() -> None:
    game = FakeProcess(4242)
    now = [0.0]
    forced: list[int] = []
    observed = []

    def poll() -> int | None:
        return 0 if now[0] >= 90 else None

    def sleep(seconds: float) -> None:
        now[0] += seconds

    game.poll = poll
    worker = ServiceStopWorker(
        game,
        None,
        poll_interval_sec=10,
        graceful_game_stop_fn=lambda _process: True,
        force_kill_fn=lambda process: forced.append(process.pid) is None,
        sleep_fn=sleep,
        clock_fn=lambda: now[0],
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert now[0] == 90
    assert forced == []
    assert observed[0].game_stopped is True
    assert observed[0].game_error is None
    assert observed[0].succeeded is True


def test_stop_worker_reports_nonzero_graceful_game_exit_but_clears_ownership() -> None:
    game = FakeProcess(4242)
    forced: list[int] = []
    observed = []

    def request_game(process: FakeProcess) -> bool:
        process.return_code = 17
        return True

    worker = ServiceStopWorker(
        game,
        None,
        graceful_game_stop_fn=request_game,
        force_kill_fn=lambda process: forced.append(process.pid) is None,
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert forced == []
    assert len(observed) == 1
    assert observed[0].game_stopped is True
    assert observed[0].game_error == (
        "Game service exited with code 17 during graceful shutdown."
    )
    assert observed[0].succeeded is False
