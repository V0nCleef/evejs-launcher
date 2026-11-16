"""Data-only recovery of native drone membership, independent of pilot names.

The caller supplies same-account snapshots and authoritative owned drone IDs for
the selected world. Names and drone types are never evidence for assignment.
Plans are reviewed before they are staged; this module does not write files.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import struct
from typing import Iterable

from . import shared_drone_settings as codec


@dataclass(frozen=True)
class RestoredGroup:
    name: str
    identity: bytes
    drone_ids: tuple[int, ...]
    source_hashes: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryPlan:
    before_sha256: str
    before_drone_signature: str
    candidate: bytes
    groups: tuple[RestoredGroup, ...]
    ambiguous_ids: tuple[int, ...]

    @property
    def restored_count(self) -> int:
        return sum(len(group.drone_ids) for group in self.groups)


@dataclass
class _Group:
    name: str
    identity: bytes
    value: codec.Value
    members: codec.Value
    by_id: dict[int, codec.Value]


def _integer(node: codec.Value) -> int:
    if node.op == 0x2f and 1 <= len(node.raw) <= 8:
        result = int.from_bytes(node.raw, 'little', signed=True)
    elif node.op in (3, 4, 5, 6):
        result = struct.unpack({3: '<q', 4: '<i', 5: '<h', 6: '<b'}[node.op], node.raw)[0]
    elif node.op in (7, 8, 9):
        result = {7: -1, 8: 0, 9: 1}[node.op]
    else:
        raise codec.SettingsFormatError('Unsupported native drone identifier')
    if not 0 < result < 2**63:
        raise codec.SettingsFormatError('Invalid native drone identifier')
    return result


def _groups(root: codec.Value) -> dict[bytes, _Group]:
    ui = codec.get(root, 'ui')
    if ui is None:
        return {}
    setting = codec.get(ui, codec.GROUPS)
    if setting is None:
        return {}
    if setting.op not in (0x14, 0x2c) or len(setting.children) != 2:
        raise codec.SettingsFormatError('Invalid timestamped native drone groups')
    groups = {}
    for key, value in codec.entries(setting.children[1]):
        name = key.text()
        if name is None or len(name) > 1024:
            raise codec.SettingsFormatError('Invalid native drone group name')
        identity = codec.get(value, 'id')
        members = codec.get(value, 'droneIDs')
        if (identity is None or identity.op not in (0x14, 0x2c)
                or len(identity.children) != 2 or members is None or members.op != 0x15):
            raise codec.SettingsFormatError('Invalid native drone group')
        atoms = []
        for child in identity.children:
            label = child.text()
            if label is not None:
                if len(label) > 1024:
                    raise codec.SettingsFormatError('Native drone group identity is too long')
                atoms.append(('text', label))
            else:
                atoms.append(('integer', _integer(child)))
        encoded = json.dumps(atoms,ensure_ascii=True,separators=(',', ':')).encode('ascii')
        if encoded in groups:
            raise codec.SettingsFormatError('Conflicting native drone group identities')
        by_id = {_integer(node): node for node in members.children}
        if len(by_id) != len(members.children):
            raise codec.SettingsFormatError('Duplicate native drone membership')
        groups[encoded] = _Group(name, encoded, value, members, by_id)
    return groups


def native_memberships(data: bytes) -> dict[bytes, tuple[str, frozenset[int]]]:
    """Return bounded native facts without executing objects from a DAT file."""
    return {key: (group.name, frozenset(group.by_id))
            for key, group in _groups(codec.loads(data)).items()}


def seed_missing_groups(current: bytes | None, historical: bytes) -> bytes | None:
    """Prepare definitions for review when no personal groups were initialized.

    Explicitly empty groups remain authoritative. Historical drone memberships
    are cleared here; plan_recovery must verify every restored owned item.
    """
    root=codec.loads(current if current is not None else bytes.fromhex('7e000000001600'))
    ui=codec.get(root,'ui')
    if ui is not None and codec.get(ui,codec.GROUPS) is not None:
        return current if _groups(root) else None
    prior=codec.loads(historical)
    prior_ui=codec.get(prior,'ui')
    setting=codec.get(prior_ui,codec.GROUPS) if prior_ui is not None else None
    if setting is None or not _groups(prior):
        return None
    copied=deepcopy(setting)
    group_ui=codec.Value(0x16,children=[copied,codec.Value(0x2e,codec.GROUPS.encode())])
    temporary=codec.Value(0x16,children=[group_ui,codec.Value(0x2e,b'ui')])
    for group in _groups(temporary).values():
        group.members.children=[]
    if ui is None:
        ui=codec.Value(0x16)
        root.children.extend([ui,codec.Value(0x2e,b'ui')])
    ui.children.extend([copied,codec.Value(0x2e,codec.GROUPS.encode())])
    return codec.dumps(root)


def plan_recovery(current: bytes, history: Iterable[bytes],
                  owned_drone_ids: Iterable[int]) -> RecoveryPlan | None:
    """Restore only agreed historical assignments to existing native group IDs.

    Explicitly empty groups and current assignments are authoritative. History
    cannot move a drone, create a group, or assign a foreign/unknown item. A
    conflicting historical assignment is reported rather than guessed.
    """
    identifiers = tuple(owned_drone_ids)
    if any(type(item) is not int or not 0 < item < 2**63 for item in identifiers):
        raise ValueError('Owned drone identifiers must be positive integers')
    owned = frozenset(identifiers)
    if not owned:
        return None
    root = codec.loads(current)
    groups = _groups(root)
    if not groups:
        return None
    assigned = set().union(*(set(group.by_id) for group in groups.values()))
    missing = owned - assigned
    if not missing:
        return None
    facts: dict[int, dict[bytes, tuple[codec.Value, set[str]]]] = {}
    seen = set()
    for index, data in enumerate(history):
        if index >= 1024:
            raise codec.SettingsFormatError('Too many native preference snapshots')
        digest = hashlib.sha256(data).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        historical = _groups(codec.loads(data))
        for identity, group in historical.items():
            # Deleted/recreated groups are different native identities. Their
            # old membership cannot authorize or veto an assignment to a group
            # the player currently retains, even when the labels look alike.
            if identity not in groups:
                continue
            for item in missing.intersection(group.by_id):
                assignments = facts.setdefault(item, {})
                if identity not in assignments:
                    assignments[identity] = (group.by_id[item], set())
                assignments[identity][1].add(digest)
    added: dict[bytes, list[int]] = {}
    provenance: dict[bytes, set[str]] = {}
    ambiguous = []
    for item, assignments in sorted(facts.items()):
        known = set(assignments).intersection(groups)
        if not known:
            continue
        if len(assignments) != 1:
            ambiguous.append(item)
            continue
        identity = next(iter(known))
        node, hashes = assignments[identity]
        groups[identity].members.children.append(node)
        added.setdefault(identity, []).append(item)
        provenance.setdefault(identity, set()).update(hashes)
    if not added and not ambiguous:
        return None
    candidate = codec.dumps(root) if added else current
    # Verify the full preference tree, including every unrelated UI leaf.
    check = codec.loads(candidate)
    checked_groups = _groups(check)
    for identity, identifiers in added.items():
        members = checked_groups[identity].members
        members.children = [node for node in members.children if _integer(node) not in identifiers]
    if codec.dumps(check) != codec.dumps(codec.loads(current)):
        raise codec.SettingsFormatError('Unrelated preferences changed during recovery')
    changes = tuple(RestoredGroup(groups[identity].name, identity, tuple(identifiers),
                                  tuple(sorted(provenance[identity])))
                    for identity, identifiers in sorted(added.items()))
    return RecoveryPlan(hashlib.sha256(current).hexdigest(), codec.signature(current),
                        candidate, changes, tuple(ambiguous))
