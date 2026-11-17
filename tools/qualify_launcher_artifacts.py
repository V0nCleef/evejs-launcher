"""Record reproducible public input/package hashes and reject private bundle data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_private(relative):
    blocked = {'.qa', '.git', '.hermes', '.venv', '__pycache__', '_local', '_backup', 'node_modules'}
    if (blocked.intersection(relative.parts) or relative.suffix.lower() in ('.vhdx', '.sqlite', '.db')
            or relative.name in ('.evejs-linux.conf', '.env', 'project-vm.json', 'registration-result.json')):
        raise ValueError('The artifact contains private/local data: ' + str(relative))


def describe(records):
    records.sort()
    encoded = json.dumps(records, separators=(',', ':'), ensure_ascii=True).encode()
    return {'fileCount': len(records), 'fileBytes': sum(record[1] for record in records if isinstance(record[1], int)),
            'treeSHA256': hashlib.sha256(encoded).hexdigest(), 'records': records}


def tree(folder):
    records = []
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            if not path.resolve(strict=True).is_relative_to(folder.resolve()):
                raise ValueError('An artifact link escapes its bundle.')
            records.append([path.relative_to(folder).as_posix(), 'link', path.readlink().as_posix()])
        elif path.is_file():
            relative = path.relative_to(folder)
            check_private(relative)
            records.append([relative.as_posix(), path.stat().st_size, sha(path)])
    return describe(records)


def linux_archive_tree(path):
    # Windows cannot dereference Linux's relative symlink reparse records.
    # Hash the actual release entries, which are identical on every host.
    records = []
    with tarfile.open(path, 'r:gz') as archive:
        for entry in archive:
            relative = PurePosixPath(entry.name)
            if relative.parts[0] != 'EveJS-Launcher-Linux-x86_64' or '..' in relative.parts:
                raise ValueError('The Linux archive root is invalid.')
            name = PurePosixPath(*relative.parts[1:])
            check_private(name)
            if entry.issym():
                target = posixpath.normpath(posixpath.join(relative.parent.as_posix(), entry.linkname))
                if not target.startswith('EveJS-Launcher-Linux-x86_64/'):
                    raise ValueError('The Linux archive link escapes its bundle.')
                records.append([str(name), 'link', entry.linkname])
            elif entry.isreg():
                if entry.mode & 0o022:
                    raise ValueError('Linux release contains publicly writable files.')
                with archive.extractfile(entry) as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                records.append([str(name), entry.size, digest])
    return describe(records)


def source_manifest():
    paths = []
    for name in ('src', 'assets', 'docs', 'examples', 'licenses'):
        paths.extend(path for path in (ROOT / name).rglob('*') if path.is_file() and '__pycache__' not in path.parts)
    for name in ('main.py', 'VERSION', 'CHANGELOG.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md',
                 'build.spec', 'build_linux.spec', 'build_support.py', 'requirements-linux.txt', 'requirements-build.txt',
                 'scripts/build_linux.sh', 'scripts/package_linux.py'):
        paths.append(ROOT / name)
    records = [[path.relative_to(ROOT).as_posix(), sha(path)] for path in sorted(set(paths))]
    return {'algorithm': 'sha256(JSON compact sorted [relativePath,fileSHA256] records)',
        'sha256': hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest(), 'records': records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / '.qa/artifact-qualification.json')
    parser.add_argument('--windows-zip', action='store_true')
    args = parser.parse_args()
    windows = ROOT / 'dist/EveJS-Launcher-V1'
    linux = ROOT / 'dist/EveJS-Launcher-Linux-x86_64'
    if args.windows_zip:
        destination = ROOT / 'dist/EveJS-Launcher-V1.zip'
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(windows.rglob('*')):
                if path.is_file(): archive.write(path, path.relative_to(windows.parent).as_posix())
        (destination.parent / (destination.name + '.sha256')).write_text(sha(destination) + '  ' + destination.name + '\n')
    result = {'protocol': 'evejs_launcher_artifact_qualification_v1', 'source': source_manifest(),
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
        'windows': tree(windows), 'linux': linux_archive_tree(ROOT / 'dist/EveJS-Launcher-Linux-x86_64.tar.gz')}
    for host, directory, executable, archive in (
        ('windows', windows, 'EveJS-Launcher-V1.exe', ROOT / 'dist/EveJS-Launcher-V1.zip'),
        ('linux', linux, 'evejs-launcher', ROOT / 'dist/EveJS-Launcher-Linux-x86_64.tar.gz')):
        result[host]['executableSHA256'] = sha(directory / executable)
        if archive.is_file():
            result[host]['archiveSHA256'] = sha(archive)
            result[host]['archiveBytes'] = archive.stat().st_size
    with tarfile.open(ROOT / 'dist/EveJS-Launcher-Linux-x86_64.tar.gz', 'r:gz') as archive:
        unsafe = [entry.name for entry in archive if not entry.issym() and entry.mode & 0o022]
        if unsafe: raise ValueError('Linux release contains publicly writable files.')
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({host: {key: value for key, value in result[host].items() if key != 'records'} for host in ('windows', 'linux')}, indent=2))


if __name__ == '__main__':
    main()
