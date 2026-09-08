"""Optional EveJS compatibility, kept separate from mod SemVer and EVE builds."""
import json
from pathlib import Path
import re


def normalize_evejs_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*))?', value):
        raise ValueError('EveJS versions must have three or four numeric components, such as 0.12.7.1.')
    return value.removeprefix('v')


def parse_evejs_versions(value):
    if value is None:
        return None
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise ValueError('evejsVersions must be a nonempty list of at most 64 exact versions, or be omitted.')
    versions = tuple(normalize_evejs_version(item) for item in value)
    if len(set(versions)) != len(versions):
        raise ValueError('evejsVersions contains duplicate versions.')
    return versions


def supports_evejs(versions, installed):
    return versions is None or (installed is not None and installed in versions)


def installed_evejs_version(root):
    """Read installed metadata, never infer a release from a folder's name.

    A disagreement is unknown, not a reason to guess which file is newer.
    """
    from .mod_api_manifest import _safe_path, _relative_path
    root = Path(root).resolve(strict=True)
    found = []
    try:
        for filename, key in (('package.json', 'version'), ('config/version.json', 'evejsVersion')):
            path = _safe_path(root, _relative_path(filename, 'Version file'), 'Version file')
            if not path.exists():
                continue
            if not path.is_file() or path.stat().st_size > 65536:
                return None
            payload = json.loads(path.read_text(encoding='utf-8-sig'))
            found.append(normalize_evejs_version(payload.get(key)))
    except (OSError, ValueError, AttributeError):
        return None
    return found[0] if found and len(set(found)) == 1 else None
