from dataclasses import replace
import json
from pathlib import Path

import pytest

from src import config
from src.core import mod_api_runtime as runtime
from src.core import mod_client_preparation as preparation
from src.core.mod_api_manifest import read_api_manifest
from test_mod_api_runtime_protocol import make_mod, helper_runner, row_for


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path/'host')
    monkeypatch.setattr(runtime, 'is_eve_client_running', lambda: False)
    descriptor, context = make_mod(tmp_path, name='example', version='2.0.0', kind='settings')
    document = json.loads(descriptor.manifest_path.read_text())
    document['updates'] = {'provider':'github','repository':'example/mod','asset':'Mod-{version}.zip'}
    descriptor.manifest_path.write_text(json.dumps(document))
    descriptor = read_api_manifest(context.evejs_root, context.mod_folder)

    def enroll(payload, request):
        payload['clientPreparation'] = {'mode':'verify-install','legacyVersions':['1.0.0']}

    result = runtime.run_mod_helper(descriptor, 'install', context,
        runner=helper_runner(receipt=False, mutate=enroll))
    preparation.record_preparation(result)
    monkeypatch.setenv('LOCALAPPDATA', str(context.profile_settings_root))
    monkeypatch.setattr(runtime.platform_api, 'get_eve_settings_path', lambda _: context.profile_settings_root)
    return descriptor, context, result


def test_enrollment_is_exact_family_version_and_client_scoped(tmp_path, monkeypatch):
    descriptor, context, result = setup(tmp_path, monkeypatch)
    assert preparation.enrolled(descriptor, context.client_root)
    assert preparation.enrolled(replace(descriptor, version='1.0.0', root=tmp_path/'other-root'), context.client_root)
    assert not preparation.enrolled(replace(descriptor, version='1.0.1'), context.client_root)
    assert not preparation.enrolled(replace(descriptor, id='other'), context.client_root)
    assert not preparation.enrolled(replace(descriptor, updates=replace(descriptor.updates,repository='different/mod')), context.client_root)
    other = tmp_path/'other-client'; other.mkdir()
    assert not preparation.enrolled(descriptor, other)
    # Removal does not discard compatibility information needed by another root.
    preparation.record_preparation(replace(result, action='prepare_remove'))
    assert preparation.enrolled(descriptor, context.client_root)


@pytest.mark.parametrize('scenario,expected', [
    ('healthy',['verify','prepare_profile']),
    ('repair',['verify','install','verify','prepare_profile']),
    ('failed-install',['verify','install']),
    ('failed-recheck',['verify','install','verify']),
    ('running',['verify']),
    ('running-healthy',['verify','prepare_profile']),
    ('malformed',['verify']),
])
def test_preparation_uses_explicit_global_install_and_rechecks(tmp_path, monkeypatch, scenario, expected):
    descriptor, context, _ = setup(tmp_path, monkeypatch)
    requests = []
    checks = [0]
    monkeypatch.setattr(runtime,'is_eve_client_running',lambda: scenario.startswith('running'))

    def reply(payload, request):
        if request['action'] == 'verify':
            checks[0] += 1
            healthy = scenario in ('healthy','running-healthy') or (checks[0] > 1 and scenario != 'failed-recheck')
            if not healthy: payload.update(success=False, state='failed', message='Missing companion')
            if scenario == 'malformed': payload['requestId'] = 'incorrect'
        if request['action'] == 'install':
            assert request['profile'] is None
            assert request['settings']['profile'] == {}
            if scenario == 'failed-install': payload.update(success=False,state='failed',message='Refused changed entry')

    runner = helper_runner(receipt=False, mutate=reply, requests=requests, contributions=[])
    launch = lambda: runtime.prepare_public_client_mods(context.evejs_root,context.client_root,
        context.profile_root/'tq',runner=runner,mods=[row_for(descriptor)])
    if scenario in ('healthy','repair','running-healthy'):
        launch()
    else:
        with pytest.raises(runtime.ModApiRuntimeError): launch()
    assert [r[0]['action'] for r in requests] == expected


def test_unenrolled_mod_keeps_its_existing_profile_only_behavior(tmp_path, monkeypatch):
    descriptor, context, _ = setup(tmp_path, monkeypatch)
    for file in (config.CONFIG_DIR/'mod-client-preparation').glob('*.json'): file.unlink()
    requests=[]
    runtime.prepare_public_client_mods(context.evejs_root, context.client_root, context.profile_root/'tq',
        runner=helper_runner(receipt=False,requests=requests),mods=[row_for(descriptor)])
    assert [r[0]['action'] for r in requests] == ['prepare_profile']


@pytest.mark.parametrize('policy', [None, {}, {'mode':'always','legacyVersions':[]},
    {'mode':'verify-install','legacyVersions':['*']},
    {'mode':'verify-install','legacyVersions':['1.0.0','1.0.0']}])
def test_invalid_enrollment_is_rejected(tmp_path, monkeypatch, policy):
    descriptor, context, _ = setup(tmp_path, monkeypatch)
    with pytest.raises(runtime.ModApiRuntimeError):
        runtime.run_mod_helper(descriptor,'install',context,
            runner=helper_runner(receipt=False,mutate=lambda p,r:p.update(clientPreparation=policy)))


def test_verification_cannot_enroll_and_corrupt_records_do_not_trigger_install(tmp_path, monkeypatch):
    descriptor, context, _ = setup(tmp_path, monkeypatch)
    with pytest.raises(runtime.ModApiRuntimeError, match='completed install'):
        runtime.run_mod_helper(descriptor,'verify',context,runner=helper_runner(receipt=False,
            mutate=lambda p,r:p.update(clientPreparation={'mode':'verify-install','legacyVersions':[]})))
    next((config.CONFIG_DIR/'mod-client-preparation').glob('*.json')).write_text('{')
    requests=[]
    with pytest.raises(ValueError):
        runtime.prepare_public_client_mods(context.evejs_root,context.client_root,context.profile_root/'tq',
            runner=helper_runner(receipt=False,requests=requests),mods=[row_for(descriptor)])
    assert not requests
