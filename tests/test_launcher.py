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


def test_native_context_retains_existing_defaults() -> None:
    context = ClientLaunchContext.native()

    assert context.game_host == "127.0.0.1"
    assert context.game_port == 26000
    assert context.proxy_url == "http://127.0.0.1:26002"
    assert context.image_url is None
    assert context.target_identity is None
    assert context.settings_identity is None
    assert context.monitor_generation is None


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
    profile_tq = tmp_path / "profile" / "tq"
    exe = profile_tq / "bin64" / "ExeFile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    captured: dict[str, object] = {}

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(
        launcher,
        "launch_eve_client",
        lambda executable, env, cwd: captured.update(
            executable=executable,
            env=env,
            cwd=cwd,
        )
        or object(),
    )

    result = launcher.launch_client(
        evejs_root=str(tmp_path / "evejs"),
        profile_tq_path=profile_tq,
        client_path=str(tmp_path / "client" / "tq"),
        launch_context=_docker_context(_docker_endpoints()),
    )

    assert result is not None
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:32602"  # type: ignore[index]


def test_launch_client_resfiles_come_from_configured_client_not_profile_junction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    profile_resfiles = profile_tq.parent / "ResFiles"
    profile_resfiles.mkdir(parents=True)
    exe = profile_tq / "bin64" / "ExeFile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    configured_tq = tmp_path / "configured-client" / "tq"
    configured_resfiles = configured_tq.parent / "ResFiles"
    configured_resfiles.mkdir(parents=True)
    captured: dict[str, object] = {}

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(
        launcher,
        "launch_eve_client",
        lambda executable, env, cwd: captured.update(
            executable=executable,
            env=env,
            cwd=cwd,
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


def test_launch_client_passes_only_verified_typed_auto_login_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    exe = profile_tq / "bin64" / "ExeFile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    captured: dict[str, object] = {}

    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(
        launcher,
        "require_auto_login_arguments",
        lambda intent, **kwargs: (
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
        client_path=str(tmp_path / "client" / "tq"),
        launch_context=ClientLaunchContext.native(),
        auto_login=AutoLoginLaunch("fixture-account", 90000001),
    )

    assert result is not None
    assert captured["arguments"] == (
        "/login:fixture-account:fixture-dummy",
        "/autoSelectCharacter:90000001",
    )


def test_launch_client_prepares_selected_root_trust_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evejs"
    client = tmp_path / "client" / "tq"
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    exe = profile_tq / "bin64" / "exefile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    client.mkdir(parents=True)
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
        lambda executable, env, cwd: events.append(("spawn", executable))
        or object(),
    )

    launcher.launch_client(
        evejs_root=str(root),
        profile_tq_path=profile_tq,
        client_path=str(client),
    )

    assert events[0] == ("trust", str(root), client)
    assert events[1] == ("spawn", exe)


def test_launch_client_does_not_spawn_when_certificate_preparation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evejs"
    client = tmp_path / "client" / "tq"
    profile_tq = tmp_path / "profiles" / "account" / "tq"
    exe = profile_tq / "bin64" / "exefile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    client.mkdir(parents=True)

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
