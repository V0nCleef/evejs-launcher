"""Recoverable conversion to Linux Setup's exact unprivileged CDN recipe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from .linux_installation import LinuxInstallation, LinuxSetupError, file_sha256, read_json, read_setup_config

JOURNAL = Path('_local/launcher-linux/cdn-profile')
_FILES = ('setup.conf', 'exefile.exe', 'launchdarkly_stackless_client_sdk.pyd')


def _folder(root: Path) -> Path:
    folder = root
    for part in JOURNAL.parts:
        folder = folder / part
        if folder.is_symlink():
            raise LinuxSetupError('The CDN recovery directory must not be redirected.')
        folder.mkdir(exist_ok=True)
    return folder


def _atomic(path: Path, data: bytes, mode: int):
    if path.is_symlink() or path.parent.is_symlink():
        raise LinuxSetupError('The CDN profile file must not be redirected.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        if os.name == 'posix':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_manifest(folder, manifest):
    _atomic(folder / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode(), 0o600)


def recover_cdn_profile(root: Path) -> str:
    root = root.resolve(strict=True)
    folder = _folder(root)
    manifest = read_json(folder / 'manifest.json', limit=16384)
    values = read_setup_config(root / '.evejs-linux.conf')
    client = Path(values.get('TQ_PATH', '')).resolve(strict=True)
    if (manifest.get('schema') != 1 or manifest.get('root') != str(root)
            or manifest.get('client') != str(client) or manifest.get('phase') != 'prepared'
            or set(manifest.get('files', {})) != set(_FILES)):
        raise LinuxSetupError('No matching interrupted CDN profile transaction is available.')
    targets = (root / '.evejs-linux.conf', client / 'bin64/exefile.exe', client / 'bin64/launchdarkly_stackless_client_sdk.pyd')
    # Validate all preimages before restoring any file.
    for name in _FILES:
        path = folder / name
        info = manifest['files'][name]
        if path.is_symlink() or not path.is_file() or file_sha256(path) != info.get('sha256'):
            raise LinuxSetupError('The CDN rollback files are incomplete or changed. They were preserved for repair.')
        if type(info.get('mode')) is not int or not 0 <= info['mode'] <= 0o777:
            raise LinuxSetupError('Invalid CDN rollback file mode.')
    for name, target in zip(_FILES, targets):
        _atomic(target, (folder / name).read_bytes(), manifest['files'][name]['mode'])
    manifest['phase'] = 'rolled-back'
    _write_manifest(folder, manifest)
    return 'Restored the previous CDN profile and its exact client binaries.'


def prepare_unprivileged_cdn(root: Path) -> str:
    from .linux_maintenance import SETUP_TOOL_HASHES
    from .linux_process import run_bounded
    installation = LinuxInstallation.load(root)
    root = installation.root
    helper = root / '.evejs-linux-tools/evejs-linux-client-preflight.py'
    if helper.is_symlink() or not helper.is_file() or file_sha256(helper) != SETUP_TOOL_HASHES[helper.name]:
        raise LinuxSetupError('The verified Linux Setup v1.19 client preflight helper is required.')
    python = shutil.which('python3')
    if python is None:
        raise LinuxSetupError('Linux client preparation requires python3.')
    command = [python, str(helper), 'check', '--repo', str(root), '--tq', str(installation.client),
               '--build', str(installation.build), '--cdn-port', '8443']
    if installation.cdn_port == 8443:
        run_bounded(command, cwd=root, timeout=120)
        return 'The unprivileged CDN profile on port 8443 is already prepared.'
    folder = _folder(root)
    if (folder / 'manifest.json').exists():
        previous = read_json(folder / 'manifest.json', limit=16384)
        if previous.get('phase') not in ('applied', 'rolled-back') or previous.get('root') != str(root):
            raise LinuxSetupError('Recover the interrupted CDN profile transaction before preparing another profile.')
    targets = (root / '.evejs-linux.conf', installation.client / 'bin64/exefile.exe',
               installation.client / 'bin64/launchdarkly_stackless_client_sdk.pyd')
    manifest = {'schema': 1, 'root': str(root), 'client': str(installation.client), 'phase': 'prepared', 'files': {}}
    for name, target in zip(_FILES, targets):
        if target.is_symlink() or target.parent.is_symlink() or not target.is_file() or target.stat().st_size > 32 * 1024**2:
            raise LinuxSetupError('The CDN profile requires ordinary client binaries and configuration files.')
        content = target.read_bytes()
        _atomic(folder / name, content, 0o600)
        manifest['files'][name] = {'sha256': file_sha256(folder / name), 'mode': stat.S_IMODE(target.stat().st_mode) & 0o777}
    # Nothing is mutated until every rollback byte and its manifest are durable.
    _write_manifest(folder, manifest)
    try:
        command[2] = 'apply'
        output = run_bounded(command, cwd=root, timeout=120)
        original = (folder / 'setup.conf').read_text()
        lines = [line for line in original.splitlines() if not line.startswith('EVEJS_CDN_PORT=')]
        _atomic(root / '.evejs-linux.conf', ('\n'.join([*lines, 'EVEJS_CDN_PORT=8443']) + '\n').encode(),
                manifest['files']['setup.conf']['mode'])
        command[2] = 'check'
        run_bounded(command, cwd=root, timeout=120)
    except Exception as exc:
        try:
            recover_cdn_profile(root)
        except Exception as recovery:
            raise LinuxSetupError('CDN preparation failed. Use Recover CDN profile before starting EveJS; the rollback files were retained.') from recovery
        raise LinuxSetupError('CDN preparation failed; the previous configuration and client binaries were restored. ' + str(exc)) from exc
    manifest['phase'] = 'applied'
    _write_manifest(folder, manifest)
    return 'Prepared localhost HTTPS on port 8443 without changing host port policy.\n\n' + output
