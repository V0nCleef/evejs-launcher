"""Stable, root-scoped dependency and preload ordering for public mods."""
from __future__ import annotations

from dataclasses import dataclass, replace
import heapq
from pathlib import Path
from typing import Iterable

from .mod_manifest import ActivationKind, Mod, scan_mods


@dataclass(frozen=True)
class RelationshipIssue:
    paths: tuple[Path, ...]
    message: str


@dataclass(frozen=True)
class ModOrderPlan:
    mods: tuple[Mod, ...]
    issues: tuple[RelationshipIssue, ...]

    def require_valid(self, relevant: Iterable[Mod] | None = None) -> tuple[Mod, ...]:
        paths = None if relevant is None else {mod.path for mod in relevant}
        errors = [issue.message for issue in self.issues
                  if paths is None or paths.intersection(issue.paths)]
        if errors:
            raise ValueError("\n".join(dict.fromkeys(errors)))
        return self.mods


def plan_mod_order(mods: Iterable[Mod], *, backend: str | None = None) -> ModOrderPlan:
    """Preserve user/discovery order except where active loaders declare edges.

    Optional ordering references to absent/disabled mods are ignored. Required
    IDs must resolve to exactly one enabled, valid mod on the selected backend.
    Duplicate IDs are otherwise permitted; a reference must not guess a folder.
    """
    mods = tuple(mods)
    roots = {mod.evejs_root.resolve() for mod in mods if mod.evejs_root is not None}
    if len(roots) > 1:
        raise ValueError("A mod plan cannot combine different EveJS installations.")
    identities = {}
    for index, mod in enumerate(mods):
        identities.setdefault(mod.id.casefold(), []).append(index)
    active = {i for i, mod in enumerate(mods)
              if mod.active and mod.valid and (backend is None or backend in mod.supported_backends)}
    edges = [set() for _ in mods]
    issues = []

    def issue(indices, message):
        item = RelationshipIssue(tuple(dict.fromkeys(mods[i].path for i in indices)), message)
        if item not in issues:
            issues.append(item)

    for index in sorted(active):
        mod = mods[index]
        descriptor = mod.api_descriptor
        if descriptor is None:
            continue
        for kind, references in (("requires", descriptor.requires), ("before", descriptor.load_before),
                                 ("after", descriptor.load_after), ("conflicts", descriptor.conflicts)):
            for reference in references:
                matches = identities.get(reference.casefold(), [])
                if len(matches) > 1:
                    issue([index, *matches], f"{mod.name}: '{reference}' identifies multiple mod folders. Use unique declared IDs.")
                    continue
                target = matches[0] if matches else None
                if target not in active:
                    if kind == "requires":
                        issue([index, *matches], f"{mod.name} requires '{reference}' to be installed and enabled for this backend.")
                    continue
                other = mods[target]
                if kind == "conflicts":
                    issue([index, target], f"{mod.name} conflicts with {other.name}. Disable one of these mods.")
                    continue
                loaders = mod.activation_kind is ActivationKind.LOADER_RENAME and other.activation_kind is ActivationKind.LOADER_RENAME
                if kind in {"before", "after"} and not loaders:
                    issue([index, target], f"{mod.name}: preload ordering cannot change {other.name}'s integrated initialization order.")
                    continue
                if loaders:
                    source, destination = (index, target) if kind == "before" else (target, index)
                    edges[source].add(destination)
    indegree = [0] * len(mods)
    for destinations in edges:
        for target in destinations:
            indegree[target] += 1
    ready = [i for i, count in enumerate(indegree) if count == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        index = heapq.heappop(ready)
        ordered.append(index)
        for target in sorted(edges[index]):
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, target)
    if len(ordered) != len(mods):
        remaining = [i for i, count in enumerate(indegree) if count]
        issue(remaining, "Mod ordering contains a cycle or depends on one: " + ", ".join(mods[i].name for i in remaining) + ". Review requires/loadBefore/loadAfter.")
        ordered = list(range(len(mods)))
    return ModOrderPlan(tuple(mods[i] for i in ordered), tuple(issues))


def validate_mod_change(mod: Mod, enabled: bool) -> None:
    """Called under the root lease before activation or removal writes."""
    if mod.evejs_root is None:
        raise ValueError("The mod is not bound to an EveJS installation.")
    proposed = [replace(item, active=enabled) if item.path == mod.path else item
                for item in scan_mods(mod.evejs_root)]
    plan_mod_order(proposed).require_valid([mod])
