"""Read-only actual binding inspection rejects wide or drifting publications."""
import json

import pytest

from test_docker_autologin import _fixture, _FULL_ID, _RUNTIME_ID
from src.core.runtime.docker_compose import ComposeInspector, ComposeValidationError


def inspector(tmp_path, monkeypatch, payload):
    target, _, report, _, _ = _fixture(tmp_path)
    value = ComposeInspector(None)
    pins = [(_FULL_ID, _RUNTIME_ID), (_FULL_ID, _RUNTIME_ID)]
    monkeypatch.setattr(value, 'container_runtime_id_and_identity', lambda *args: pins.pop(0))
    commands = []
    def parsed(args, target, *, parser):
        value._assert_read_only(args, target)
        commands.append(args)
        return parser(json.dumps(payload))
    monkeypatch.setattr(value, '_run_parsed', parsed)
    return value, target, report.records['server'], commands, pins


def test_bounded_port_probe_uses_only_exact_read_only_inspect(tmp_path, monkeypatch):
    value, target, record, commands, _ = inspector(tmp_path, monkeypatch,
        {'443/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8443'}], '40111/tcp': None})
    result = value.container_tcp_publications(target, record, _FULL_ID)
    assert [(port.port, port.target) for port in result] == [(8443, 443)]
    assert commands == [('inspect', '--type', 'container', '--format', '{{json .NetworkSettings.Ports}}', _FULL_ID)]
    with pytest.raises(RuntimeError):
        value._assert_read_only(('inspect', '--type', 'container', '--format', '{{json .Config.Env}}', _FULL_ID), target)


@pytest.mark.parametrize('host,port', [('0.0.0.0', '8443'), ('127.0.0.1', '0'), ('127.0.0.1', '99999')])
def test_non_loopback_or_invalid_binding_is_rejected(tmp_path, monkeypatch, host, port):
    value, target, record, _, _ = inspector(tmp_path, monkeypatch,
        {'443/tcp': [{'HostIp': host, 'HostPort': port}]})
    with pytest.raises(ComposeValidationError):
        value.container_tcp_publications(target, record, _FULL_ID)


def test_runtime_replacement_during_binding_probe_is_rejected(tmp_path, monkeypatch):
    value, target, record, _, pins = inspector(tmp_path, monkeypatch, {})
    pins[-1] = (_FULL_ID, 'c' * 64)
    with pytest.raises(ComposeValidationError, match='changed'):
        value.container_tcp_publications(target, record, _FULL_ID)
