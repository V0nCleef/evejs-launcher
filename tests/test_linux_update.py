"""Real ELF/process update transactions, including rollback before runtime adoption."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux installer process and filesystem semantics')
if sys.platform == 'linux':
    from src.core.helpers import linux_update_agent as agent
    from src.updater import linux_update as update


def until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Fixture process timed out')
        time.sleep(.02)


@pytest.fixture
def transaction(tmp_path):
    if not shutil.which('cc'):
        pytest.skip('real ELF transaction fixture requires cc')
    source = tmp_path / 'fixture.c'
    source.write_text('#include <unistd.h>\n#include <stdio.h>\nint main(int argc,char**argv){if(argc>1){sleep(20);}else{FILE*f=fopen("rollback-observed","w");if(f){fputs("restarted",f);fclose(f);}}return 0;}')
    binary = tmp_path / 'fixture'
    subprocess.run(['cc', '-o', str(binary), str(source)], check=True)
    install = tmp_path / 'installed'
    # Ubuntu may use umask 0002. The updater correctly rejects a group-writable
    # installation, so the ordinary transaction fixture must set its own mode.
    install.mkdir(mode=0o700)
    shutil.copy2(binary, install / agent.EXECUTABLE)
    (install / '_internal').mkdir()
    (install / '_internal/VERSION').write_text('1.0.0')
    (install / '_internal/liboriginal.so').write_text('old runtime')
    # Unknown user material stays in place through commit and rollback.
    (install / 'user-notes.txt').write_text('keep this')
    (install / 'user-link').symlink_to(tmp_path / 'external-user-file')
    old = subprocess.Popen([str(install / agent.EXECUTABLE), '--old'], start_new_session=True)
    parent = tmp_path / 'updates'
    parent.mkdir(mode=0o700)
    directory = parent / 'transaction-fixture'
    directory.mkdir(mode=0o700)
    candidate = directory / 'unpacked/EveJS-Launcher-Linux-x86_64'
    candidate.mkdir(parents=True)
    shutil.copy2(binary, candidate / agent.EXECUTABLE)
    (candidate / '_internal').mkdir()
    (candidate / '_internal/VERSION').write_text('2.0.0')
    (candidate / '_internal/libnew.so.1').write_text('new runtime')
    (candidate / '_internal/libnew.so').symlink_to('libnew.so.1')
    request = {'schema': 1, 'install': str(install), 'created': time.time(),
        'parent': agent.identity(old.pid), 'token': 'a' * 64, 'version': '2.0.0',
        'digest': agent.tree_digest(candidate), 'originalDigest': agent.tree_digest(install),
        'environment': dict(os.environ)}
    agent.publish(directory / 'request.json', request)
    children = []
    yield directory, install, old, request, children
    if old.poll() is None:
        old.kill()
    old.wait(timeout=5)
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
    candidate_record = directory / 'candidate.json'
    if candidate_record.exists():
        pin = agent.read_json(candidate_record)['process']
        if agent.running(pin):
            os.killpg(pin['pid'], 9)


def launch_agent(directory, children, *, parent_timeout=3, startup_timeout=3):
    code = ('from pathlib import Path; from src.core.helpers.linux_update_agent import run_agent; '
            f'raise SystemExit(run_agent(Path({str(directory)!r}), parent_timeout={parent_timeout}, startup_timeout={startup_timeout}))')
    process = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    children.append(process)
    return process


def test_group_writable_installation_is_rejected(transaction):
    directory, install, old, request, children = transaction
    install.chmod(0o775)
    with pytest.raises(ValueError, match='invalid or expired'):
        agent.load_request(directory)


def test_successful_switch_requires_exact_startup_ack_and_preserves_user_files(transaction):
    directory, install, old, request, children = transaction
    process = launch_agent(directory, children)
    until(lambda: (directory / 'ready.json').exists())
    assert (install / '_internal/VERSION').read_text() == '1.0.0'
    old.terminate(); old.wait(timeout=5)
    until(lambda: (directory / 'candidate.json').exists())
    assert not (directory / 'commit.json').exists()
    candidate = agent.read_json(directory / 'candidate.json')
    agent.publish(directory / 'ack.json', candidate)
    assert process.wait(timeout=10) == 0, process.stderr.read().decode()
    assert agent.read_json(directory / 'commit.json') == candidate
    assert (install / '_internal/VERSION').read_text() == '2.0.0'
    assert (install / '_internal/libnew.so').is_symlink()
    assert (install / '.evejs-launcher-rollback/_internal/VERSION').read_text() == '1.0.0'
    assert (install / 'user-notes.txt').read_text() == 'keep this'
    assert (install / 'user-link').is_symlink()
    assert not list(install.glob('.evejs-launcher-stage-*'))
    assert not (directory / 'unpacked').exists()


@pytest.mark.parametrize('bad_ack', [False, True])
def test_missing_or_forged_ack_rolls_back_and_relaunches_original(transaction, bad_ack):
    directory, install, old, request, children = transaction
    checkpoint = install / 'runtime-checkpoint-fixture'
    checkpoint.write_text('one-use ticket must remain unconsumed')
    process = launch_agent(directory, children, startup_timeout=.4)
    until(lambda: (directory / 'ready.json').exists())
    old.terminate(); old.wait(timeout=5)
    until(lambda: (directory / 'candidate.json').exists())
    if bad_ack:
        agent.publish(directory / 'ack.json', {'token': 'b' * 64, 'process': {'pid': old.pid}})
    assert process.wait(timeout=10) == 1, process.stderr.read().decode()
    assert agent.tree_digest(install) == request['originalDigest']
    assert agent.read_json(directory / 'result.json')['state'] == 'rolled-back'
    assert not (directory / 'commit.json').exists()
    until(lambda: (install / 'rollback-observed').exists())
    assert checkpoint.read_text() == 'one-use ticket must remain unconsumed'
    assert not list(install.glob('.evejs-launcher-stage-*'))


def test_live_parent_timeout_leaves_original_running(transaction):
    directory, install, old, request, children = transaction
    process = launch_agent(directory, children, parent_timeout=.2)
    assert process.wait(timeout=10) == 1, process.stderr.read().decode()
    assert agent.tree_digest(install) == request['originalDigest']
    assert old.poll() is None
    assert not list(install.glob('.evejs-launcher-stage-*'))


def test_cancel_before_exit_leaves_original_running(transaction):
    directory, install, old, request, children = transaction
    process = launch_agent(directory, children)
    until(lambda: (directory / 'ready.json').exists())
    agent.publish(directory / 'cancel.json', {'token': request['token']})
    assert process.wait(timeout=10) == 1
    assert agent.tree_digest(install) == request['originalDigest']
    assert old.poll() is None


def test_agent_termination_before_gui_exit_cleans_staged_runtime(transaction):
    directory, install, old, request, children = transaction
    process = launch_agent(directory, children)
    until(lambda: (directory / 'ready.json').exists())
    process.terminate()
    assert process.wait(timeout=10) == 1
    assert agent.tree_digest(install) == request['originalDigest']
    assert old.poll() is None
    assert not list(install.glob('.evejs-launcher-stage-*'))
    assert agent.read_json(directory / 'request.json')['environment'] == {}


def test_copy_failure_never_replaces_original(transaction, monkeypatch):
    directory, install, old, request, children = transaction
    def failure(*args, **kwargs):
        raise OSError('fixture disk write failed')
    monkeypatch.setattr(agent.shutil, 'copytree', failure)
    assert agent.run_agent(directory) == 1
    assert not (directory / 'ready.json').exists()
    assert agent.tree_digest(install) == request['originalDigest']
    assert old.poll() is None


def test_changed_stage_is_rejected_before_ready(transaction):
    directory, install, old, request, children = transaction
    (directory / 'unpacked/EveJS-Launcher-Linux-x86_64/_internal/VERSION').write_text('changed')
    assert agent.run_agent(directory) == 1
    assert not (directory / 'ready.json').exists()
    assert agent.tree_digest(install) == request['originalDigest']


def test_partial_switch_failure_restores_both_original_components(transaction, monkeypatch):
    directory, install, old, request, children = transaction
    replace = agent.os.replace
    wait = agent.wait_until
    calls = []

    def finish_parent(predicate, timeout, child=None):
        if not calls:
            calls.append('exited')
            old.terminate(); old.wait(timeout=5)
        return wait(predicate, timeout, child)

    def fail_runtime_switch(source, target):
        if Path(source).parent.name.startswith('.evejs-launcher-stage-') and Path(source).name == '_internal':
            raise OSError('fixture runtime replacement failure')
        return replace(source, target)

    monkeypatch.setattr(agent, 'wait_until', finish_parent)
    monkeypatch.setattr(agent.os, 'replace', fail_runtime_switch)
    assert agent.run_agent(directory) == 1
    assert agent.tree_digest(install) == request['originalDigest']
    assert (install / 'user-notes.txt').read_text() == 'keep this'
    until(lambda: (install / 'rollback-observed').exists())


@pytest.mark.parametrize('url', [
    'https://evil.test/V0nCleef/evejs-launcher/releases/download/v2.0.0/EveJS-Launcher-Linux-x86_64.tar.gz',
    'http://github.com/V0nCleef/evejs-launcher/releases/download/v2.0.0/EveJS-Launcher-Linux-x86_64.tar.gz',
    'https://github.com/other/repo/releases/download/v2.0.0/EveJS-Launcher-Linux-x86_64.tar.gz',
    'https://github.com/V0nCleef/evejs-launcher/releases/download/v2.0.0/EveJS-Launcher-V1.zip',
    'https://github.com/V0nCleef/evejs-launcher/releases/download/v2.0.0/EveJS-Launcher-Linux-x86_64.tar.gz?x=1',
])
def test_rejects_unattributed_linux_downloads(url):
    with pytest.raises(ValueError):
        update.release_version(url)


def test_pinned_release_asset_version():
    assert update.release_version('https://github.com/V0nCleef/evejs-launcher/releases/download/v2.0.0/EveJS-Launcher-Linux-x86_64.tar.gz') == '2.0.0'


def test_receipt_retention_prunes_only_completed_attributed_install_transactions(tmp_path):
    parent = tmp_path / 'updates'
    parent.mkdir(mode=0o700)
    install = tmp_path / 'installed'
    install.mkdir()
    folders = []
    for index in range(4):
        folder = parent / ('transaction-' + str(index) * 8)
        folder.mkdir(mode=0o700)
        request = {'schema': 1, 'install': str(install), 'created': float(index),
                   'parent': {'pid': 0, 'uid': os.getuid(), 'image': str(install / agent.EXECUTABLE)}}
        agent.publish(folder / 'request.json', request)
        agent.publish(folder / 'result.json', {'state': 'failed'})
        folders.append(folder)
    (folders[0] / 'user-notes.txt').write_text('preserve unknown material')
    request = agent.read_json(folders[1] / 'request.json')
    agent.publish(folders[1] / 'request.json', dict(request, install=str(tmp_path / 'another-install')))
    update._prune_transactions(parent, install)
    assert folders[0].exists() and folders[1].exists() and folders[3].exists()
    assert not folders[2].exists()
