"""Native Cargo builds are explicit, source-bound and never replace user data."""
import json
from pathlib import Path
import struct
import shlex
import shutil
import subprocess
import sys

import pytest

from test_linux_installation import linux_installation
from src.core import linux_market as market
from src.core.linux_installation import LinuxSetupError

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux binary modes and native provider')


@pytest.fixture
def sources(linux_installation, monkeypatch):
    root = linux_installation.root / 'externalservices/market-server'
    (root / 'src').mkdir(parents=True)
    (root / 'Cargo.toml').write_text('[package]\nname="market-server"\nversion="0.1.0"\nedition="2024"\n')
    (root / 'Cargo.lock').write_text('fixture locked dependencies')
    (root / 'src/main.rs').write_text('fn main() {}')
    (root / 'data').mkdir()
    (root / 'data/player-market.db').write_bytes(b'preserve database fixture')
    monkeypatch.setattr(market.shutil, 'which', lambda name: '/usr/bin/cargo')
    calls = []
    def compile(command, **kwargs):
        calls.append((command, kwargs))
        binary = Path(command[-1]) / 'release/market-server'
        binary.parent.mkdir(parents=True, exist_ok=True)
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', header, 18, 62)
        binary.write_bytes(header)
        binary.chmod(0o755)
        binary.with_suffix('.d').write_text('market-server: ' + shlex.quote(str(root / 'src/main.rs')) + '\n')
        return 'fixture build successful'
    monkeypatch.setattr(market, 'run_bounded', compile)
    return linux_installation, root, calls


def test_explicit_cargo_provider_attests_source_and_binary_preserving_data(sources):
    installation, root, calls = sources
    assert 'verified' in market.build_market(installation)
    binary = installation.verified_market_binary()
    assert binary.name == 'market-server'
    assert calls[0][0][1:7] == ['build', '--locked', '--release', '--bin', 'market-server', '--target-dir']
    assert calls[0][1]['cwd'] == root
    assert (root / 'data/player-market.db').read_bytes() == b'preserve database fixture'
    assert 'already matches' in market.build_market(installation)
    assert len(calls) == 1
    binary.write_bytes(b'changed executable')
    with pytest.raises(LinuxSetupError, match='receipt'):
        installation.verified_market_binary()
    assert 'verified' in market.build_market(installation)
    assert installation.verified_market_binary() == binary
    assert len(calls) == 2


def test_source_change_never_reuses_a_previous_build(sources):
    installation, root, _ = sources
    market.build_market(installation)
    (root / 'src/main.rs').write_text('fn main() {println!("changed")}')
    with pytest.raises(LinuxSetupError, match='Build Market from source'):
        installation.verified_market_binary()


def test_build_changed_inputs_are_never_adopted(sources, monkeypatch):
    installation, root, _ = sources
    original = market.run_bounded
    def compile(*args, **kwargs):
        result = original(*args, **kwargs)
        (root / 'src/main.rs').write_text('changed during build')
        return result
    monkeypatch.setattr(market, 'run_bounded', compile)
    with pytest.raises(LinuxSetupError, match='changed during compilation'):
        market.build_market(installation)
    _, _, cache = installation.market_source_identity()
    assert not list((cache / 'launcher-market-builds').glob('*/build.json'))


def test_external_path_dependency_is_rejected_before_compilation(sources):
    installation, root, calls = sources
    with (root / 'Cargo.toml').open('a') as stream:
        stream.write('[dependencies]\noutside={path="../../../outside"}\n')
    with pytest.raises(LinuxSetupError, match='inside the Market source tree'):
        market.build_market(installation)
    assert not calls


def test_missing_toolchain_does_not_fall_back_to_windows_executable(sources, monkeypatch):
    installation, root, calls = sources
    monkeypatch.setattr(market.shutil, 'which', lambda name: None)
    with pytest.raises(LinuxSetupError, match='Cargo'):
        market.build_market(installation)
    assert not calls


def test_real_cargo_embedded_root_and_runtime_directory_resources_are_attested(linux_installation):
    cargo = shutil.which('cargo')
    if not cargo:
        pytest.skip('real offline Cargo input regression requires Cargo')
    root = linux_installation.root / 'externalservices/market-server'
    (root / 'src').mkdir(parents=True)
    (root / 'config').mkdir()
    (root / 'data').mkdir()
    (root / 'Cargo.toml').write_text('[package]\nname="market-server"\nversion="0.1.0"\nedition="2024"\n')
    (root / 'message.txt').write_text('before')
    (root / 'config/compiled.txt').write_text('compiled')
    (root / 'config/runtime.toml').write_text('runtime=true')
    (root / 'data/market.sqlite').write_bytes(b'private runtime database')
    (root / 'src/main.rs').write_text('fn main(){println!("{}:{}",include_str!("../message.txt"),include_str!("../config/compiled.txt"));}')
    subprocess.run([cargo, 'generate-lockfile', '--offline'], cwd=root, check=True, capture_output=True)
    market.build_market(linux_installation)
    binary = linux_installation.verified_market_binary()
    assert subprocess.check_output([str(binary)], text=True).strip() == 'before:compiled'
    (root / 'data/market.sqlite').write_bytes(b'updated runtime database')
    (root / 'config/runtime.toml').write_text('runtime=false')
    assert linux_installation.verified_market_binary() == binary
    for resource, content, expected in [('message.txt', 'after', 'after:compiled'),
                                         ('config/compiled.txt', 'changed', 'after:changed')]:
        (root / resource).write_text(content)
        with pytest.raises(LinuxSetupError, match='receipt'):
            linux_installation.verified_market_binary()
        market.build_market(linux_installation)
        assert subprocess.check_output([str(linux_installation.verified_market_binary())], text=True).strip() == expected
