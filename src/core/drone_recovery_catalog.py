"""Bounded, read-only selected-world inventory and saved-profile discovery."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from . import shared_drone_settings as codec
from .native_drone_profiles import _guard
from .shared_client_settings import SharedSettingsError
from .shared_settings_profile import _json_bytes, _read_file

MAX_PROFILES=4096
MAX_PROFILE_BYTES=64*1024*1024
MAX_ROWS=100_000


class CatalogLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class PilotInventory:
    account_id: int
    character_id: int
    username: str
    name: str
    ship_id: int
    quantities: tuple[tuple[int,int], ...]
    bay_quantities: tuple[tuple[int,int], ...] = ()

    @property
    def owned_ids(self):
        return frozenset(item for item,_ in self.quantities)


@dataclass(frozen=True)
class SavedDroneProfile:
    world_root: str
    account_id: int
    character_ids: frozenset[int]
    path: Path
    data: bytes
    modified_ns: int
    sha256: str
    storage_root: Path | None = None
    active_for: frozenset[int] = frozenset()
    active_ns: int = 0


def _positive(value):
    return type(value) is int and 0<value<2**63


def inventory_facts(database: Path) -> tuple[PilotInventory, ...]:
    """The database MUST come from the selected Native or verified bind provider."""
    database=Path(database).resolve(strict=True)
    accounts={}
    characters={}
    quantities={}
    con=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)
    try:
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')
        for n,(username,blob) in enumerate(con.execute('SELECT key,json FROM accounts LIMIT 4097')):
            if n>=4096:
                raise ValueError('Account limit exceeded')
            row=json.loads(blob)
            identity=row.get('id') if isinstance(row,dict) else None
            if not _positive(identity) or not isinstance(username,str) or not 0<len(username)<=256:
                continue
            if identity in accounts:
                raise ValueError('Ambiguous account identity')
            accounts[identity]=username
        for n,(key,blob) in enumerate(con.execute('SELECT key,json FROM characters LIMIT 20001')):
            if n>=20000:
                raise ValueError('Character limit exceeded')
            row=json.loads(blob)
            if (not isinstance(row,dict) or not _positive(row.get('accountId'))
                    or row.get('accountId') not in accounts):
                continue
            if not isinstance(key,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',key):
                continue
            identity=int(key)
            ship=row.get('shipID')
            name=row.get('characterName')
            if not _positive(identity) or not isinstance(name,str) or not 0<len(name)<=256:
                continue
            ship_row=con.execute('SELECT json FROM items WHERE key=?',(str(ship),)).fetchone() if _positive(ship) else None
            ship_data=json.loads(ship_row[0]) if ship_row else None
            if (not isinstance(ship_data,dict) or not _positive(ship_data.get('ownerID'))
                    or not _positive(ship_data.get('itemID')) or ship_data.get('ownerID')!=identity
                    or ship_data.get('itemID')!=ship or ship_data.get('categoryID')!=6):
                ship=0
            characters[identity]=(row['accountId'],name,ship)
            quantities[identity]={}
        bays={identity:{} for identity in characters}
        query="SELECT key,json FROM items WHERE json_extract(json,'$.categoryID')=18 LIMIT 100001"
        for n,(key,blob) in enumerate(con.execute(query)):
            if n>=MAX_ROWS:
                raise ValueError('Drone inventory limit exceeded')
            row=json.loads(blob)
            owner=row.get('ownerID')
            if not _positive(owner) or owner not in characters:
                continue
            identity=row.get('itemID')
            if not _positive(identity) or str(identity)!=key:
                continue
            singleton,quantity=row.get('singleton'),row.get('quantity')
            if type(singleton) is int and singleton==1:
                count=1
            elif type(quantity) is int and 0<quantity<2**31:
                count=quantity
            else:
                continue
            quantities[owner][identity]=count
            if row.get('flagID')==87 and row.get('locationID')==characters[owner][2]:
                bays[owner][identity]=count
        return tuple(PilotInventory(account,identity,accounts[account],name,ship,
                                    tuple(sorted(quantities[identity].items())),tuple(sorted(bays[identity].items())))
                     for identity,(account,name,ship) in sorted(characters.items()))
    finally:
        con.close()


def saved_profiles(roots: tuple[Path,...], scope: str, selected_world_root: str,
                   pilots: tuple[PilotInventory,...], *,
                   target_identity: str | None = None) -> tuple[SavedDroneProfile,...]:
    """Foreign stable scopes are excluded; unscoped roots remain review choices."""
    from .drone_group_recovery import native_memberships
    accounts={}
    for pilot in pilots:
        accounts.setdefault(pilot.account_id,set()).add(pilot.character_id)
    found={}
    total_bytes=0
    inspected=0
    for requested_root in dict.fromkeys(roots):
        root=Path(requested_root).absolute()
        if not root.is_dir():
            continue
        _guard(root,root)
        manifests=list(root.glob('*/active.json'))
        if len(manifests)>1024:
            raise SharedSettingsError('The shared character settings could not be verified.')
        for path in manifests:
            _guard(root,path)
            try:
                document=json.loads(_read_file(path))
                identity=document.get('identity') if isinstance(document,dict) else None
                if not isinstance(identity,dict):
                    continue
                stable=identity.get('storageIdentity')
                if stable is not None and stable!=scope:
                    continue
                if document.get('schemaVersion')!=1 or path.parent.name!=hashlib.sha256(_json_bytes(identity)).hexdigest():
                    continue
                source=identity.get('source')
                if (not isinstance(source,dict) or not _positive(source.get('account_id'))
                        or not _positive(source.get('character_id')) or not isinstance(source.get('username'),str)):
                    continue
                if stable is None:
                    if identity.get('targetIdentity')!=target_identity:
                        continue
                    world=identity.get('serverRoot')
                    if (not isinstance(world,str) or not Path(world).is_absolute()
                            or os.path.normcase(str(Path(source.get('evejs_root','')).resolve()))!=os.path.normcase(str(Path(world).resolve()))):
                        continue
                else:
                    world=selected_world_root
                base_identity={k:v for k,v in identity.items() if k!='settingsFolder'}
                base=root/hashlib.sha256(_json_bytes(base_identity)).hexdigest()
                expected=base/'main'
                if os.path.normcase(str(expected))!=identity.get('settingsFolder'):
                    continue
                folders=[expected]
                sessions=base/'sessions'
                _guard(root,sessions)
                if sessions.is_dir():
                    entries=list(sessions.iterdir())
                    if len(entries)>MAX_PROFILES:
                        raise CatalogLimitError('Saved profile limit exceeded')
                    folders.extend(item for item in entries if re.fullmatch(r'[0-9a-f]{32}',item.name) and item.is_dir())
                for folder in folders:
                    _guard(root,folder)
                    if not folder.is_dir():
                        continue
                    inspected+=1
                    if inspected>MAX_PROFILES:
                        raise CatalogLimitError('Saved profile limit exceeded')
                    for account,allowed_characters in accounts.items():
                        data_path=folder/f'core_user_{account}.dat'
                        if not data_path.is_file() or str(data_path) in found:
                            continue
                        character_ids=frozenset(character for character in allowed_characters
                                                if (folder/f'core_char_{character}.dat').is_file())
                        if not character_ids:
                            continue
                        for character in character_ids:
                            _guard(root,folder/f'core_char_{character}.dat')
                        _guard(root,data_path)
                        info=data_path.stat()
                        data=_read_file(data_path)
                        after=data_path.stat()
                        if (info.st_size,info.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                            raise ValueError('Saved preferences changed while reading')
                        total_bytes+=len(data)
                        if total_bytes>MAX_PROFILE_BYTES:
                            raise CatalogLimitError('Saved profile byte limit exceeded')
                        active_for=set()
                        active_ns=0
                        if folder==expected and account==source['account_id'] and source['character_id'] in character_ids:
                            active_for.add(source['character_id'])
                            active_ns=info.st_mtime_ns
                        for character in character_ids:
                            pointer=base/'pilots'/f'{account}-{character}.json'
                            if not pointer.is_file():
                                continue
                            _guard(root,pointer)
                            point=json.loads(_read_file(pointer))
                            if (isinstance(point,dict) and point.get('schemaVersion')==1
                                    and type(point.get('accountID')) is int and type(point.get('characterID')) is int
                                    and (point['accountID'],point['characterID'])==(account,character)
                                    and folder.parent==sessions and point.get('session')==folder.name):
                                active_for.add(character)
                                active_ns=max(active_ns,pointer.stat().st_mtime_ns)
                        current_authority=bool(active_for) and os.path.normcase(str(Path(world).resolve()))==os.path.normcase(str(Path(selected_world_root).resolve()))
                        if codec.has_drone_settings(data) and not current_authority:
                            native_memberships(data)
                        elif not active_for:
                            continue
                        found[str(data_path)]=SavedDroneProfile(os.path.normcase(str(Path(world).resolve())),
                            account,character_ids,data_path,data,info.st_mtime_ns,hashlib.sha256(data).hexdigest(),
                            root,frozenset(active_for),active_ns)
            except (OSError,ValueError,UnicodeError,TypeError,KeyError):
                # A corrupt or unrelated archived snapshot is never an authority
                # for a recovery. Current selected profiles are checked separately.
                continue
    return tuple(found[key] for key in sorted(found))
