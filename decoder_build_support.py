"""Include only a verified staged optional decoder and its upstream notices."""
from pathlib import Path
import hashlib
import json


def decoder_build_inputs(platform, root=Path('.')):
    if platform not in {'windows', 'linux'}:
        raise ValueError('Unknown decoder platform')
    folder = root / 'assets/decoders' / platform
    manifest = folder / 'decoder-manifest.json'
    if not manifest.is_file():
        return [], []  # Visual feedback remains available without optional audio.
    data = json.loads(manifest.read_text())
    if data.get('platform') != platform or data.get('release') != 'r2168':
        raise ValueError('Wrong staged decoder revision')
    datas, binaries = [(str(manifest), f'assets/decoders/{platform}')], []
    for name, digest in data['files'].items():
        relative = Path(name)
        path = folder / relative
        if relative.is_absolute() or '..' in relative.parts or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError('Unsafe staged decoder path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Staged decoder changed: {name}')
        target = f'assets/decoders/{platform}'
        if relative.parent != Path('.'):
            target += '/' + relative.parent.as_posix()
        entry = (str(path), target)
        (binaries if platform == 'linux' and name == 'vgmstream-cli' else datas).append(entry)
    return datas, binaries
