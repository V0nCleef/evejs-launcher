"""Private, authenticated, one-use transfer of Linux service supervisors."""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import uuid

from .linux_process import start_ticks
from .linux_supervisor import SupervisedServiceProcess

_LIMIT = 2 * 1024 * 1024


def _key(create: bool) -> bytes:
    from .. import config
    directory = config.CONFIG_DIR / 'update-runtime'
    if directory.is_symlink():
        raise ValueError('The Linux update checkpoint directory must not be redirected.')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError('The Linux update checkpoint directory must be private.')
    path = directory / '.authentication-key'
    if create and not path.exists():
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        key = stream.read(33)
    if info.st_uid != os.getuid() or info.st_mode & 0o077 or len(key) != 32:
        raise ValueError('The Linux update authentication key is unavailable.')
    return key


def _protect(payload: bytes) -> bytes:
    digest = hmac.new(_key(True), payload, hashlib.sha256).hexdigest()
    return json.dumps({'payload': base64.b64encode(payload).decode(), 'hmac': digest}, separators=(',', ':')).encode()


def _unprotect(content: bytes) -> dict:
    envelope = json.loads(content)
    payload = base64.b64decode(envelope['payload'], validate=True)
    expected = hmac.new(_key(False), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, envelope['hmac']):
        raise ValueError('The Linux update checkpoint changed.')
    return json.loads(payload)


