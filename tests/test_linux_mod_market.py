"""Mod-native Market selection is source-bound and cannot silently use stock code."""
import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

import pytest

from src.core.linux_installation import LinuxInstallation, LinuxSetupError
from src.core.linux_mod_market import FILES, verified_mod_market

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux executable modes and ownership')


@pytest.fixture
def provider(tmp_path):
    root = tmp_path / 'world'
    mod = root / 'mods/buyers'
    mod.mkdir(parents=True)
    (mod / 'loader.js').write_text('// enabled fixture')
    (mod / 'helper.js').write_text('// never executed')
    (mod / 'evejs-launcher.mod.json').write_text(json.dumps({
        'schemaVersion': 3, 'id': 'npc-market-buyers', 'displayName': 'NPC Market Buyers',
        'version': '0.1.26', 'kind': 'loader', 'supportedBackends': ['native'],
        'activation': {'strategy': 'loader_rename'}, 'restart': 'game_server',
        'launcherApi': {'version': 1, 'helper': {'runtime': 'node', 'path': 'helper.js'},
                        'capabilities': ['verify', 'prepare_server_start']}}))
    header = bytearray(64)
    header[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', header, 18, 62)
    records = []
    for index, name in enumerate(FILES):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(header if index == 0 else b'fixture native source')
        target.chmod(0o755 if index == 0 else 0o644)
        records.append({'target': name, 'installedSHA': hashlib.sha256(target.read_bytes()).hexdigest()})
    market = root / 'externalservices/market-server'
    for name in ('Cargo.toml', 'Cargo.lock'):
        (market / name).write_text('fixture locked compiler input')
    installation = SimpleNamespace(root=root, version='261115.2')
    installation.market_source_identity = lambda: LinuxInstallation.market_source_identity(installation)
    inputs = {name: hashlib.sha256((market / name).read_bytes()).hexdigest()
              for name in ['Cargo.toml', 'Cargo.lock', *[n.removeprefix('externalservices/market-server/') for n in FILES[1:]]]}
    runtime = {'schema': 'evejs-native-linux-market/1', 'architecture': 'x64', 'evejsVersion': installation.version,
               'binary': FILES[0], 'binarySHA256': records[0]['installedSHA'],
               'sourceDigest': installation.market_source_identity()[1], 'compilerInputs': inputs}
    receipt = root / '_local/npc-market-buyers/install/receipt.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({'schema': 3, 'nativeHost': 'linux-x64', 'state': 'active',
                                  'version': '0.1.26', 'files': records, 'linuxRuntime': runtime}))
    receipt.chmod(0o600)
    return installation, receipt, root / FILES[0], mod


def test_mod_binary_is_selected_read_only_before_stock_cache(provider):
    installation, receipt, binary, _ = provider
    before = receipt.read_bytes()
    assert verified_mod_market(installation) == binary
    assert LinuxInstallation.verified_market_binary(installation) == binary
    assert receipt.read_bytes() == before


@pytest.mark.parametrize('change', ['binary', 'source', 'dependency', 'pending', 'disabled', 'permissions'])
def test_changed_or_unfinished_mod_runtime_fails_closed(provider, change):
    installation, receipt, binary, mod = provider
    if change == 'binary':
        binary.write_bytes(binary.read_bytes() + b'changed')
    elif change == 'source':
        (installation.root / FILES[2]).write_text('unknown source change')
    elif change == 'dependency':
        (installation.root / 'externalservices/market-server/Cargo.lock').write_text('changed dependency')
    elif change == 'pending':
        receipt.with_name('pending.json').write_text('{}')
    elif change == 'disabled':
        (mod / 'loader.js').rename(mod / 'loader.js.disabled')
    else:
        binary.chmod(0o775)
    with pytest.raises(LinuxSetupError):
        verified_mod_market(installation)


def test_restored_support_returns_to_normal_stock_selection(provider):
    installation, receipt, _, _ = provider
    value = json.loads(receipt.read_text())
    value['state'] = 'restored'
    receipt.write_text(json.dumps(value))
    assert verified_mod_market(installation) is None


def test_compiler_source_mode_does_not_override_recorded_bytes(provider):
    installation, _, _, _ = provider
    (installation.root / 'externalservices/market-server/Cargo.lock').chmod(0o664)
    assert verified_mod_market(installation) is not None
