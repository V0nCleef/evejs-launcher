"""Bounded Linux onedir extraction with contained PyInstaller symlinks."""
from __future__ import annotations

from dataclasses import dataclass
import gzip
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import struct
import tarfile

PACKAGE_DIRECTORY = 'EveJS-Launcher-Linux-x86_64'
PACKAGE_NAME = PACKAGE_DIRECTORY + '.tar.gz'
EXECUTABLE = 'evejs-launcher'


class _TarInput:
    """Bound special-header reads before tarfile allocates their declared size."""
    def __init__(self, stream):
        self.stream = stream

    def read(self, size):
        if size < 0 or size > 16 * 1024**2:
            raise ValueError('The Linux update contains oversized tar metadata.')
        return self.stream.read(size)

    def seek(self, offset, whence=0):
        position = offset if whence == 0 else self.tell() + offset if whence == 1 else -1
        if not 0 <= position <= 2 * 1024**3 + 64 * 1024**2:
            raise ValueError('The Linux update tar stream exceeds its size limit.')
        return self.stream.seek(offset, whence)

    def tell(self):
        return self.stream.tell()


@dataclass(frozen=True)
class LinuxPackage:
    directory: Path
    version: str
    file_count: int


def _relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or '\\' in name or path.is_absolute() or '..' in path.parts
            or any(ord(char) < 32 for char in name)
            or not path.parts or path.parts[0] != PACKAGE_DIRECTORY):
        raise ValueError('The Linux update contains an unsafe archive path.')
    return path


def validate_executable(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('The Linux update executable is missing or redirected.')
    with path.open('rb') as stream:
        header = stream.read(64)
    if (len(header) < 64 or header[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', header, 18)[0] != 62):
        raise ValueError('The Linux update executable is not an x86_64 ELF binary.')
    if not path.stat().st_mode & 0o111:
        raise ValueError('The Linux update executable has no execute permission.')


def extract_linux_package(archive: Path, destination: Path, *, expected_version: str | None = None) -> LinuxPackage:
    if destination.exists() or destination.is_symlink():
        raise ValueError('Linux update extraction requires a fresh private destination.')
    if archive.stat().st_size > 512 * 1024**2:
        raise ValueError('The Linux update archive exceeds its download size limit.')
    with gzip.open(archive, 'rb') as compressed, tarfile.open(fileobj=_TarInput(compressed), mode='r:') as source:
        members = []
        kinds = {}
        total = 0
        for entry in source:
            if len(members) >= 20000:
                raise ValueError('The Linux update contains too many archive entries.')
            path = _relative(entry.name)
            name = path.as_posix()
            if name in kinds:
                raise ValueError('The Linux update contains duplicate archive paths.')
            if not entry.issym() and entry.mode & 0o022:
                raise ValueError('The Linux update contains files writable by other users.')
            if entry.isdir():
                kind = 'directory'
            elif entry.isreg():
                kind = 'file'
                total += entry.size
                if entry.size < 0 or entry.size > 512 * 1024**2 or total > 2 * 1024**3:
                    raise ValueError('The Linux update exceeds its expanded size limit.')
            elif entry.issym():
                kind = 'link'
                link = PurePosixPath(entry.linkname)
                target = posixpath.normpath(posixpath.join(path.parent.as_posix(), entry.linkname))
                if (not entry.linkname or link.is_absolute() or '\\' in entry.linkname
                        or any(ord(char) < 32 for char in entry.linkname)
                        or not target.startswith(PACKAGE_DIRECTORY + '/')):
                    raise ValueError('The Linux update contains an escaping symlink.')
            else:
                raise ValueError('The Linux update contains a hardlink, device or unsupported entry.')
            kinds[name] = kind
            members.append((path, entry, kind))
        if kinds.get(PACKAGE_DIRECTORY) != 'directory':
            raise ValueError('The Linux update package root is missing.')
        for path, _, _ in members:
            for parent in path.parents:
                if str(parent) != '.' and kinds.get(parent.as_posix()) in ('link', 'file'):
                    raise ValueError('The Linux update writes through a linked or non-directory parent.')
        # Do not inherit a user's umask (commonly 0002 on Ubuntu). The updater
        # requires a non-public-writable install, including implicit tar parents.
        directories = {}
        for path, entry, kind in members:
            for parent in path.parents:
                if str(parent) != '.':
                    directories.setdefault(parent, 0o755)
            if kind == 'directory':
                directories[path] = entry.mode & 0o777
        destination.mkdir(mode=0o700)
        destination.chmod(0o700)
        for path in sorted(directories, key=lambda value: len(value.parts)):
            target = destination.joinpath(*path.parts)
            target.mkdir(mode=0o700)
            target.chmod(0o700)
        for path, entry, kind in members:
            target = destination.joinpath(*path.parts)
            if kind == 'file':
                with source.extractfile(entry) as content, target.open('xb') as output:
                    shutil.copyfileobj(content, output, length=1024 * 1024)
                target.chmod(entry.mode & 0o777)
        for path, entry, kind in members:
            if kind == 'link':
                target = destination.joinpath(*path.parts)
                target.symlink_to(entry.linkname)
        directory = destination / PACKAGE_DIRECTORY
        for path in directory.rglob('*'):
            if path.is_symlink():
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise ValueError('The Linux update contains an unresolved or cyclic symlink.') from exc
                if not resolved.is_relative_to(directory):
                    raise ValueError('The Linux update contains an escaping symlink chain.')
        validate_executable(directory / EXECUTABLE)
        runtime = directory / '_internal'
        version_file = runtime / 'VERSION'
        if runtime.is_symlink() or not runtime.is_dir() or version_file.is_symlink() or not version_file.is_file():
            raise ValueError('The Linux update runtime or version record is incomplete.')
        with version_file.open('rb') as stream:
            version = stream.read(65).decode('ascii').strip()
        if not re.fullmatch(r'\d+\.\d+\.\d+', version) or (expected_version is not None and version != expected_version):
            raise ValueError('The Linux update version does not match the release.')
        if not (runtime / 'base_library.zip').is_file():
            raise ValueError('The Linux update Python runtime is incomplete.')
        # Apply final directory modes after writes and validation so read-only
        # archive directories remain usable while their children are extracted.
        for path in sorted(directories, key=lambda value: len(value.parts), reverse=True):
            destination.joinpath(*path.parts).chmod(directories[path])
    return LinuxPackage(directory, version, sum(kind == 'file' for _, _, kind in members))
