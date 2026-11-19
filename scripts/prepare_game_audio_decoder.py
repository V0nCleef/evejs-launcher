"""Stage a pinned optional upstream decoder; never download game media."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
RELEASE = 'r2168'
ARCHIVES = {
    'windows': ('vgmstream-win64.zip', '692d45070852a35c1fa42726aa3c9de528e44eec925d37d99b1074256b99a229'),
    'linux': ('vgmstream-linux.zip', '2dfdfddda68e0e36e62f20dc050a4e45f4bc3318c7efeadf8a063df646d57c84'),
}
LICENSES = (
    'celt-0.11.0.COPYING', 'celt-0.6.1.COPYING', 'ffmpeg-9.0.2.COPYING.LGPLv2.1',
    'ffmpeg-9.0.2.COPYING.LGPLv3', 'libogg-1.3.5.COPYING', 'libvorbis-1.3.7.COPYING',
    'mpg123-1.33.7-COPYING', 'opus-1.6.1-COPYING', 'speex-1.2.1.COPYING',
)


def download(url):
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ValueError('Decoder download exceeds its size bound')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=ARCHIVES, required=True)
    parser.add_argument('--archive', type=Path, help='Use an already downloaded, hash-verified upstream archive')
    args = parser.parse_args()
    filename, expected = ARCHIVES[args.platform]
    folder = ROOT / 'assets/decoders' / args.platform
    folder.mkdir(parents=True, exist_ok=True)
    assert folder.resolve().is_relative_to(ROOT.resolve()) and not folder.is_symlink()
    archive = args.archive
    owned_archive = archive is None
    if owned_archive:
        archive = folder / filename
        archive.write_bytes(download(f'https://github.com/vgmstream/vgmstream/releases/download/{RELEASE}/{filename}'))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError('Upstream decoder archive does not match the reviewed checksum')
    files = []
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            if item.is_dir():
                continue
            name = Path(item.filename)
            if len(name.parts) != 1 or name.is_absolute() or item.file_size > 64 * 1024 * 1024:
                raise ValueError('Unexpected path in the reviewed decoder archive')
            destination = folder / name
            if destination.is_symlink():
                raise ValueError('Decoder destination cannot be a symbolic link')
            destination.write_bytes(zipped.read(item))
            files.append(destination)
    executable = folder / ('vgmstream-cli.exe' if args.platform == 'windows' else 'vgmstream-cli')
    if not executable.is_file():
        raise ValueError('Decoder executable missing from upstream archive')
    executable.chmod(0o755)
    notices = folder / 'licenses'
    notices.mkdir(exist_ok=True)
    for name in LICENSES:
        path = notices / name
        path.write_bytes(download(f'https://raw.githubusercontent.com/vgmstream/vgmstream/{RELEASE}/ext_libs/licenses/{name}'))
        files.append(path)
    copying = folder / 'COPYING'
    if not copying.exists():
        copying.write_bytes(download(f'https://raw.githubusercontent.com/vgmstream/vgmstream/{RELEASE}/COPYING'))
        files.append(copying)
    manifest = {'upstream': 'https://github.com/vgmstream/vgmstream', 'release': RELEASE,
                'archive': filename, 'archiveSha256': expected, 'platform': args.platform,
                'source': f'https://github.com/vgmstream/vgmstream/tree/{RELEASE}',
                'dependencySources': f'https://github.com/vgmstream/vgmstream/tree/{RELEASE}/ext_libs',
                'files': {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted(set(files))}}
    (folder / 'decoder-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    if owned_archive:
        archive.unlink()
    print(json.dumps({'platform': args.platform, 'files': len(files), 'archiveSha256': expected}))


if __name__ == '__main__':
    main()
