"""Delivery notices are scoped, optional and never disable a mod."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from src import config
from src.core import mod_api_runtime as runtime
from src.core.mod_client_delivery import FEATURE, FEATURE_ENV, LEGACY_GUIDE_URL, record_delivery, reported_delivery
from src.core.mod_activation_state import project_mod_activation
from src.pages.mods_page import ModRow
from test_mod_api_runtime_protocol import make_mod, helper_runner, row_for


def report(tmp_path, monkeypatch, method='client-script-patch'):
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path / 'launcher-settings')
    descriptor, context = make_mod(tmp_path, kind='settings')
    def metadata(payload, request):
        if method is not None:
            payload['clientScriptDelivery'] = method
    result = runtime.run_mod_helper(descriptor, 'verify', context,
        runner=helper_runner(receipt=False, mutate=metadata)).require_ready()
    return result, row_for(descriptor)


@pytest.mark.parametrize('method', [None, 'none', 'login-handshake', 'client-script-patch'])
def test_legacy_helpers_and_each_explicit_method_remain_supported(tmp_path, monkeypatch, method):
    result, mod = report(tmp_path, monkeypatch, method)
    record_delivery(result, 'native')
    assert reported_delivery(mod, result.context.client_root, 'native') == method


def test_host_advertises_metadata_without_changing_request_schema(tmp_path):
    descriptor, context = make_mod(tmp_path, kind='settings')
    requests = []
    runtime.run_mod_helper(descriptor, 'verify', context,
        runner=helper_runner(receipt=False, requests=requests)).require_ready()
    request, _, options = requests[0]
    assert FEATURE in options['environment'][FEATURE_ENV].split(',')
    assert set(request) == {'protocol', 'requestId', 'action', 'mod', 'runtime', 'profile', 'settings'}


def test_observation_does_not_bleed_between_clients_backends_roots_or_versions(tmp_path, monkeypatch):
    result, mod = report(tmp_path, monkeypatch)
    record_delivery(result, 'native')
    assert reported_delivery(mod, result.context.client_root, 'native') == 'client-script-patch'
    assert reported_delivery(mod, tmp_path / 'another-client', 'native') is None
    assert reported_delivery(mod, result.context.client_root, 'docker') is None
    assert reported_delivery(replace(mod, active=False), result.context.client_root, 'native') is None
    other = replace(mod.api_descriptor, root=tmp_path / 'another-server')
    assert reported_delivery(replace(mod, api_descriptor=other), result.context.client_root, 'native') is None
    other = replace(mod.api_descriptor, identity='different-manifest')
    assert reported_delivery(replace(mod, api_descriptor=other), result.context.client_root, 'native') is None
    mod.api_descriptor.launcher_api.helper.path.write_bytes(b'updated-helper')
    assert reported_delivery(mod, result.context.client_root, 'native') is None


def test_switching_to_login_or_removing_metadata_clears_legacy_notice(tmp_path, monkeypatch):
    result, mod = report(tmp_path, monkeypatch)
    record_delivery(result, 'native')
    record_delivery(replace(result, client_script_delivery='login-handshake'), 'native')
    assert reported_delivery(mod, result.context.client_root, 'native') == 'login-handshake'
    record_delivery(replace(result, client_script_delivery=None), 'native')
    assert reported_delivery(mod, result.context.client_root, 'native') is None
    record_delivery(result, 'native')
    record_delivery(replace(result, action='prepare_disable'), 'native')
    assert reported_delivery(mod, result.context.client_root, 'native') is None


def test_bad_cache_or_unwritable_notice_storage_cannot_block_mods(tmp_path, monkeypatch):
    result, mod = report(tmp_path, monkeypatch)
    record_delivery(result, 'native')
    cache = next((config.CONFIG_DIR / 'mod-client-delivery').glob('*.json'))
    cache.write_text('bad json')
    assert reported_delivery(mod, result.context.client_root, 'native') is None
    blocker = tmp_path / 'not-a-directory'; blocker.write_text('x')
    monkeypatch.setattr(config, 'CONFIG_DIR', blocker)
    record_delivery(result, 'native')
    assert reported_delivery(mod, result.context.client_root, 'native') is None
    assert result.require_ready() is result


@pytest.mark.parametrize('method', ['archive', None, {}, [], True])
def test_malformed_opt_in_reporting_does_not_invent_a_delivery_mode(tmp_path, monkeypatch, method):
    descriptor, context = make_mod(tmp_path, kind='settings')
    def metadata(payload, request):
        payload['clientScriptDelivery'] = method
    with pytest.raises(runtime.ModApiRuntimeError, match='delivery method'):
        runtime.run_mod_helper(descriptor, 'verify', context,
            runner=helper_runner(receipt=False, mutate=metadata))


@pytest.mark.parametrize('method,active,visible', [
    ('client-script-patch', True, True), ('login-handshake', True, False),
    ('none', True, False), (None, True, False), ('client-script-patch', False, False)])
def test_notice_only_labels_reported_active_script_patching_and_never_blocks_toggle(qapp, tmp_path, monkeypatch, method, active, visible):
    result, mod = report(tmp_path, monkeypatch, method)
    mod = replace(mod, active=active)
    projection = project_mod_activation(mod, None)
    row = ModRow(mod, projection=projection, projection_resolver=lambda _: projection,
                 client_script_delivery=method, delegated_activation=True)
    try:
        row.show(); qapp.processEvents()
        assert row.delivery_notice.isVisible() == visible
        assert row.delivery_help.isVisible() == visible
        assert row.toggle.isEnabled()
        assert 'still supported' in row.delivery_notice.text()
        row.mod.active = False
        row._update_state_presentation()
        assert row.delivery_notice.isHidden()
        assert row.delivery_help.isHidden()
    finally:
        row.close(); row.deleteLater()


def test_notice_help_opens_the_specific_guide_section_without_changing_activation(qapp, tmp_path, monkeypatch):
    result, mod = report(tmp_path, monkeypatch)
    projection = project_mod_activation(mod, None)
    opened = []
    monkeypatch.setattr('src.pages.mods_page.QDesktopServices.openUrl', lambda url: opened.append(url.toString()) or True)
    row = ModRow(mod, projection=projection, projection_resolver=lambda _: projection,
                 client_script_delivery='client-script-patch', delegated_activation=True)
    try:
        row.delivery_help.click()
        assert opened == [LEGACY_GUIDE_URL]
        assert mod.active and row.toggle.isEnabled()
        page = Path(__file__).resolve().parents[1] / 'docs/how-to-make-a-mod/12-client-files.md'
        assert '## Legacy client script patches' in page.read_text(encoding='utf-8')
    finally:
        row.close(); row.deleteLater()
