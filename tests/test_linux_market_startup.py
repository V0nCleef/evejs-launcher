"""Native provider repair must precede source-bound executable verification."""
import ssl
from types import SimpleNamespace

import pytest

from src.core.linux_installation import LinuxInstallation, LinuxSetupError
from src.workers.server_worker import ServiceStartWorker


@pytest.mark.parametrize('case', ['repair', 'prepare-failure', 'unrepaired'])
def test_provider_prepares_before_final_binary_verification(qapp, tmp_path, monkeypatch, case):
    monkeypatch.setattr('src.core.host.IS_LINUX', True)
    monkeypatch.setattr('src.core.linux_client.require_backend_tls', lambda *_, **kwargs: None)
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


@pytest.mark.parametrize('scenario', ['slow', 'exits', 'times-out', 'wrong-ca'])
def test_linux_start_waits_for_delayed_tls_without_accepting_failure(qapp, monkeypatch, scenario):
    monkeypatch.setattr('src.core.host.IS_LINUX', True)
    installation = SimpleNamespace(require_supported_mods=lambda: None,
                                   verified_market_binary=lambda: None, cdn_port=443)
    monkeypatch.setattr(LinuxInstallation, 'load', lambda _: installation)
    elapsed = 0.0
    probes = []

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    def tls(actual, port, *, deadline, clock):
        assert actual is installation and port == 26000
        assert deadline == 45 and clock() == elapsed and elapsed < deadline
        probes.append(elapsed)
        if scenario == 'slow' and len(probes) == 3:
            return
        if scenario == 'wrong-ca':
            raise LinuxSetupError('certificate does not match') from ssl.SSLCertVerificationError()
        raise LinuxSetupError('TLS listener is still starting')

    monkeypatch.setattr('src.core.linux_client.require_backend_tls', tls)
    process = SimpleNamespace(pid=502, poll=lambda: 17 if scenario == 'exits' and elapsed else None)
    worker = ServiceStartWorker(
        'fixture', mode='vanilla', start_market=False, start_game=True,
        start_game_fn=lambda *_, **__: process, probe=lambda _: True,
        sleep_fn=sleep, clock_fn=lambda: elapsed,
    )
    results = []
    worker.completed.connect(results.append)
    worker.run()
    [result] = results
    assert result.game_process is process
    assert result.game_ready == (scenario == 'slow')
    assert (elapsed, result.game_error) == {
        'slow': (2, None),
        'exits': (1, 'Game service exited before readiness (code 17).'),
        'times-out': (45, 'TLS listener is still starting'),
        'wrong-ca': (0, 'certificate does not match'),
    }[scenario]
