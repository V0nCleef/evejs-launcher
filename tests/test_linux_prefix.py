"""A Proton selection must not silently rebuild a newer or untracked prefix."""
import sys

import pytest

from test_linux_installation import linux_installation
from src.core import linux_client
from src.core.linux_installation import LinuxSetupError

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Proton Linux prefix mappings')


@pytest.mark.parametrize('existing,selected', [
    ('10.0-100', '9.0-203'),
    ('9.0-203', '9.0-100'),
    ('unknown-format', '9.0-203'),
    ('9.0-203', None),
])
def test_newer_or_unknown_prefix_is_preserved(linux_installation, monkeypatch, existing, selected):
    installation = linux_installation
    installation.proton.write_text('CURRENT_PREFIX_VERSION = ' + repr(selected) if selected else '# unknown wrapper')
    (installation.prefix / 'version').write_text(existing)
    registry = installation.prefix / 'pfx/drive_c/user.reg'
    registry.parent.mkdir(parents=True)
    registry.write_bytes(b'fixture persistent prefix data')
    monkeypatch.setattr(linux_client, 'run_bounded', lambda *args, **kwargs: pytest.fail('Proton must not run'))
    with pytest.raises(LinuxSetupError, match='newer/unknown'):
        linux_client.require_prefix(installation)
    assert registry.read_bytes() == b'fixture persistent prefix data'
    assert (installation.prefix / 'version').read_text() == existing


def test_existing_unversioned_prefix_is_never_initialized_in_place(linux_installation, monkeypatch):
    (linux_installation.prefix / 'pfx/drive_c').mkdir(parents=True)
    monkeypatch.setattr(linux_client, 'run_bounded', lambda *args, **kwargs: pytest.fail('Proton must not run'))
    with pytest.raises(LinuxSetupError, match='no Proton version record'):
        linux_client.require_prefix(linux_installation)


@pytest.mark.parametrize('existing,selected', [('9.0-203', '9.0-203'), ('9.0-203', '10.0-100')])
def test_compatible_prefix_keeps_the_selected_mapping(linux_installation, monkeypatch, existing, selected):
    installation = linux_installation
    installation.proton.write_text('CURRENT_PREFIX_VERSION = ' + repr(selected))
    (installation.prefix / 'version').write_text(existing)
    mapping = installation.prefix / 'pfx/dosdevices/z:'
    mapping.parent.mkdir(parents=True)
    mapping.symlink_to('/')
    monkeypatch.setattr(linux_client, 'run_bounded', lambda *args, **kwargs: pytest.fail('Prepared mapping must be reused'))
    linux_client.require_prefix(installation)
    assert mapping.is_symlink()
