"""Explicit prefix attribution for generic profile/settings consumers."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path

from .linux_installation import LinuxInstallation, LinuxSetupError, read_json

_CURRENT = ContextVar('evejs_linux_installation', default=None)
_SCOPE = ContextVar('evejs_linux_settings_scope', default='')


@contextmanager
def bind_installation(installation: LinuxInstallation, *, settings_scope: str = ""):
    token = _CURRENT.set(installation)
    scope_token = _SCOPE.set(settings_scope)
    try:
        yield
    finally:
        _SCOPE.reset(scope_token)
        _CURRENT.reset(token)


def installation_for_profile(path: Path) -> LinuxInstallation:
    current = _CURRENT.get()
    if current is not None:
        return current
    marker = path.parent / '.evejs-linux-profile.json'
    data = read_json(marker, limit=8192)
    if data.get('schemaVersion') != 1 or data.get('username') != path.parent.name:
        raise LinuxSetupError('The Linux profile binding could not be verified.')
    installation = LinuxInstallation.load(data.get('evejsRoot', ''))
    if path.resolve(strict=True) != installation.client:
        raise LinuxSetupError('The Linux profile points to a different copied client.')
    return installation


def enroll_profile(profile: Path, installation: LinuxInstallation, username: str, *, settings_scope: str = ""):
    from .profiles import _atomic_write_text
    marker = profile / '.evejs-linux-profile.json'
    expected = {'schemaVersion': 1, 'username': username, 'evejsRoot': str(installation.root)}
    if settings_scope:
        expected['settingsScope'] = settings_scope
    if marker.is_symlink():
        raise LinuxSetupError('The Linux profile binding must not be redirected.')
    if marker.exists() and read_json(marker, limit=8192) != expected:
        raise LinuxSetupError('The Linux account profile belongs to another installation.')
    _atomic_write_text(marker, json.dumps(expected, ensure_ascii=False) + '\n')


def account_settings_path(path: Path) -> Path:
    from .linux_client import settings_base, account_profile_name
    installation = installation_for_profile(path)
    if path.absolute() == installation.client:
        return settings_base(installation) / 'settings'
    scope = _SCOPE.get()
    if _CURRENT.get() is None:
        scope = read_json(path.parent / '.evejs-linux-profile.json', limit=8192).get('settingsScope', '')
        if not isinstance(scope, str) or len(scope) > 1024:
            raise LinuxSetupError('The Linux settings scope is invalid.')
    return settings_base(installation) / ('settings_' + account_profile_name(installation, path.parent.name, scope))


def named_settings_alias(path: Path, profile_name: str, host: str) -> Path:
    from .linux_client import settings_base
    installation = installation_for_profile(path)
    return settings_base(installation, host) / ('settings_' + profile_name)
