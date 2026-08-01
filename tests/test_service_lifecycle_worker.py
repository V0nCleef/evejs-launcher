"""Unit tests for background service lifecycle workers."""
from __future__ import annotations

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


def test_start_worker_does_not_start_game_after_market_exits_before_readiness() -> None:
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
        graceful_timeout_sec=1,
        poll_interval_sec=0,
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
        graceful_timeout_sec=0.01,
        poll_interval_sec=0,
        force_kill_fn=force_kill,
        clock_fn=iter((0.0, 1.0)).__next__,
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert forced == [1001]
    assert len(observed) == 1
    assert observed[0].game_stopped is True
    assert observed[0].game_error is None


def test_stop_worker_reports_one_result_for_injected_force_kill_failure() -> None:
    game = FakeProcess(1001)
    observed = []

    def terminate_without_exit() -> None:
        game.terminate_calls += 1

    game.terminate = terminate_without_exit
    worker = ServiceStopWorker(
        game,
        None,
        graceful_timeout_sec=0.01,
        poll_interval_sec=0,
        force_kill_fn=lambda _process: False,
        clock_fn=iter((0.0, 1.0)).__next__,
    )
    worker.completed.connect(observed.append)

    worker.run()

    assert len(observed) == 1
    assert observed[0].game_stopped is False
    assert observed[0].game_error == "Forced service shutdown failed."
