"""Reject archive escapes while preserving Linux Qt library symlinks/modes."""
import io
import os
import gzip
import struct
import sys
import tarfile

import pytest

from src.updater.linux_package import PACKAGE_DIRECTORY as ROOT, extract_linux_package

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux archive modes and symlinks')


def package(tmp_path, extras=()):
    archive = tmp_path / 'update.tar.gz'
    header = bytearray(64)
    header[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', header, 18, 62)
    with tarfile.open(archive, 'w:gz') as output:
        folder = tarfile.TarInfo(ROOT)
        folder.type = tarfile.DIRTYPE
        folder.mode = 0o755
        output.addfile(folder)
        for name, content, mode in [('evejs-launcher', bytes(header), 0o755),
                                    ('_internal/VERSION', b'9.8.7\n', 0o644),
                                    ('_internal/base_library.zip', b'fixture runtime', 0o644),
                                    ('_internal/libfixture.so.1', b'fixture library', 0o755)]:
            entry = tarfile.TarInfo(ROOT + '/' + name)
            entry.size = len(content)
            entry.mode = mode
            output.addfile(entry, io.BytesIO(content))
        for entry in extras:
            output.addfile(entry, io.BytesIO(b'x') if entry.isreg() else None)
    return archive


def test_contained_qt_style_symlink_and_file_mode_are_preserved(tmp_path):
    link = tarfile.TarInfo(ROOT + '/_internal/libfixture.so')
    link.type = tarfile.SYMTYPE
    link.linkname = 'libfixture.so.1'
    result = extract_linux_package(package(tmp_path, [link]), tmp_path / 'stage', expected_version='9.8.7')
    assert result.version == '9.8.7'
    assert (result.directory / '_internal/libfixture.so').is_symlink()
    assert (result.directory / '_internal/libfixture.so').read_bytes() == b'fixture library'
    assert (result.directory / 'evejs-launcher').stat().st_mode & 0o111


def test_publicly_writable_runtime_is_rejected(tmp_path):
    entry = tarfile.TarInfo(ROOT + '/_internal/unsafe-resource')
    entry.mode = 0o666
    entry.size = 1
    with pytest.raises(ValueError, match='writable by other users'):
        extract_linux_package(package(tmp_path, [entry]), tmp_path / 'stage')


@pytest.mark.parametrize('name,linkname,kind', [
    ('../outside', '', tarfile.REGTYPE),
    (ROOT + '/_internal/escape', '/etc/passwd', tarfile.SYMTYPE),
    (ROOT + '/_internal/escape', '../../outside', tarfile.SYMTYPE),
    (ROOT + '/_internal/hard', ROOT + '/evejs-launcher', tarfile.LNKTYPE),
    (ROOT + '/device', '', tarfile.CHRTYPE),
    (ROOT + '/evejs-launcher', '', tarfile.REGTYPE),
])
def test_unsafe_archive_entries_are_rejected_before_extraction(tmp_path, name, linkname, kind):
    entry = tarfile.TarInfo(name)
    entry.type = kind
    entry.linkname = linkname
    entry.size = 1 if kind == tarfile.REGTYPE else 0
    with pytest.raises(ValueError):
        extract_linux_package(package(tmp_path, [entry]), tmp_path / 'stage')
    assert not (tmp_path / 'stage').exists()


def test_release_version_mismatch_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='version does not match'):
        extract_linux_package(package(tmp_path), tmp_path / 'stage', expected_version='9.8.8')


def test_symlink_parent_cannot_receive_archive_files(tmp_path):
    parent = tarfile.TarInfo(ROOT + '/_internal/redirect')
    parent.type = tarfile.SYMTYPE
    parent.linkname = '../_internal'
    child = tarfile.TarInfo(ROOT + '/_internal/redirect/file')
    child.size = 1
    with pytest.raises(ValueError, match='linked or non-directory'):
        extract_linux_package(package(tmp_path, [parent, child]), tmp_path / 'stage')
    assert not (tmp_path / 'stage').exists()


def test_huge_pax_header_is_rejected_before_allocation(tmp_path):
    entry = tarfile.TarInfo('PaxHeader')
    entry.type = tarfile.XHDTYPE
    entry.size = 2**40
    archive = tmp_path / 'oversized-header.tar.gz'
    with gzip.open(archive, 'wb') as output:
        output.write(entry.tobuf(format=tarfile.GNU_FORMAT))
    with pytest.raises(ValueError, match='oversized tar metadata'):
        extract_linux_package(archive, tmp_path / 'stage')
    assert not (tmp_path / 'stage').exists()


@pytest.mark.parametrize('umask', [0o002, 0o022, 0o077])
def test_directory_modes_ignore_user_umask_and_include_implicit_parents(tmp_path, umask):
    resource = tarfile.TarInfo(ROOT + '/_internal/readonly/nested/resource')
    resource.size = 1
    readonly = tarfile.TarInfo(ROOT + '/_internal/readonly')
    readonly.type = tarfile.DIRTYPE
    readonly.mode = 0o550
    archive = package(tmp_path, [resource, readonly])
    previous = os.umask(umask)
    try:
        result = extract_linux_package(archive, tmp_path / 'stage')
    finally:
        os.umask(previous)
    expected = {
        tmp_path / 'stage': 0o700,
        result.directory: 0o755,
        result.directory / '_internal': 0o755,
        result.directory / '_internal/readonly': 0o550,
        result.directory / '_internal/readonly/nested': 0o755,
    }
    try:
        for directory, mode in expected.items():
            assert directory.stat().st_mode & 0o777 == mode
        assert (result.directory / '_internal/readonly/nested/resource').read_bytes() == b'x'
    finally:
        # Permit pytest to retire the intentionally read-only fixture as a normal user.
        (result.directory / '_internal/readonly').chmod(0o750)
