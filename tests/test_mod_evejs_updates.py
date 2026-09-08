from dataclasses import replace
import io
import json
import pytest
from src.core.mod_evejs_compatibility import installed_evejs_version, parse_evejs_versions
from src.core.mod_update_source import parse_update_source, check_update, ModUpdateError
from src.core.mod_updates import install_release
from src.core.mod_operations import ModOperationContext
from src.core.mod_manifest import scan_mods
from test_mod_updates import fixture


def feed(restrictions):
    releases, metadata = [], {}
    for number, versions in enumerate(restrictions, start=1):
        version = f'1.{number}.0'
        base = f'https://github.com/Author/Example/releases/download/v{version}/Example-{version}'
        body = dict(schemaVersion=1, id='author.example', version=version, asset=f'Example-{version}.zip')
        if versions is not None:
            body['evejsVersions'] = versions
        content = json.dumps(body).encode()
        metadata[base + '.update.json'] = content
        releases.append(dict(draft=False, prerelease=False, tag_name='v'+version,
            html_url=f'https://github.com/Author/Example/releases/tag/v{version}',
            assets=[dict(name=f'Example-{version}.zip', state='uploaded', size=100, browser_download_url=base+'.zip'),
                    dict(name=f'Example-{version}.update.json', state='uploaded', size=len(content), browser_download_url=base+'.update.json')]))
    def opener(url):
        return io.BytesIO(json.dumps(releases).encode() if 'api.github.com' in url else metadata[url])
    return opener


@pytest.mark.parametrize('installed,expected', [('0.12.7.1', '1.1.0'), ('0.12.8', '1.2.0'), ('0.12.6', None), (None, None)])
def test_only_newest_compatible_mod_is_offered(installed, expected):
    source = parse_update_source(dict(provider='github', repository='Author/Example', asset='Example-{version}.zip'))
    result = check_update(source, '1.0.0', evejs_version=installed, expected_mod_id='author.example',
                          opener=feed([['0.12.7.1'], ['0.12.8']]))
    assert (result.version if result else None) == expected


@pytest.mark.parametrize('installed', ['0.12.7.1', '999.1.0', None])
def test_omitted_evejs_constraint_is_independent(installed):
    source = parse_update_source(dict(provider='github', repository='Author/Example', asset='Example-{version}.zip'))
    assert check_update(source, '1.0.0', evejs_version=installed, expected_mod_id='author.example',
                        opener=feed([None])).version == '1.1.0'


def test_detection_does_not_guess_from_folder_name(tmp_path):
    root = tmp_path / 'EveJS-v99.0.0'
    root.mkdir()
    assert installed_evejs_version(root) is None
    (root / 'package.json').write_text('{"version":"0.12.7.1"}')
    assert installed_evejs_version(root) == '0.12.7.1'
    (root / 'config').mkdir()
    version = root / 'config/version.json'
    version.write_text('{"evejsVersion":"0.12.7.1"}')
    assert installed_evejs_version(root) == '0.12.7.1'
    version.write_text('{"evejsVersion":"0.12.8"}')
    assert installed_evejs_version(root) is None


def test_evejs_change_after_offer_blocks_install(fixture):
    root, target, offer, downloader = fixture
    (root / 'package.json').write_text('{"version":"0.12.8"}')
    offer = replace(offer, evejs_versions=('0.12.7.1',))
    with pytest.raises(ModUpdateError, match='installed EveJS'):
        install_release(scan_mods(root)[0], offer, ModOperationContext(root), guard=lambda: None, downloader=downloader)
    assert scan_mods(root)[0].version == '1.0.0'


def test_zip_cannot_hide_a_restriction_missing_from_sidecar(fixture, tmp_path):
    import zipfile
    import shutil
    root, target, offer, downloader = fixture
    original = tmp_path / 'original.zip'
    downloader(offer, original)
    changed = tmp_path / 'changed.zip'
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(changed, 'w') as destination:
        for item in source.infolist():
            content = source.read(item)
            if item.filename.endswith('evejs-launcher.mod.json'):
                document = json.loads(content)
                document['compatibility'] = dict(evejsVersions=['99.0.0'])
                content = json.dumps(document).encode()
            destination.writestr(item.filename, content)
    with pytest.raises(ModUpdateError, match='compatibility differs'):
        install_release(scan_mods(root)[0], offer, ModOperationContext(root), guard=lambda: None,
                        downloader=lambda release, target: shutil.copyfile(changed, target))
    assert scan_mods(root)[0].version == '1.0.0'
