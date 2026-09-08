"""Emit the small GitHub release asset from the ZIP users will actually install.

Usage: python build_mod_update_metadata.py path/to/MyMod-1.2.3.zip
No imports from launcher code or external dependencies are required.
"""
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile


def build(archive_path):
    archive_path = Path(archive_path)
    if archive_path.suffix.lower() != '.zip':
        raise ValueError('Provide the release ZIP, not a source directory.')
    with zipfile.ZipFile(archive_path) as archive:
        matches = [item for item in archive.infolist() if PurePosixPath(item.filename).name == 'evejs-launcher.mod.json']
        if len(matches) != 1 or matches[0].file_size > 1024 * 1024:
            raise ValueError('The ZIP must contain exactly one bounded public mod manifest.')
        manifest = json.loads(archive.read(matches[0]))
    if manifest.get('schemaVersion') != 3:
        raise ValueError('Use a schema-3 mod manifest.')
    version = manifest['version']
    updates = manifest['updates']
    expected = updates['asset'].replace('{version}', version)
    if expected != archive_path.name:
        raise ValueError(f'The manifest expects the release ZIP to be named {expected}.')
    metadata = dict(schemaVersion=1, id=manifest['id'], version=version, asset=archive_path.name)
    versions = manifest.get('compatibility', {}).get('evejsVersions')
    if versions is not None:
        metadata['evejsVersions'] = versions
    target = archive_path.with_suffix('.update.json')
    with target.open('x', encoding='utf-8') as output:
        json.dump(metadata, output, ensure_ascii=False, indent=2)
        output.write('\n')
    return target


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(build(sys.argv[1]))
