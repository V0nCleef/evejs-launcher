"""Client environment and immutable runtime endpoint tests."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.core import launcher
from src.core.client_autologin import AutoLoginLaunch
from src.core.launcher import ClientLaunchContext
from src.core.runtime.endpoints import Endpoint, RuntimeEndpoints


def _endpoint(name: str, port: int, target: int, protocol: str = "tcp") -> Endpoint:
    return Endpoint(
        service="market" if name == "market" else "server",
        host="127.0.0.1",
        port=port,
        target=target,
        protocol=protocol,
    )


def _docker_endpoints() -> RuntimeEndpoints:
    return RuntimeEndpoints(
        game=_endpoint("game", 32600, 26000),
        image=_endpoint("image", 32601, 26001),
        proxy=_endpoint("proxy", 32602, 26002),
        assets=_endpoint("assets", 34443, 26003),
        xmpp=_endpoint("xmpp", 35222, 5222),
        market=_endpoint("market", 40110, 40110),
    )


def _docker_context(endpoints: RuntimeEndpoints | None) -> ClientLaunchContext:
    return ClientLaunchContext.from_docker(
        endpoints,
        target_identity="docker:fixture-target",
        settings_identity="docker-settings:fixture-settings",
        monitor_generation=7,
    )


def _create_client_cache(client_tq: Path) -> Path:
    client_tq.mkdir(parents=True, exist_ok=True)
    resfiles = client_tq.parent / "ResFiles"
    resfiles.mkdir(parents=True, exist_ok=True)
    (client_tq.parent / "index_tranquility.txt").write_text(
        "fixture",
        encoding="utf-8",
    )
    return resfiles


def _accept_fixture_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        launcher,
        "_validate_client_resource_cache_contents",
        lambda _resfiles: None,
    )


def test_explicit_remapped_proxy_populates_every_proxy_variant() -> None:
    env = launcher.build_env("C:/Fixture/EveJS", "http://127.0.0.1:32602")

    for key in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        assert env[key] == "http://127.0.0.1:32602"

    assert env["EVEJS_PROXY_LOCAL_INTERCEPT"] == "1"
    assert env["EVEJS_PROXY_UNHANDLED_HOST_POLICY"] == "block"
    assert env["EVEJS_NO_PROXY"] == "127.0.0.1,localhost,::1"
    assert env["no_proxy"] == env["EVEJS_NO_PROXY"]
    assert env["NO_PROXY"] == env["EVEJS_NO_PROXY"]
    assert "launchdarkly.com" in env["EVEJS_DARKLY_BLOCK_HOSTS"]


def test_network_policy_preserves_inherited_blocked_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVEJS_PROXY_BLOCKED_HOSTS", "custom.invalid,.custom.invalid")

    env = launcher.build_env("C:/Fixture/EveJS")

    assert env["EVEJS_PROXY_BLOCKED_HOSTS"].startswith(
        "custom.invalid,.custom.invalid,"
    )
    assert env["EVEJS_PROXY_BLOCKED_HOSTS"].endswith(
        env["EVEJS_DARKLY_BLOCK_HOSTS"]
    )


def test_network_policy_discards_inherited_one_shot_launch_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EO_REMOTEFILECACHEFOLDER", "C:/stale-client/ResFiles")
    monkeypatch.setenv("EVEJS_OVERVIEW_BRIDGE", "capture:stale")
    monkeypatch.setenv("EVEJS_OVERVIEW_ACK_PATH", "C:/stale-client/ack.json")

    env = launcher.build_env("C:/Fixture/EveJS")

    assert "EO_REMOTEFILECACHEFOLDER" not in env
    assert "EVEJS_OVERVIEW_BRIDGE" not in env
    assert "EVEJS_OVERVIEW_ACK_PATH" not in env


def test_resource_cache_validation_matches_play_bat_thresholds() -> None:
    assert launcher._MIN_RESOURCE_CACHE_HEX_DIRECTORIES == 240
    assert launcher._MIN_RESOURCE_CACHE_FILES == 50_000


def test_resource_cache_rejects_too_few_hex_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resfiles = tmp_path / "ResFiles"
    (resfiles / "00").mkdir(parents=True)
    monkeypatch.setattr(launcher, "_MIN_RESOURCE_CACHE_HEX_DIRECTORIES", 2)
    monkeypatch.setattr(launcher, "_MIN_RESOURCE_CACHE_FILES", 0)

    with pytest.raises(RuntimeError, match="hexadecimal directories"):
        launcher._validate_client_resource_cache_contents(resfiles)


def test_resource_cache_rejects_too_few_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resfiles = tmp_path / "ResFiles"
    cache_bucket = resfiles / "00"
    cache_bucket.mkdir(parents=True)
    (cache_bucket / "one.cache").write_bytes(b"")
    monkeypatch.setattr(launcher, "_MIN_RESOURCE_CACHE_HEX_DIRECTORIES", 1)
    monkeypatch.setattr(launcher, "_MIN_RESOURCE_CACHE_FILES", 2)

    with pytest.raises(RuntimeError, match="1 files found"):
        launcher._validate_client_resource_cache_contents(resfiles)


@pytest.mark.parametrize(
    "proxy_url",
    [
        "",
        "127.0.0.1:26002",
        "ftp://127.0.0.1:26002",
        "https://127.0.0.1:26002",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
    ],
)
def test_network_policy_rejects_invalid_proxy_origins(proxy_url: str) -> None:
    with pytest.raises(ValueError, match="proxy URL"):
        launcher.build_env("C:/Fixture/EveJS", proxy_url)


def test_native_context_rejects_invalid_proxy_before_launch() -> None:
    with pytest.raises(ValueError, match="proxy URL"):
        ClientLaunchContext.native(proxy_url="not-a-proxy")


@pytest.mark.parametrize("game_port", [True, 0, 65536, 26000.5])
def test_native_context_rejects_invalid_game_ports(game_port: object) -> None:
    with pytest.raises(ValueError, match="game port"):
        ClientLaunchContext.native(game_port=game_port)  # type: ignore[arg-type]


def test_native_context_retains_existing_defaults() -> None:
    context = ClientLaunchContext.native()

    assert context.game_host == "127.0.0.1"
    assert context.game_port == 26000
    assert context.proxy_url == "http://127.0.0.1:26002"
    assert context.image_url is None
    assert context.target_identity is None
    assert context.settings_identity is None
    assert context.monitor_generation is None


def test_client_endpoint_gate_waits_for_exact_game_and_proxy_targets() -> None:
    context = ClientLaunchContext.native(
        game_port=27555,
        proxy_url="http://127.0.0.1:27557",
    )
    now = [0.0]
    game_calls: list[tuple[str, int]] = []
    proxy_calls: list[str] = []
    proxy_results = iter((False, True))

    launcher.wait_for_client_endpoints(
        context,
        timeout_sec=2.0,
        poll_interval_sec=0.25,
        game_probe=lambda host, port: game_calls.append((host, port)) or True,
        proxy_probe=lambda url: proxy_calls.append(url) or next(proxy_results),
        sleep_fn=lambda seconds: now.__setitem__(0, now[0] + seconds),
        clock_fn=lambda: now[0],
    )

    assert game_calls == [("127.0.0.1", 27555), ("127.0.0.1", 27555)]
    assert proxy_calls == [
        "http://127.0.0.1:27557",
        "http://127.0.0.1:27557",
    ]
    assert now[0] == pytest.approx(0.25)


def test_client_endpoint_gate_fails_closed_when_proxy_never_becomes_ready() -> None:
    context = ClientLaunchContext.native(proxy_url="http://127.0.0.1:27557")
    now = [0.0]

    with pytest.raises(RuntimeError, match=r"proxy .*27557/health is not ready"):
        launcher.wait_for_client_endpoints(
            context,
            timeout_sec=1.0,
            poll_interval_sec=0.5,
            game_probe=lambda _host, _port: True,
            proxy_probe=lambda _url: False,
            sleep_fn=lambda seconds: now.__setitem__(0, now[0] + seconds),
            clock_fn=lambda: now[0],
        )


def test_final_client_endpoint_check_is_fail_closed_without_retry() -> None:
    context = ClientLaunchContext.native()
    game_calls: list[tuple[str, int]] = []
    proxy_calls: list[str] = []

    with pytest.raises(RuntimeError, match="stopped being ready"):
        launcher.require_client_endpoints_ready(
            context,
            game_probe=lambda host, port: game_calls.append((host, port)) or True,
            proxy_probe=lambda url: proxy_calls.append(url) or False,
        )

    assert game_calls == [("127.0.0.1", 26000)]
    assert proxy_calls == ["http://127.0.0.1:26002"]


@pytest.mark.parametrize("status,expected", [(204, True), (404, False), (503, False)])
def test_proxy_health_probe_requires_successful_health_route(
    status: int,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []

    class Response:
        def __init__(self) -> None:
            self.status = status

        @staticmethod
        def read(_size: int) -> bytes:
            return b""

    class Connection:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            events.append(("connect", host, port, timeout))

        def request(self, method: str, path: str) -> None:
            events.append(("request", method, path))

        @staticmethod
        def getresponse() -> Response:
            return Response()

        def close(self) -> None:
            events.append(("close",))

    monkeypatch.setattr(launcher, "HTTPConnection", Connection)

    assert launcher._probe_proxy_health("http://127.0.0.1:27557") is expected
    assert events == [
        ("connect", "127.0.0.1", 27557, 2.0),
        ("request", "GET", "/health"),
        ("close",),
    ]


def test_proxy_health_probe_closes_connection_after_request_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Connection:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            assert (host, port, timeout) == ("localhost", 27557, 2.0)

        @staticmethod
        def request(method: str, path: str) -> None:
            assert (method, path) == ("GET", "/health")
            raise OSError("fixture connection dropped")

        @staticmethod
        def getresponse() -> object:
            return pytest.fail("response must not be read after request failure")

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(launcher, "HTTPConnection", Connection)

    assert launcher._probe_proxy_health("http://localhost:27557/") is False
    assert events == ["close"]


@pytest.mark.parametrize(
    "timeout_sec,poll_interval_sec",
    [(0, 0.5), (1, 0), (1, -0.1)],
)
def test_client_endpoint_gate_rejects_unbounded_timing_values(
    timeout_sec: float,
    poll_interval_sec: float,
) -> None:
    with pytest.raises(ValueError, match="timeout|interval"):
        launcher.wait_for_client_endpoints(
            ClientLaunchContext.native(),
            timeout_sec=timeout_sec,
            poll_interval_sec=poll_interval_sec,
        )


def test_launch_client_rejects_invalid_proxy_before_cache_or_trust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    exe = profile_tq / "bin64" / "exefile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(
        launcher,
        "prepare_evejs_client_certificate_trust",
        lambda *_args: pytest.fail("trust must not be mutated"),
    )

    with pytest.raises(ValueError, match="proxy URL"):
        launcher.launch_client(
            evejs_root=str(tmp_path / "evejs"),
            profile_tq_path=profile_tq,
            proxy_url="invalid-proxy",
            client_path=str(tmp_path / "missing-client" / "tq"),
        )


def test_docker_context_uses_one_complete_authoritative_endpoint_set() -> None:
    endpoints = _docker_endpoints()
    context = _docker_context(endpoints)

    assert context.game_host == "127.0.0.1"
    assert context.game_port == 32600
    assert context.proxy_url == "http://127.0.0.1:32602"
    assert context.image_url == "http://127.0.0.1:32601"
    assert context.target_identity == "docker:fixture-target"
    assert context.settings_identity == "docker-settings:fixture-settings"
    assert context.monitor_generation == 7


def test_docker_context_fails_closed_without_observed_endpoints() -> None:
    with pytest.raises(ValueError, match="endpoints"):
        _docker_context(None)


@pytest.mark.parametrize("missing", ["game", "image", "proxy"])
def test_docker_context_fails_closed_when_required_endpoint_is_incomplete(
    missing: str,
) -> None:
    endpoints = replace(_docker_endpoints(), **{missing: None})

    with pytest.raises(ValueError, match="incomplete"):
        _docker_context(endpoints)


@pytest.mark.parametrize(
    "identity_args",
    [
        {"target_identity": "", "settings_identity": "settings", "monitor_generation": 7},
        {"target_identity": "target", "settings_identity": "", "monitor_generation": 7},
        {"target_identity": "target", "settings_identity": "settings", "monitor_generation": -1},
    ],
)
def test_docker_context_fails_closed_without_complete_identity(
    identity_args: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="identity"):
        ClientLaunchContext.from_docker(_docker_endpoints(), **identity_args)


def test_launch_client_uses_context_proxy_without_native_default_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accept_fixture_cache(monkeypatch)
    profile_tq = tmp_path / "profile" / "tq"
    exe = profile_tq / "bin64" / "ExeFile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    captured: dict[str, object] = {}
    configured_tq = tmp_path / "client" / "tq"
    _create_client_cache(configured_tq)

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(
        launcher,
        "launch_eve_client",
        lambda executable, env, cwd, *, arguments: captured.update(
            executable=executable,
            env=env,
            cwd=cwd,
            arguments=arguments,
        )
        or object(),
    )

    result = launcher.launch_client(
        evejs_root=str(tmp_path / "evejs"),
        profile_tq_path=profile_tq,
        client_path=str(configured_tq),
        launch_context=_docker_context(_docker_endpoints()),
    )

    assert result is not None
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:32602"  # type: ignore[index]
    assert captured["arguments"] == ("/port:32600",)


def test_launch_client_resfiles_come_from_configured_client_not_profile_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accept_fixture_cache(monkeypatch)
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    profile_resfiles = profile_tq.parent / "ResFiles"
    profile_resfiles.mkdir(parents=True)
    exe = profile_tq / "bin64" / "ExeFile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    configured_tq = tmp_path / "configured-client" / "tq"
    configured_resfiles = _create_client_cache(configured_tq)
    captured: dict[str, object] = {}

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(
        launcher,
        "launch_eve_client",
        lambda executable, env, cwd, *, arguments: captured.update(
            executable=executable,
            env=env,
            cwd=cwd,
            arguments=arguments,
        )
        or object(),
    )

    result = launcher.launch_client(
        evejs_root=str(tmp_path / "evejs"),
        profile_tq_path=profile_tq,
        client_path=str(configured_tq),
    )

    assert result is not None
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["EO_REMOTEFILECACHEFOLDER"] == str(configured_resfiles)
    assert env["EO_REMOTEFILECACHEFOLDER"] != str(profile_resfiles)
    assert captured["arguments"] == ("/port:26000",)


def test_launch_client_passes_only_verified_typed_auto_login_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accept_fixture_cache(monkeypatch)
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    exe = profile_tq / "bin64" / "ExeFile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    captured: dict[str, object] = {}
    configured_tq = tmp_path / "client" / "tq"
    _create_client_cache(configured_tq)

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(
        launcher,
        "require_auto_login_arguments",
        lambda intent, **kwargs: (
            "/noconsole",
            f"/login:{intent.username}:fixture-dummy",
            f"/autoSelectCharacter:{intent.character_id}",
        ),
    )
    monkeypatch.setattr(
        launcher,
        "launch_eve_client",
        lambda executable, env, cwd, *, arguments: captured.update(
            executable=executable,
            env=env,
            cwd=cwd,
            arguments=arguments,
        )
        or object(),
    )

    result = launcher.launch_client(
        evejs_root=str(tmp_path / "evejs"),
        profile_tq_path=profile_tq,
        client_path=str(configured_tq),
        launch_context=ClientLaunchContext.native(),
        auto_login=AutoLoginLaunch("fixture-account", 90000001),
    )

    assert result is not None
    assert captured["arguments"] == (
        "/port:26000",
        "/noconsole",
        "/login:fixture-account:fixture-dummy",
        "/autoSelectCharacter:90000001",
    )


@pytest.mark.parametrize("missing", ["resfiles", "index"])
def test_launch_client_rejects_incomplete_selected_resource_cache_before_trust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    client = tmp_path / "client" / "tq"
    client.mkdir(parents=True)
    if missing == "resfiles":
        (client.parent / "index_tranquility.txt").write_text(
            "fixture",
            encoding="utf-8",
        )
    else:
        (client.parent / "ResFiles").mkdir()
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    exe = profile_tq / "bin64" / "exefile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(
        launcher,
        "prepare_evejs_client_certificate_trust",
        lambda *_args: pytest.fail("trust must not be mutated"),
    )
    monkeypatch.setattr(
        launcher,
        "launch_eve_client",
        lambda *_args, **_kwargs: pytest.fail("EVE must not spawn"),
    )

    with pytest.raises(FileNotFoundError, match="ResFiles|resource index"):
        launcher.launch_client(
            evejs_root=str(tmp_path / "evejs"),
            profile_tq_path=profile_tq,
            client_path=str(client),
        )


def test_launch_client_prepares_selected_root_trust_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accept_fixture_cache(monkeypatch)
    root = tmp_path / "evejs"
    client = tmp_path / "client" / "tq"
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    exe = profile_tq / "bin64" / "exefile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    _create_client_cache(client)
    events: list[tuple[object, ...]] = []

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(
        launcher,
        "prepare_evejs_client_certificate_trust",
        lambda selected_root, selected_client: events.append(
            ("trust", selected_root, selected_client)
        )
        or True,
    )
    monkeypatch.setattr(
        launcher,
        "launch_eve_client",
        lambda executable, env, cwd, *, arguments: events.append(
            ("spawn", executable, arguments)
        )
        or object(),
    )

    launcher.launch_client(
        evejs_root=str(root),
        profile_tq_path=profile_tq,
        client_path=str(client),
        pre_spawn_check=lambda: events.append(("endpoint-check",)),
    )

    assert events[0] == ("trust", str(root), client)
    assert events[1] == ("endpoint-check",)
    assert events[2] == ("spawn", exe, ("/port:26000",))


def test_launch_client_does_not_spawn_when_certificate_preparation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accept_fixture_cache(monkeypatch)
    root = tmp_path / "evejs"
    client = tmp_path / "client" / "tq"
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    exe = profile_tq / "bin64" / "exefile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    _create_client_cache(client)

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)

    def fail_trust(_root, _client):  # type: ignore[no-untyped-def]
        raise RuntimeError("trust preparation failed")

    monkeypatch.setattr(
        launcher,
        "prepare_evejs_client_certificate_trust",
        fail_trust,
    )
    monkeypatch.setattr(
        launcher,
        "launch_eve_client",
        lambda *_args, **_kwargs: pytest.fail("EVE must not spawn"),
    )

    with pytest.raises(RuntimeError, match="trust preparation failed"):
        launcher.launch_client(
            evejs_root=str(root),
            profile_tq_path=profile_tq,
            client_path=str(client),
        )


def test_launch_client_does_not_spawn_when_final_endpoint_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accept_fixture_cache(monkeypatch)
    root = tmp_path / "evejs"
    client = tmp_path / "client" / "tq"
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    exe = profile_tq / "bin64" / "exefile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    _create_client_cache(client)

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(
        launcher,
        "prepare_evejs_client_certificate_trust",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        launcher,
        "launch_eve_client",
        lambda *_args, **_kwargs: pytest.fail("EVE must not spawn"),
    )

    with pytest.raises(RuntimeError, match="final endpoint check failed"):
        launcher.launch_client(
            evejs_root=str(root),
            profile_tq_path=profile_tq,
            client_path=str(client),
            pre_spawn_check=lambda: (_ for _ in ()).throw(
                RuntimeError("final endpoint check failed")
            ),
        )
