"""Keep personal native drone preferences independent of the shared UI source."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

from .profiles import _is_directory_link, _path_entry_exists
from .shared_client_settings import SharedSettingsError

_SCOPE = re.compile(r'installation-groups:[0-9a-f]{64}\Z')
_FOLDER = re.compile(r'(?:[0-9a-f]{64}/(?:main|sessions/[0-9a-f]{32})|native-drones/[0-9a-f]{64}/recovered/[1-9][0-9]{0,18}-[1-9][0-9]{0,18})\Z')
_LIMIT = 1024


def _json(data):
    return (json.dumps(data,sort_keys=True,indent=2)+'\n').encode('utf-8')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _check(scope, account_id, character_id):
    if (not isinstance(scope,str) or not _SCOPE.fullmatch(scope)
            or any(type(v) is not int or not 0 < v < 2**63 for v in (account_id,character_id))):
        raise SharedSettingsError('The shared character settings could not be verified.')


def _record_path(root, scope, account_id, character_id):
    _check(scope,account_id,character_id)
    return root/'native-drones'/_hash(scope.encode('ascii'))/f'{account_id}-{character_id}.json'


def _guard(root, path):
    if (not path.is_relative_to(root)
            or os.path.normcase(str(path.resolve())) != os.path.normcase(str(path.absolute()))):
        raise SharedSettingsError('The shared profile backup folder is redirected.')
    for item in (path,*path.parents):
        if _path_entry_exists(item) and _is_directory_link(item):
            raise SharedSettingsError('The shared profile backup folder is redirected.')
        if item==root:
            break


def _read(root,path):
    from .shared_settings_profile import _read_file
    _guard(root,path)
    return _read_file(path)


def _folder(root, relative, scope=None):
    if not isinstance(relative,str) or not _FOLDER.fullmatch(relative):
        raise SharedSettingsError('The shared character settings could not be verified.')
    result=root.joinpath(*relative.split('/'))
    if relative.startswith('native-drones/') and (scope is None or relative.split('/')[1]!=_hash(scope.encode('ascii'))):
        raise SharedSettingsError('The shared character settings could not be verified.')
    _guard(root,result)
    return result


def _account_bytes(root,folder,account_id,character_id):
    if not (folder/f'core_char_{character_id}.dat').is_file():
        raise SharedSettingsError('The shared character settings could not be verified.')
    _read(root,folder/f'core_char_{character_id}.dat')
    return _read(root,folder/f'core_user_{account_id}.dat')


def read_personal_settings(root: Path, scope: str, account_id: int, character_id: int) -> bytes | None:
    """Read the last personal client, even when a different UI source was chosen."""
    path=personal_settings_file(root,scope,account_id,character_id)
    return _read(Path(root).absolute(),path) if path is not None else None


def personal_settings_file(root: Path, scope: str, account_id: int, character_id: int) -> Path | None:
    """Locate an attributed personal file without changing its profile."""
    root=Path(root).absolute()
    record=_record_path(root,scope,account_id,character_id)
    _guard(root,record)
    if not record.exists():
        return _scoped_predecessor_file(root,scope,account_id,character_id)
    try:
        saved=json.loads(_read(root,record))
        if (not isinstance(saved,dict) or saved.get('schemaVersion')!=1 or saved.get('storageIdentity')!=scope
                or any(type(saved.get(key)) is not int for key in ('schemaVersion','accountID','characterID'))
                or (saved.get('accountID'),saved.get('characterID'))!=(account_id,character_id)):
            raise ValueError('Invalid personal drone profile')
        folder=_folder(root,saved.get('folder'),scope)
        marker=json.loads(_read(root,folder/'native-drone-profile.json'))
        if (not isinstance(marker,dict)
                or any(type(marker.get(key)) is not int for key in ('schemaVersion','accountID','characterID'))
                or marker!={'schemaVersion':1,'storageIdentity':scope,'accountID':account_id,'characterID':character_id}):
            raise ValueError('Invalid personal drone profile authority')
        _account_bytes(root,folder,account_id,character_id)
        return folder/f'core_user_{account_id}.dat'
    except (OSError,ValueError,TypeError,KeyError) as exc:
        raise SharedSettingsError('The shared character settings could not be verified.') from exc


def remember_personal_settings(root: Path, scope: str, account_id: int, character_id: int, folder: Path) -> None:
    """Remember the generated native file; later client saves remain authoritative."""
    from .shared_settings_profile import _safe_storage,_atomic_bytes
    root=Path(root).absolute()
    record=_record_path(root,scope,account_id,character_id)
    try:
        relative=Path(folder).absolute().relative_to(root).as_posix()
    except ValueError as exc:
        raise SharedSettingsError('The shared character settings could not be verified.') from exc
    folder=_folder(root,relative,scope)
    _account_bytes(root,folder,account_id,character_id)
    _guard(root,record.parent)
    _safe_storage(record.parent)
    marker={'schemaVersion':1,'storageIdentity':scope,'accountID':account_id,'characterID':character_id}
    marker_path=folder/'native-drone-profile.json'
    _guard(root,marker_path)
    if marker_path.exists() and json.loads(_read(root,marker_path))!=marker:
        raise SharedSettingsError('The shared character settings could not be verified.')
    _atomic_bytes(marker_path,_json(marker))
    _atomic_bytes(record,_json({**marker,'folder':relative}))


def _scoped_predecessor_file(root,scope,account_id,character_id):
    """Bootstrap only from manifests already bound to this logical world.

    Unscoped historical worlds belong to the separate reviewed recovery flow.
    Different source characters inside the same scope do not change ownership.
    """
    if not root.is_dir():
        return None
    manifests=list(root.glob('*/active.json'))
    if len(manifests)>_LIMIT:
        raise SharedSettingsError('The shared character settings could not be verified.')
    candidates={}
    for manifest in manifests:
        try:
            saved=json.loads(_read(root,manifest))
            if not isinstance(saved,dict):
                continue
            identity=saved.get('identity',{})
            if not isinstance(identity,dict):
                continue
            if identity.get('storageIdentity')!=scope:
                continue
            if saved.get('schemaVersion')!=1 or manifest.parent.name!=_hash(_json(identity)):
                raise ValueError('Invalid scoped profile manifest')
            source=identity['source']
            if (not isinstance(source,dict) or set(source)!={'username','account_id','character_id'}
                    or not isinstance(source['username'],str)
                    or any(type(source[k]) is not int or not 0<source[k]<2**63 for k in ('account_id','character_id'))):
                raise ValueError('Invalid scoped profile source')
            base_identity={'source':source,'storageIdentity':scope}
            base=root/_hash(_json(base_identity))
            main=base/'main'
            if identity.get('settingsFolder')!=os.path.normcase(str(main)):
                raise ValueError('Invalid scoped profile location')
            if (source['account_id'],source['character_id'])==(account_id,character_id):
                folder=main
                modified=(main/f'core_user_{account_id}.dat').stat().st_mtime_ns
            else:
                pointer=base/'pilots'/f'{account_id}-{character_id}.json'
                if not pointer.exists():
                    continue
                item=json.loads(_read(root,pointer))
                if (not isinstance(item,dict) or item.get('schemaVersion')!=1
                        or (item.get('accountID'),item.get('characterID'))!=(account_id,character_id)
                        or not isinstance(item.get('session'),str)
                        or not re.fullmatch(r'[0-9a-f]{32}',item['session'])):
                    raise ValueError('Invalid scoped personal profile')
                folder=base/'sessions'/item['session']
                modified=pointer.stat().st_mtime_ns
            data=_account_bytes(root,folder,account_id,character_id)
            candidates[str(folder)]=(modified,folder/f'core_user_{account_id}.dat')
        except (OSError,ValueError,TypeError,KeyError) as exc:
            raise SharedSettingsError('The shared character settings could not be verified.') from exc
    if not candidates:
        return None
    newest=max(modified for modified,_ in candidates.values())
    latest=sorted(path for modified,path in candidates.values() if modified==newest)
    if len({_hash(_read(root,path)) for path in latest})!=1:
        raise SharedSettingsError('The shared character settings could not be verified.')
    return latest[0]
