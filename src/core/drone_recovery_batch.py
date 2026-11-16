"""Prepare one reviewed recovery for all characters in the selected installation."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

from . import native_drone_profiles as native
from . import shared_drone_settings as codec
from .drone_group_recovery import RecoveryPlan, native_memberships, plan_recovery, seed_missing_groups
from .drone_recovery_catalog import PilotInventory, SavedDroneProfile, inventory_facts, saved_profiles
from .shared_client_settings import SharedSettingsError


class RecoveryChangedError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecoveryContext:
    storage_root: Path
    scope: str
    database: Path
    world_root: str
    history_roots: tuple[Path,...]
    target_identity: str | None = None


@dataclass(frozen=True)
class CharacterRecovery:
    pilot: PilotInventory
    before_data: bytes | None
    before_path: Path | None
    plan: RecoveryPlan
    sources: tuple[SavedDroneProfile,...]
    initializes_profile: bool

    @property
    def drone_count(self):
        quantities=dict(self.pilot.quantities)
        return sum(quantities[item] for group in self.plan.groups for item in group.drone_ids)


@dataclass(frozen=True)
class WorldRecovery:
    source_root: str
    characters: tuple[CharacterRecovery,...]


@dataclass(frozen=True)
class RecoveryScan:
    context: RecoveryContext
    database_identity: tuple[int,int]
    worlds: tuple[WorldRecovery,...]
    blocked_characters: tuple[int,...]


def _world(value):
    return os.path.normcase(str(Path(value).resolve()))


def _current(context, pilot, catalog):
    path=native.personal_settings_file(context.storage_root,context.scope,pilot.account_id,pilot.character_id)
    if path is not None:
        return native._read(context.storage_root.absolute(),path),path
    candidates=[item for item in catalog if item.world_root==_world(context.world_root)
                and item.account_id==pilot.account_id and pilot.character_id in item.active_for]
    if not candidates:
        return None,None
    newest=max(item.active_ns for item in candidates)
    candidates=[item for item in candidates if item.active_ns==newest]
    if len({item.sha256 for item in candidates})!=1:
        raise SharedSettingsError('The shared character settings could not be verified.')
    item=candidates[0]
    return item.data,item.path


def scan_recovery(context: RecoveryContext) -> RecoveryScan:
    """Read current facts once, then offer each older saved world as a batch."""
    info=context.database.stat()
    identity=(info.st_dev,info.st_ino)
    pilots=inventory_facts(context.database)
    catalog=saved_profiles(context.history_roots,context.scope,context.world_root,pilots,
                           target_identity=context.target_identity)
    current={}
    blocked=[]
    for pilot in pilots:
        try:
            current[pilot.character_id]=_current(context,pilot,catalog)
        except (OSError,ValueError,SharedSettingsError):
            blocked.append(pilot.character_id)
    worlds=[]
    for world in sorted({item.world_root for item in catalog}):
        proposals=[]
        for pilot in pilots:
            if pilot.character_id not in current or not pilot.owned_ids:
                continue
            before,path=current[pilot.character_id]
            sources=tuple(item for item in catalog if item.world_root==world and item.account_id==pilot.account_id)
            if not sources:
                continue
            try:
                history=[item.data for item in sources if codec.has_drone_settings(item.data)]
                positive=[]
                for item in sources:
                    if not codec.has_drone_settings(item.data):
                        continue
                    groups=native_memberships(item.data)
                    matching=set().union(*(set(members) for _,members in groups.values())) if groups else set()
                    score=len(matching.intersection(pilot.owned_ids))
                    if score:
                        positive.append((score,pilot.character_id in item.character_ids,item.modified_ns,item))
                if not positive:
                    continue
                best=max(positive,key=lambda entry:entry[:3])[3]
                seed=seed_missing_groups(before,best.data)
                if seed is None:
                    continue
                plan=plan_recovery(seed,history,pilot.owned_ids)
                if plan is None or not plan.groups:
                    continue
                # An ambiguous assignment remains outside the proposal; no item
                # is moved out of an existing group or guessed into another.
                proposals.append(CharacterRecovery(pilot,before,path,plan,sources,
                                                    before is None or seed!=before))
            except (ValueError,UnicodeError,RecursionError):
                blocked.append(pilot.character_id)
        worlds.append(WorldRecovery(world,tuple(proposals)))
    after=context.database.stat()
    if (after.st_dev,after.st_ino)!=identity:
        raise SharedSettingsError('The shared character settings could not be verified.')
    worlds.sort(key=lambda item:(-len(item.characters),-sum(p.drone_count for p in item.characters),
                                 item.source_root!=_world(context.world_root),item.source_root))
    return RecoveryScan(context,identity,tuple(worlds),tuple(sorted(set(blocked))))


def apply_recovery(scan: RecoveryScan, source_root: str) -> dict:
    """Stage the reviewed batch in generic personal slots; no old file is edited.

    The normal shared-profile launch reads these same stable pointers for every
    character. Native clients must be closed before changing that authority.
    """
    from . import platform
    from .overview_patch import is_eve_client_running
    from .shared_settings_profile import _EMPTY_SETTINGS, _replace_settings, _safe_storage, _json_bytes
    selected=next((world for world in scan.worlds if world.source_root==source_root),None)
    if selected is None or not selected.characters:
        raise RecoveryChangedError('No reviewed memberships are available.')
    context=scan.context
    root=context.storage_root.absolute()
    with platform.serialize_evejs_client_trust_and_spawn(timeout_seconds=15):
        if is_eve_client_running():
            raise SharedSettingsError('Close EVE clients before applying the selected shared character profile.')
        fresh=scan_recovery(context)
        if fresh.database_identity!=scan.database_identity:
            raise RecoveryChangedError('Saved data changed. Scan again before restoring groups.')
        updated=next((world for world in fresh.worlds if world.source_root==source_root),None)
        latest={row.pilot.character_id:row for row in updated.characters} if updated is not None else {}
        for row in selected.characters:
            current=latest.get(row.pilot.character_id)
            if (current is None or current.pilot!=row.pilot
                    or current.before_path!=row.before_path or current.before_data!=row.before_data
                    or current.plan.candidate!=row.plan.candidate):
                raise RecoveryChangedError('Saved data changed. Scan again before restoring groups.')
            for source in row.sources:
                if source.storage_root is None:
                    raise RecoveryChangedError('Saved profile authority is unavailable.')
                native._guard(source.storage_root,source.path)
                if hashlib.sha256(native._read(source.storage_root,source.path)).hexdigest()!=source.sha256:
                    raise RecoveryChangedError('Saved data changed. Scan again before restoring groups.')
        store=root/'native-drones'/hashlib.sha256(context.scope.encode('ascii')).hexdigest()
        native._guard(root,store)
        _safe_storage(store)
        changes={}
        for row in selected.characters:
            account,character=row.pilot.account_id,row.pilot.character_id
            native._check(context.scope,account,character)
            folder=store/'recovered'/f'{account}-{character}'
            native._guard(root,folder)
            _safe_storage(folder)
            marker={'schemaVersion':1,'storageIdentity':context.scope,'accountID':account,'characterID':character}
            user=folder/f'core_user_{account}.dat'
            char=folder/f'core_char_{character}.dat'
            owner=folder/'native-drone-profile.json'
            record=native._record_path(root,context.scope,account,character)
            for path in (user,char,owner,record):
                native._guard(root,path)
            if owner.exists() and json.loads(native._read(root,owner))!=marker:
                raise SharedSettingsError('The shared character settings could not be verified.')
            # These are data-only preference records, never a login profile or
            # an executable client path. The shared source still owns UI layout.
            changes[user]=row.plan.candidate
            changes[char]=_EMPTY_SETTINGS
            changes[owner]=_json_bytes(marker)
            changes[record]=_json_bytes({**marker,'folder':folder.relative_to(root).as_posix()})
        _replace_settings(changes,store)
        for row in selected.characters:
            data=native.read_personal_settings(root,context.scope,row.pilot.account_id,row.pilot.character_id)
            if data!=row.plan.candidate:
                raise SharedSettingsError('The shared character settings could not be verified.')
        return {'characters':len(selected.characters),
                'groups':sum(len(row.plan.groups) for row in selected.characters),
                'drones':sum(row.drone_count for row in selected.characters),
                'serverChanged':False,'historicalProfilesChanged':False}
