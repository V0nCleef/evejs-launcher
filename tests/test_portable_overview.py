"""Portable Python 2 patching preserves original methods and string types."""
import base64
from dataclasses import replace
import json
from pathlib import Path
import struct

import pytest

from src.core import portable_overview as portable


def owned():
    payload = json.loads((Path(portable.__file__).parent / 'client/overview_bridge_py27.json').read_bytes())
    return portable.load_pyc(base64.b64decode(payload['bytecode']))


def fixture():
    module = owned()
    original = portable._one(module, b'OverviewBridge')
    original = replace(original, name=b'OverviewPresetSvc')
    module = replace(module, consts=tuple(original if isinstance(item, portable.Code) else item for item in module.consts))
    return portable.MAGIC + b'\0' * 4 + portable.dump(module)


def test_portable_graft_preserves_every_original_method_code_and_constant():
    original = fixture()
    output = portable.graft_overview(original)
    old = portable._one(portable.load_pyc(original), b'OverviewPresetSvc')
    new = portable._one(portable.load_pyc(output), b'OverviewPresetSvc')
    assert new.consts[:len(old.consts)] == old.consts
    assert new.code.startswith(old.code[:-2])
    assert b'_EveJSOriginalOverviewRun' in new.names
    assert output[:8] == original[:8]
    with pytest.raises(ValueError, match='already installed'):
        portable.graft_overview(output)


@pytest.mark.parametrize('value', [b'bytes\xff', 'Unicode \u96ea', portable.Long(1), portable.Long(0),
    2**100, -2**90, 42, None, True, 1.25, complex(2, 4), Ellipsis, StopIteration,
    (b'byte', 'text'), [1, 2], {b'key': 'value'}, {b'a', b'b'}, frozenset((1, 2))])
def test_marshal_two_preserves_values_and_distinct_python_two_types(value):
    reader = portable.Reader(portable.dump(value))
    result = reader.read()
    assert result == value
    if isinstance(value, portable.Long):
        assert type(result) is portable.Long
    elif type(value) not in (int,):
        assert type(result) is type(value)
    assert reader.offset == len(reader.content)


@pytest.mark.parametrize('content', [b'?', b'R' + struct.pack('<i', 7), b's' + struct.pack('<i', 999999),
    b'(' + struct.pack('<i', -1), b'l' + struct.pack('<i', 200000)])
def test_malformed_or_oversized_marshal_is_rejected(content):
    with pytest.raises(ValueError):
        portable.Reader(content).read()


def test_bridge_payload_is_launcher_owned_and_reproducible():
    from src.core.overview_patch import _BRIDGE_METHODS
    import hashlib
    source = ('class OverviewBridge:\n'
        '    def Run(self, *args, **kwargs):\n'
        '        result = self._EveJSOriginalOverviewRun(*args, **kwargs)\n'
        '        self._evejsOverviewBridgeDone = False\n'
        '        uthread2.StartTasklet(self._EveJSOverviewBridgeWaitForSession)\n'
        '        return result\n' + _BRIDGE_METHODS)
    payload = json.loads((Path(portable.__file__).parent / 'client/overview_bridge_py27.json').read_bytes())
    assert hashlib.sha256(source.encode()).hexdigest() == payload['sourceSHA256']
    bridge = portable._one(owned(), b'OverviewBridge')
    assert {item.name for item in bridge.consts if isinstance(item, portable.Code)} == {
        b'Run', b'_EveJSOverviewBridgeWaitForSession', b'_EveJSOverviewBridgeWriteAck', b'_EveJSOverviewBridgeTryRun'}
