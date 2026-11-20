"""Linux Docker client endpoints stay tied to the observed running container."""
from dataclasses import replace
import ssl
import sys
from types import SimpleNamespace

import pytest

from test_linux_client_launch import prepared_launch
from test_linux_installation import linux_installation
from test_docker_autologin import _fixture, _Inspector, _FULL_ID
from src.core import linux_client, linux_docker_client as docker_client
from src.core.linux_installation import LinuxSetupError
from src.core.runtime.endpoints import Endpoint

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux Proton profiles')


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    target, cfg, report, context, probe = _fixture(tmp_path)
    inspector = _Inspector(report)
    actual = [cfg.endpoints.game, cfg.endpoints.image, cfg.endpoints.proxy, cfg.endpoints.assets, cfg.endpoints.xmpp,
              Endpoint('server', '127.0.0.1', 8443, 443, 'tcp')]
    inspector.container_tcp_publications = lambda *args: tuple(actual)
    request = SimpleNamespace(docker_auto_login_probe=probe, launch_context=context)
    installation = SimpleNamespace(root=tmp_path, cdn_port=8443)
    monkeypatch.setattr(docker_client, 'probe_http_health', lambda *args: True)
    monkeypatch.setattr(docker_client, 'probe_endpoint', lambda *args: True)
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(docker_client.socket, 'create_connection', lambda *args, **kwargs: Connection())
    monkeypatch.setattr(docker_client, 'local_ca_context', lambda ca:
        SimpleNamespace(wrap_socket=lambda *args, **kwargs: Connection()))
    return request, installation, inspector, actual


def test_exact_container_publications_and_tls_are_required(runtime):
    request, installation, inspector, _ = runtime
    docker_client.require_docker_runtime(request, installation, inspector=inspector)
    assert inspector.preflight_calls == 2


def test_missing_cdn_publication_cannot_use_an_unrelated_native_listener(runtime):
    request, installation, inspector, actual = runtime
    actual.pop()
    with pytest.raises(LinuxSetupError, match='Publish.*8443'):
        docker_client.require_docker_runtime(request, installation, inspector=inspector)


def test_stale_port_mapping_is_rejected(runtime):
    request, installation, inspector, actual = runtime
    actual[0] = replace(actual[0], port=28000)
    with pytest.raises(LinuxSetupError, match='does not publish'):
        docker_client.require_docker_runtime(request, installation, inspector=inspector)


def test_wrong_docker_ca_is_rejected(runtime, monkeypatch):
    request, installation, inspector, _ = runtime
    def fail(*args, **kwargs):
        raise ssl.SSLError('fixture CA mismatch')
    monkeypatch.setattr(docker_client.socket, 'create_connection', fail)
    with pytest.raises(LinuxSetupError, match='do not match the CA'):
        docker_client.require_docker_runtime(request, installation, inspector=inspector)


def test_changed_docker_runtime_is_rejected(runtime):
    from src.core.runtime.docker_autologin import DockerAutoLoginContextError
    request, installation, inspector, _ = runtime
    inspector.runtime_identities[-1] = 'c' * 64
    with pytest.raises(DockerAutoLoginContextError, match='changed'):
        docker_client.require_docker_runtime(request, installation, inspector=inspector)


def test_linux_docker_launch_uses_its_own_endpoint_scope_and_mod_backend(prepared_launch, tmp_path, monkeypatch):
    from src.core import mod_api_runtime, mod_client_notifications
    from pathlib import Path
    request, calls = prepared_launch
    original = request.evejs_root
    compose_root = tmp_path / 'compose-project'
    compose_root.mkdir()
    _, _, _, context, probe = _fixture(compose_root)
    context = replace(context, game_port=28000, proxy_url='http://127.0.0.1:28002')
    request = replace(request, evejs_root=str(compose_root), linux_setup_root=original,
                      launch_context=context, docker_auto_login_probe=probe, auto_login_enabled=False)
    installation = linux_client.LinuxInstallation.load(original)
    (installation.client / 'bin64/_trinity_dx12.dll').write_bytes(b'renderer fixture')
    for root, selected in ((Path(original), 'dx11'), (compose_root, 'dx12')):
        config = root / 'tools/ClientSETUP/scripts/EvEJSConfig.bat'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(f'set "EVEJS_CLIENT_RENDERER={selected}"\n')
    checks = []
    monkeypatch.setattr(docker_client, 'require_docker_runtime', lambda *args: checks.append(args[0]))
    def forbidden(*args):
        raise AssertionError('Docker launch must not require Native Market')
    monkeypatch.setattr(linux_client.LinuxInstallation, 'verified_market_binary', forbidden)
    mods = []
    notifications = []
    monkeypatch.setattr(mod_api_runtime, 'prepare_public_client_mods', lambda *args, **kwargs:
        mods.append((args, kwargs)) or SimpleNamespace(environment={}, arguments=()))
    monkeypatch.setattr(mod_client_notifications, 'start_client_notifications', lambda *args, **kwargs: notifications.append(kwargs))
    process = linux_client.perform_linux_client_launch(request)
    assert len(checks) == 2
    assert '/port:28000' in calls[0][0]
    assert calls[0][1]['env']['HTTP_PROXY'] == 'http://127.0.0.1:28002'
    assert calls[0][1]['env']['TRINITYPLATFORM'] == 'dx12'
    assert mods[0][0][0] == str(compose_root)
    assert mods[0][1]['backend'] == 'docker'
    assert notifications[0]['backend'] == 'docker'
    installation = linux_client.LinuxInstallation.load(original)
    expected = linux_client.account_profile_name(installation, request.username, context.target_identity)
    assert process.settings_alias[1].name == 'settings_' + expected
    assert expected != linux_client.account_profile_name(installation, request.username)


def test_docker_account_creation_is_verified_before_profile_mutation(prepared_launch, tmp_path, monkeypatch):
    from src.core.runtime import docker_autologin
    request, calls = prepared_launch
    _, _, _, context, probe = _fixture(tmp_path)
    request = replace(request, launch_context=context, docker_auto_login_probe=probe,
                      require_account_creation=True)
    monkeypatch.setattr(docker_client, 'require_docker_runtime', lambda *args: None)
    def disabled(*args):
        raise LinuxSetupError('Online account creation is disabled')
    monkeypatch.setattr(docker_autologin, 'require_docker_account_creation', disabled)
    with pytest.raises(LinuxSetupError, match='disabled'):
        linux_client.perform_linux_client_launch(request)
    assert not calls and not request.profiles_root.exists()
