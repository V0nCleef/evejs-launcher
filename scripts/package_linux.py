"""Package one Linux onedir deliverable, preserving modes and relative symlinks."""
from __future__ import annotations
import hashlib
from pathlib import Path
import tarfile


def main():
    root = Path(__file__).resolve().parent.parent
    directory = root / 'dist/EveJS-Launcher-Linux-x86_64'
    binary = directory / 'evejs-launcher'
    if not binary.is_file() or binary.read_bytes()[:4] != b'\x7fELF':
        raise ValueError('Build the native Linux onedir before packaging.')
    output = root / 'dist/EveJS-Launcher-Linux-x86_64.tar.gz'
    for path in directory.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('The Linux package contains an escaping symlink.')
    def release_mode(entry):
        # A build mounted from NTFS can report every file as 0777. Retain
        # execute/read bits while removing public/group write permissions.
        if not entry.issym():
            entry.mode &= 0o755
        entry.uid = entry.gid = 0
        entry.uname = entry.gname = ''
        return entry
    with tarfile.open(output, 'w:gz', dereference=False) as archive:
        archive.add(directory, arcname=directory.name, filter=release_mode)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(f'{digest}  {output.name}\n')
    print(f'{output.name}: {output.stat().st_size} bytes sha256={digest}')


if __name__ == '__main__':
    main()
