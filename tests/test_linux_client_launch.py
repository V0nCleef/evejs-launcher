"""Exercise the production Proton preparation boundary without running EVE."""
from dataclasses import replace
import sys
from types import SimpleNamespace

import pytest

from test_linux_installation import linux_installation
from src.core import linux_client
from src.core.launcher import ClientLaunchContext
from src.core.overview_state import OverviewBridgeLaunch
from src.workers.client_launch_worker import ClientLaunchRequest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux profile links and locks')


@pytest.fixture
def prepared_launch(linux_installation, monkeypatch, tmp_path):
    from src.core import launcher, client_autologin, mod_api_runtime, mod_client_notifications, shared_client_settings
    monkeypatch.setattr(linux_client.LinuxInstallation, 'verified_market_binary', lambda self: tmp_path / 'market')
    for method in ('require_client_trust', 'require_client_patches', 'require_prefix', 'require_backend_tls'):
        monkeypatch.setattr(linux_client, method, lambda *args: None)
    monkeypatch.setattr(launcher, 'wait_for_client_endpoints', lambda *args: None)
    monkeypatch.setattr(launcher, '_resolve_client_resource_cache', lambda *args: linux_installation.cache / 'ResFiles')
    monkeypatch.setattr(shared_client_settings, 'require_shared_settings_support', lambda *args: None)
    monkeypatch.setattr(client_autologin, 'inspect_auto_login_capability', lambda *args: SimpleNamespace(supported=True))
    monkeypatch.setattr(client_autologin, 'require_auto_login_arguments', lambda *args, **kwargs: ('/login=fixture',))
    monkeypatch.setattr(linux_client, 'ProtonClientProcess', lambda raw, option, exe: raw)
    calls = []
    monkeypatch.setattr(linux_client.subprocess, 'Popen', lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(pid=123))
    mods = SimpleNamespace(environment={'EVEJS_FIXTURE_MOD': 'ready'}, arguments=('/fixtureMod=ready',))
    monkeypatch.setattr(mod_api_runtime, 'prepare_public_client_mods', lambda *args, **kwargs: mods)
    monkeypatch.setattr(mod_client_notifications, 'start_client_notifications', lambda *args, **kwargs: None)
    request = ClientLaunchRequest('fixture-account', 'Fixture Pilot', str(linux_installation.root),
        str(linux_installation.client), tmp_path / 'profiles', ClientLaunchContext.native(), auto_login_enabled=True)
    return request, calls


def test_idless_named_launch_preserves_manual_login_and_adds_portable_mod_options(prepared_launch):
    request, calls = prepared_launch
    linux_client.perform_linux_client_launch(request)
    command, options = calls[0]
    assert command[1] == 'run'
    assert '/login=fixture' not in command
    assert '/fixtureMod=ready' in command
    assert options['env']['EVEJS_FIXTURE_MOD'] == 'ready'
    assert '/server:127.0.0.1' in command and '/port:26000' in command
    assert any(value.startswith('/settingsprofile=EveJSLauncher_') for value in command)
    assert options['start_new_session']


def test_authenticated_launch_passes_login_and_wine_visible_overview_ack(prepared_launch, tmp_path):
    request, calls = prepared_launch
    ack = tmp_path / 'overview ack'
    request = replace(request, character_id=42, account_id=7,
        overview_bridge=OverviewBridgeLaunch('capture|42', ack))
    linux_client.perform_linux_client_launch(request)
    command, options = calls[0]
    assert '/login=fixture' in command
    assert options['env']['EVEJS_OVERVIEW_BRIDGE'] == 'capture|42'
    assert options['env']['EVEJS_OVERVIEW_ACK_PATH'] == linux_client.wine_path(ack)


def test_two_launchers_for_one_account_use_distinct_owned_process_markers(prepared_launch):
    request, calls = prepared_launch
    first = linux_client.perform_linux_client_launch(request)
    first_alias = first.settings_alias
    second = linux_client.perform_linux_client_launch(request)
    first_option = next(arg for arg in calls[0][0] if arg.startswith('/settingsprofile='))
    second_option = next(arg for arg in calls[1][0] if arg.startswith('/settingsprofile='))
    assert first_option != second_option
    assert first_alias[0] != second.settings_alias[0]
    assert first_alias[0].resolve() == second.settings_alias[0].resolve()


def test_failed_spawn_removes_only_its_disposable_settings_alias(prepared_launch, monkeypatch):
    request, calls = prepared_launch
    def fail(*args, **kwargs):
        raise OSError('fixture spawn failure')
    monkeypatch.setattr(linux_client.subprocess, 'Popen', fail)
    with pytest.raises(OSError, match='spawn failure'):
        linux_client.perform_linux_client_launch(request)
    installation = linux_client.LinuxInstallation.load(request.evejs_root)
    assert not list(linux_client.settings_base(installation).glob('settings_EveJSLauncher_run_*'))
    account = linux_client.account_profile_name(installation, request.username)
    assert (linux_client.settings_base(installation) / ('settings_' + account)).is_dir()
