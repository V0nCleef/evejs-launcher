"""Explicit source-bound Cargo provider for native Linux Market modifications."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import shlex
import tempfile
import tomllib

from .linux_installation import LinuxSetupError, file_sha256, read_json
from .linux_process import run_bounded


def _source_files(market):
    for path in market.rglob('*'):
        relative = path.relative_to(market)
        if not {'target', '.git'}.intersection(relative.parts) and path.is_file():
            if not path.resolve().is_relative_to(market):
                raise LinuxSetupError('Market build inputs contain an external symlink.')
            yield path


def input_digest(market, compiler_inputs=()):
    inputs = []
    # Runtime databases/configuration are not build inputs unless Cargo's
    # emitted dependency graph actually references them at compile time.
    for path in _source_files(market):
        relative = path.relative_to(market)
        code = path.suffix in ('.rs', '.c', '.h', '.cpp') or path.name in ('Cargo.toml', 'Cargo.lock', 'build.rs')
        runtime = relative.parts[0] in ('data', 'config', 'logs') or path.suffix in ('.db', '.sqlite', '.sqlite3', '.log')
        if code or not runtime:
            inputs.append(path)
    if (not isinstance(compiler_inputs, (tuple, list)) or len(compiler_inputs) > 10000
            or any(not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts for name in compiler_inputs)):
        raise LinuxSetupError('The native Market compiler input record is invalid.')
    for name in compiler_inputs:
        path = market / name
        if not path.is_file() or not path.resolve().is_relative_to(market):
            raise LinuxSetupError('A native Market compiler input is missing or external.')
        inputs.append(path)
    records = [(path.relative_to(market).as_posix(), file_sha256(path)) for path in sorted(set(inputs))]
    return hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()


def _compiler_inputs(market, target):
    dependency_file = target / 'release/market-server.d'
    with dependency_file.open('rb') as stream:
        content = stream.read(1024**2 + 1)
    if len(content) > 1024**2:
        raise LinuxSetupError('The native Market compiler dependency record is too large.')
    first = content.decode('utf-8').replace('\\\n', '').splitlines()[0]
    escaped = False
    separator = None
    for index, char in enumerate(first):
        if escaped:
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == ':':
            separator = index
            break
    if separator is None:
        raise LinuxSetupError('The native Market compiler dependency record is invalid.')
    result = set()
    external_roots = [target.resolve(), Path(os.environ.get('CARGO_HOME', Path.home() / '.cargo')).resolve(),
                      Path(os.environ.get('RUSTUP_HOME', Path.home() / '.rustup')).resolve()]
    for name in shlex.split(first[separator + 1:], posix=True):
        path = Path(name.replace('$$', '$'))
        path = (path if path.is_absolute() else market / path).resolve(strict=True)
        if path.is_relative_to(market):
            result.add(path.relative_to(market).as_posix())
        elif not any(path.is_relative_to(folder) for folder in external_roots):
            raise LinuxSetupError('The native Market compiler references an input outside the selected source tree.')
    if not result:
        raise LinuxSetupError('The native Market compiler dependency record has no selected sources.')
    return sorted(result)


def verified_source_build(installation, digest, cache):
    folder = cache / 'launcher-market-builds' / digest.lower()
    if not folder.exists() and not folder.is_symlink():
        return None
    if folder.is_symlink() or folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o022:
        raise LinuxSetupError('The native Market build directory has unsafe ownership or permissions.')
    receipt = read_json(folder / 'build.json', limit=1024**2)
    binary = folder / 'market-server'
    if (receipt.get('schema') != 'evejs-launcher-linux-market/2'
            or receipt.get('repo') != str(installation.root) or receipt.get('evejsVersion') != installation.version
            or receipt.get('sourceDigest') != digest
            or receipt.get('inputDigest') != input_digest(installation.root / 'externalservices/market-server', receipt.get('compilerInputs'))
            or receipt.get('binarySha256') != file_sha256(binary)):
        raise LinuxSetupError('The native Market build receipt does not match this installation and source. Rebuild it from Linux maintenance.')
    from src.updater.linux_package import validate_executable
    validate_executable(binary)
    return binary


def _owned_build(folder, installation):
    """Validate ownership before moving or pruning an old provider output."""
    if (folder.is_symlink() or not folder.is_dir() or folder.stat().st_uid != os.getuid()
            or folder.stat().st_mode & 0o022
            or set(path.name for path in folder.iterdir()) != {'market-server', 'build.json'}
            or any(path.is_symlink() or not path.is_file() for path in folder.iterdir())):
        raise LinuxSetupError('Preserve the native Market cache containing unknown files; inspect it before rebuilding.')
    receipt = read_json(folder / 'build.json', limit=1024**2)
    if receipt.get('schema') not in ('evejs-launcher-linux-market/1', 'evejs-launcher-linux-market/2') or receipt.get('repo') != str(installation.root):
        raise LinuxSetupError('The native Market cache does not belong to this installation.')


def _in_use(folder):
    for entry in Path('/proc').iterdir():
        try:
            if entry.name.isdigit() and entry.stat().st_uid == os.getuid() and (entry / 'exe').resolve().is_relative_to(folder):
                return True
        except OSError:
            pass
    return False


def _check_manifests(market):
    """A source receipt cannot attest path dependencies outside its source tree."""
    for manifest in [market / 'Cargo.toml', *sorted((market / 'crates').rglob('Cargo.toml'))]:
        if not manifest.is_file() or not manifest.resolve().is_relative_to(market):
            raise LinuxSetupError('Market contains an unavailable or external Cargo manifest.')
        data = tomllib.loads(manifest.read_text('utf-8'))
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == 'path' and isinstance(item, str):
                        target = (manifest.parent / item).resolve()
                        if not target.is_relative_to(market):
                            raise LinuxSetupError('Native Market builds require all Cargo path inputs inside the Market source tree.')
                    else:
                        walk(item)
            elif isinstance(value, list):
                for item in value: walk(item)
        walk(data)
    if (market / '.cargo').exists() or (market / '.cargo').is_symlink():
        raise LinuxSetupError('A custom Market Cargo configuration is outside the supported source receipt.')
    if not (market / 'Cargo.lock').is_file():
        raise LinuxSetupError('A reviewed Cargo.lock is required for a reproducible native Market build.')
    for path in market.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(market):
            raise LinuxSetupError('Market build inputs contain an external symlink.')


def build_market(installation):
    """Called only from stopped, root/client-locked maintenance; never during Start."""
    cargo = shutil.which('cargo')
    if not cargo:
        raise LinuxSetupError('Native source mods require Cargo and a Linux C toolchain. Install them first, or use the prepared stock Market runtime. SteamOS portable mode does not install system packages automatically.')
    market, digest, cache = installation.market_source_identity()
    _check_manifests(market)
    parent = cache / 'launcher-market-builds'
    if parent.is_symlink():
        raise LinuxSetupError('The native Market build cache must not be redirected.')
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o022:
        raise LinuxSetupError('The native Market build cache must belong to this user.')
    try:
        current = verified_source_build(installation, digest, cache)
    except (LinuxSetupError, ValueError, OSError):
        _owned_build(parent / digest.lower(), installation)
        current = None  # An explicit Build can repair its attributed corrupt output.
    if current is not None:
        return 'The verified native Market build already matches these sources.'
    inputs = input_digest(market)
    def stamp(path):
        info = path.stat()
        return info.st_ino, info.st_size, info.st_mtime_ns
    before = {path.resolve(): stamp(path) for path in _source_files(market)}
    environment = installation.server_environment()
    for name in ('RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'RUSTDOCFLAGS'):
        environment.pop(name, None)
    # One reusable compile cache; finalized runtime copies are keyed by source.
    target = cache / 'launcher-market-target'
    if target.is_symlink():
        raise LinuxSetupError('The native Market target directory must not be redirected.')
    target.mkdir(mode=0o700, exist_ok=True)
    output = run_bounded([cargo, 'build', '--locked', '--release', '--bin', 'market-server', '--target-dir', str(target)],
        cwd=market, env=environment, timeout=3600, limit=2 * 1024**2)
    if installation.market_source_identity()[1] != digest or input_digest(market) != inputs:
        raise LinuxSetupError('Market sources changed during compilation. The result was not adopted; build again after the source operation finishes.')
    compiler_inputs = _compiler_inputs(market, target)
    if any(before.get((market / name).resolve()) != stamp(market / name) for name in compiler_inputs):
        raise LinuxSetupError('Market compiler inputs changed during compilation. The result was not adopted.')
    inputs = input_digest(market, compiler_inputs)
    from src.updater.linux_package import validate_executable
    binary = target / 'release/market-server'
    validate_executable(binary)
    destination = parent / digest.lower()
    with tempfile.TemporaryDirectory(prefix='.build-', dir=parent) as temporary:
        stage = Path(temporary)
        shutil.copy2(binary, stage / 'market-server')
        receipt = {'schema': 'evejs-launcher-linux-market/2', 'repo': str(installation.root),
            'evejsVersion': installation.version, 'sourceDigest': digest, 'inputDigest': inputs,
            'compilerInputs': compiler_inputs,
            'binarySha256': file_sha256(stage / 'market-server')}
        (stage / 'build.json').write_text(json.dumps(receipt, indent=2) + '\n')
        previous = parent / '.previous'
        moved = False
        if destination.exists() or destination.is_symlink():
            _owned_build(destination, installation)
            if _in_use(destination):
                raise LinuxSetupError('The native Market executable is still running. Stop it before rebuilding.')
            if previous.exists() or previous.is_symlink():
                _owned_build(previous, installation)
                if _in_use(previous):
                    raise LinuxSetupError('A previous native Market build is still running.')
                shutil.rmtree(previous)
            os.rename(destination, previous)
            moved = True
        try:
            os.rename(stage, destination)
        except BaseException:
            if moved:
                os.rename(previous, destination)
            raise
    verified_source_build(installation, digest, cache)
    # Retain the current build and one useful previous source build. Running
    # executables and unattributed files remain protected.
    older = sorted((path for path in parent.iterdir() if path != destination and path.is_dir()),
                   key=lambda path: path.stat().st_mtime, reverse=True)
    for old in older[1:]:
        try:
            _owned_build(old, installation)
            if not _in_use(old):
                shutil.rmtree(old)
        except (LinuxSetupError, OSError):
            pass
    return output + '\nNative Linux Market build verified for the selected installation and sources.'
