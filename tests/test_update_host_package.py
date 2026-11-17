"""An OS must never select another host's executable archive."""
import pytest

from src.updater.checker import release_package_name


@pytest.mark.parametrize('host,machine,expected', [
    ('win32', 'AMD64', 'EveJS-Launcher-V1.zip'),
    ('linux', 'x86_64', 'EveJS-Launcher-Linux-x86_64.tar.gz'),
    ('linux', 'amd64', 'EveJS-Launcher-Linux-x86_64.tar.gz'),
    ('linux', 'aarch64', ''),
    ('darwin', 'arm64', ''),
])
def test_update_package_is_exact_for_the_host(host, machine, expected):
    assert release_package_name(host, machine) == expected