def _identity(pid: int) -> dict:
    folder = Path(f'/proc/{pid}')
    started = start_ticks(pid)
    if started is None or folder.stat().st_uid != os.getuid():
        raise ValueError('The Linux launcher process identity is unavailable.')
    return {'pid': pid, 'started': started, 'uid': os.getuid(),
            'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'image': os.readlink(folder / 'exe')}


def _running(identity: dict) -> bool:
    try:
        return _identity(identity['pid']) == identity
    except (OSError, ValueError):
        return False


def prepare(owner, executable):
    from .runtime_handoff import HandoffTicket, _canonical, _context, _native_receipt, checkpoint_path
    state = vars(owner)
    cfg = state.get('_cfg', {})
    tracker = state.get('_tracker')
    records = []
    for kind, field in (('game', '_server_proc'), ('market', '_market_proc')):
        process = state.get(field)
        if process is None or process.poll() is not None:
            continue
        if (not isinstance(process, SupervisedServiceProcess) or cfg.get('runtime_backend', 'native') != 'native'
                or not cfg.get('evejs_root')):
            raise ValueError('This Linux service has no transferable supervisor.')
        process._query('status')
        if process.poll() is not None:
            continue
        internal = Path(executable).resolve().parent / '_internal'
        if Path(process.descriptor['image']).is_relative_to(internal):
            raise ValueError('The Linux service depends on replaceable launcher files.')
        records.append({'kind': kind, 'supervisor': process.descriptor,
                        'receipt': _native_receipt(process) if kind == 'game' else None})
    from .linux_client_identity import capture_client
    for client in (tracker.running if tracker is not None else ()):
        alias = getattr(client.process, 'settings_alias', None)
        records.append({'kind': 'client', 'client': capture_client(client.process),
            'username': client.username, 'character': client.character_name,
            'started': client.started_at.isoformat(), 'seen_window': client.has_seen_window,
            'alias': [str(path) for path in alias] if alias is not None else None})
    if not records:
        return None
    if len(records) > 258:
        raise ValueError('Too many Linux runtime records.')
    payload = {'schema': 1, 'time': time.time(), 'executable': _canonical(executable),
               'context': _context(cfg), 'parent': _identity(os.getpid()), 'records': records}
    content = _protect(json.dumps(payload, separators=(',', ':')).encode())
    if len(content) > _LIMIT:
        raise ValueError('The Linux update checkpoint is too large.')
    path = checkpoint_path(executable)
    descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic no-replace publication. POSIX rename would overwrite a second
        # launcher's in-flight ticket, unlike the Windows implementation.
        os.link(temporary, path, follow_symlinks=False)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return HandoffTicket(path, hashlib.sha256(content).hexdigest())


def resume(owner, executable):
    from .runtime_handoff import ResumeResult, _canonical, _context, _restore_native_evidence, checkpoint_path, log
    path = checkpoint_path(executable)
    claimed = None
    try:
        if not path.exists():
            return ResumeResult()
        if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise ValueError('The Linux update checkpoint is not private.')
        with path.open('rb') as stream:
            content = stream.read(_LIMIT + 1)
        if len(content) > _LIMIT:
            raise ValueError('The Linux update checkpoint is oversized.')
        data = _unprotect(content)
        if data['schema'] != 1 or data['executable'] != _canonical(executable):
            raise ValueError('The Linux checkpoint names another launcher.')
        if _running(data['parent']):
            return ResumeResult()
        claimed = path.with_suffix('.claim-' + uuid.uuid4().hex)
        os.rename(path, claimed)
        if claimed.read_bytes() != content:
            raise ValueError('The Linux update checkpoint changed during its claim.')
        if not 0 <= time.time() - data['time'] <= 3600 or data['context'] != _context(vars(owner).get('_cfg', {})):
            raise ValueError('The Linux checkpoint expired or the installation changed.')
        records = data['records']
        if (not isinstance(records, list) or not 1 <= len(records) <= 258
                or any(not isinstance(record, dict) or record.get('kind') not in ('game', 'market', 'client') for record in records)):
            raise ValueError('Invalid Linux supervisor records.')
        services = [record['kind'] for record in records if record['kind'] != 'client']
        if len(set(services)) != len(services):
            raise ValueError('Duplicate Linux service records.')
        seen_pids = set()
        for record in records:
            if record['kind'] == 'client':
                for name in ('username', 'character', 'started'):
                    if not isinstance(record.get(name), str) or len(record[name]) > 1024:
                        raise ValueError('Invalid Linux client metadata.')
                datetime.fromisoformat(record['started'])
                if type(record.get('seen_window')) is not bool:
                    raise ValueError('Invalid Linux client window metadata.')
                pins = record['client']['processes']
                if not isinstance(pins, list):
                    raise ValueError('Invalid Linux client identities.')
                pids = [pin['pid'] for pin in pins]
            else:
                pids = [record['supervisor']['pid']]
            for pid in pids:
                if type(pid) is not int or pid <= 1 or pid in seen_pids:
                    raise ValueError('Duplicate or invalid Linux runtime PID.')
                seen_pids.add(pid)
        result = {'game': False, 'market': False, 'clients': 0, 'skipped': 0}
        for record in records:
            kind = record['kind']
            if kind == 'client':
                from .linux_client_identity import PinnedClientProcess
                started = datetime.fromisoformat(record['started'])
                if owner._tracker.is_account_running(record['username']):
                    result['skipped'] += 1
                    continue
                try:
                    process = PinnedClientProcess(record['client'])
                except (OSError, ValueError):
                    result['skipped'] += 1
                    continue
                alias = record.get('alias')
                if isinstance(alias, list) and len(alias) == 2 and all(isinstance(path, str) for path in alias):
                    from .linux_installation import LinuxInstallation
                    from .linux_client import settings_base
                    import re
                    try:
                        path, target = map(Path, alias)
                        cfg = vars(owner)['_cfg']
                        root = (cfg.get('linux_setup_root') or cfg['evejs_root']) if cfg.get('runtime_backend') == 'docker_compose' else cfg['evejs_root']
                        installation = LinuxInstallation.load(root)
                        if (path.parent == settings_base(installation)
                                and re.fullmatch(r'settings_EveJSLauncher_run_[a-f0-9]{32}', path.name)
                                and path.is_symlink() and path.resolve() == target):
                            process.settings_alias = (path, target)
                    except (OSError, ValueError):
                        pass  # Preserve an alias whose installation is unavailable.
                client = owner._tracker.add(record['username'], record['character'], process)
                client.started_at = started
                client.has_seen_window = record['seen_window']
                result['clients'] += 1
                continue
            field = '_server_proc' if kind == 'game' else '_market_proc'
            if vars(owner).get(field) is not None or vars(owner)['_cfg'].get('runtime_backend', 'native') != 'native':
                result['skipped'] += 1
                continue
            try:
                process = SupervisedServiceProcess(record['supervisor'])
                if process.poll() is not None:
                    process.release()
                    result['skipped'] += 1
                    continue
            except (OSError, ValueError):
                result['skipped'] += 1
                continue
            from .linux_process import _OWNED
            _OWNED[process.pid] = process
            setattr(owner, field, process)
            result[kind] = True
            if kind == 'game' and record.get('receipt') is not None:
                try:
                    _restore_native_evidence(owner, process, record['receipt'])
                except (OSError, ValueError, KeyError, TypeError):
                    log.warning('Recovered Linux service without optional mod evidence')
        return ResumeResult(**result)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Invalid data is never an ownership grant. Preserve the original ticket
        # for diagnosis when it could not be authenticated/claimed.
        log.warning('The Linux update runtime checkpoint was not accepted')
        return ResumeResult()
    finally:
        if claimed is not None:
            claimed.unlink(missing_ok=True)
