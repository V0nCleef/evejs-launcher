"""Opt-in installation coordination, separate from advisory delivery badges.

An installed helper can enroll its release family for verify/install/verify before
profile preparation. Exact older versions are explicitly declared by its author;
unrelated mods and undisclosed versions never acquire this behavior.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .. import config

FEATURE = 'client-preparation-v1'


def validate_policy(value):
    if (not isinstance(value, dict) or set(value) != {'mode', 'legacyVersions'}
            or value['mode'] != 'verify-install' or not isinstance(value['legacyVersions'], list)
            or len(value['legacyVersions']) > 32):
        raise ValueError('Invalid client preparation policy.')
    versions = value['legacyVersions']
    if (any(not isinstance(v, str) or not re.fullmatch(r'\d{1,6}\.\d{1,6}\.\d{1,6}', v) for v in versions)
            or len(set(versions)) != len(versions)):
        raise ValueError('Client preparation requires distinct exact legacy versions.')
    return tuple(versions)


def _scope(descriptor, client):
    if client is None or descriptor.updates is None:
        return None
    return {'client': os.path.normcase(str(Path(client).resolve(strict=True))),
            'modId': descriptor.id, 'repository': descriptor.updates.repository.casefold(),
            'asset': descriptor.updates.asset, 'tagPrefix': descriptor.updates.tag_prefix}


def _path(scope):
    key = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    return config.CONFIG_DIR/'mod-client-preparation'/(key+'.json')


def _load(path, scope):
    if not path.exists():
        return set()
    if path.stat().st_size > 16384:
        raise ValueError('Client preparation record is too large; preserve it for recovery.')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schemaVersion') != 1 or data.get('scope') != scope:
        raise ValueError('Client preparation record does not match this client and mod.')
    return set(validate_policy({'mode': 'verify-install', 'legacyVersions': data.get('versions')}))


def enrolled(descriptor, client):
    scope = _scope(descriptor, client)
    if scope is None:
        return False
    return descriptor.version in _load(_path(scope), scope)


def record_preparation(result):
    policy = getattr(result, 'client_preparation', None)
    if policy is None or result.action not in {'install', 'recover'} or not result.success or result.state != 'ready':
        return
    scope = _scope(result.descriptor, result.context.client_root)
    if scope is None:
        raise ValueError('Automatic client preparation requires a stable update source and physical client.')
    path = _path(scope)
    versions = _load(path, scope) | set(policy) | {result.descriptor.version}
    validate_policy({'mode':'verify-install', 'legacyVersions':list(versions)})
    document = {'schemaVersion':1, 'scope':scope, 'versions':sorted(versions)}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='preparation-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
