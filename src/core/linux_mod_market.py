"""Verify a mod-owned native Market against its selected installation and sources."""
from pathlib import Path, PurePosixPath
import os
import re

from .linux_installation import LinuxSetupError, read_json, file_sha256

MARKET = 'externalservices/market-server/'
FILES = [MARKET + name for name in ('target/release/market-server', 'crates/market-common/src/lib.rs',
                                  'src/main.rs', 'src/rpc.rs', 'src/state.rs')]


def _ordinary(path, root, *, private=False, check_mode=True):
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True) != path or not path.is_relative_to(root):
        raise LinuxSetupError('The mod native Market receipt or runtime is missing or redirected.')
    info = path.stat()
    if info.st_uid != os.getuid() or (check_mode and info.st_mode & (0o077 if private else 0o022)):
        raise LinuxSetupError('The mod native Market receipt or runtime has unsafe ownership or permissions.')


def verified_mod_market(installation):
    root = installation.root
    folder = root / '_local/npc-market-buyers/install'
    if any((folder / name).exists() or (folder / name).is_symlink()
           for name in ('pending.json', 'active-update.json')):
        raise LinuxSetupError('Recover the unfinished NPC Market Buyers transaction before starting services.')
    receipt_file = folder / 'receipt.json'
    if not receipt_file.exists() and not receipt_file.is_symlink():
        return None
    _ordinary(receipt_file, root, check_mode=False)
    receipt = read_json(receipt_file)
    if receipt.get('state') == 'restored':
        return None
    _ordinary(receipt_file, root, private=True)
    runtime = receipt.get('linuxRuntime')
    records = receipt.get('files')
    if (receipt.get('schema') != 3 or receipt.get('state') != 'active'
            or receipt.get('nativeHost') != 'linux-x64' or not isinstance(runtime, dict)
            or runtime.get('schema') != 'evejs-native-linux-market/1' or runtime.get('architecture') != 'x64'
            or runtime.get('binary') != FILES[0] or runtime.get('evejsVersion') != installation.version
            or not isinstance(records, list) or len(records) != len(FILES)):
        raise LinuxSetupError('NPC Market Buyers needs a native Linux installation matching this EveJS version.')
    from .mod_manifest import scan_mods
    owners = [mod for mod in scan_mods(root) if mod.id == 'npc-market-buyers' and mod.active]
    if len(owners) != 1 or not owners[0].valid or owners[0].version != receipt.get('version'):
        raise LinuxSetupError('The native Market receipt does not match one enabled NPC Market Buyers package.')
    for name, record in zip(FILES, records):
        if not isinstance(record, dict) or record.get('target') != name:
            raise LinuxSetupError('The mod native Market receipt has invalid paths.')
        target = root / name
        _ordinary(target, root)
        if file_sha256(target) != record.get('installedSHA'):
            raise LinuxSetupError('NPC Market Buyers native files changed. Use its Install / Update or Recover action.')
    market, digest, _ = installation.market_source_identity()
    if runtime.get('sourceDigest') != digest:
        raise LinuxSetupError('The mod native Market build no longer matches these sources.')
    inputs = runtime.get('compilerInputs')
    required = ['Cargo.toml', 'Cargo.lock', *[name.removeprefix(MARKET) for name in FILES[1:]]]
    if (not isinstance(inputs, dict) or len(inputs) > 10000 or not set(required).issubset(inputs)):
        raise LinuxSetupError('The mod native Market compiler input receipt is incomplete.')
    for name, expected in inputs.items():
        relative = PurePosixPath(name)
        if (not name or relative.is_absolute() or any(part in ('', '.', '..') for part in name.split('/'))
                or '\\' in name or any(ord(char) < 32 or ord(char) == 127 for char in name)
                or not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected)):
            raise LinuxSetupError('The mod native Market compiler input receipt is invalid.')
        target = market.joinpath(*relative.parts)
        _ordinary(target, root, check_mode=False)
        if file_sha256(target) != expected:
            raise LinuxSetupError('A mod native Market compiler input changed after installation.')
    binary = root / FILES[0]
    if runtime.get('binarySHA256') != records[0].get('installedSHA'):
        raise LinuxSetupError('The mod native Market executable identity is inconsistent.')
    from src.updater.linux_package import validate_executable
    validate_executable(binary)
    return binary
