"""Native provider repair must precede source-bound executable verification."""
from types import SimpleNamespace

import pytest

from src.core.linux_installation import LinuxInstallation, LinuxSetupError
from src.workers.server_worker import ServiceStartWorker


@pytest.mark.parametrize('case', ['repair', 'prepare-failure', 'unrepaired'])
def test_provider_prepares_before_final_binary_verification(qapp, tmp_path, monkeypatch, case):
    monkeypatch.setattr('src.core.host.IS_LINUX', True)
    monkeypatch.setattr('src.core.linux_client.require_backend_tls', lambda *_: None)
    calls = []
    repaired = False

    def prepare(_):
        nonlocal repaired
        calls.append('prepare')
        if case == 'prepare-failure':
            raise RuntimeError('Authored provider preparation failure')
        repaired = case == 'repair'

    def verify():
        calls.append('verify')
        if not repaired:
            raise LinuxSetupError('Market source no longer matches its receipt')

    installation = SimpleNamespace(require_supported_mods=lambda: calls.append('supported'),
                                   verified_market_binary=verify, cdn_port=443)
    monkeypatch.setattr(LinuxInstallation, 'load', lambda _: installation)

    def start(name):
        calls.append('spawn-' + name)
        return SimpleNamespace(pid=501 if name == 'market' else 502, poll=lambda: None)

    worker = ServiceStartWorker(str(tmp_path), mode='authored-fixture', start_market=True, start_game=True,
        prepare_server_fn=prepare, start_market_fn=lambda _: start('market'),
        start_game_fn=lambda _, **__: start('game'), probe=lambda _: True, poll_interval_sec=0)
    results = []
    worker.completed.connect(results.append)
    worker.run()
    assert len(results) == 1
    if case == 'repair':
        assert results[0].market_ready and results[0].game_ready
        assert calls == ['supported', 'prepare', 'verify', 'spawn-market', 'spawn-game']
    else:
        assert not results[0].market_ready and not results[0].game_ready
        assert results[0].market_error and results[0].game_error
        assert calls == (['supported', 'prepare'] if case == 'prepare-failure'
                         else ['supported', 'prepare', 'verify'])
