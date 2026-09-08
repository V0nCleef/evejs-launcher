"""Captured mod settings sessions and contribution-aware persistence.

Opening a form is read-only. Saving uses the same key ownership and recovery
path as mod lifecycle helpers, and never reparses a changed author contract.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import os
from typing import Mapping

from .mod_config_documents import DocumentValue, read_value, values_equal
from .mod_contributions import ContributionConflict, ContributionOwner, ContributionStore, FileTarget, KeyEdit, read_target
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_contributions import ConfigurationReview, ContributionPlan
from .mod_settings_schema import ModSetting, ModSettingsSchema, SettingsFile, parse_settings_schema, validate_values


class ModSettingsError(RuntimeError):
    pass


def profile_identity(profile_root: Path) -> str:
    """Shared opaque identity for GUI settings and launch preparation."""
    return os.path.normcase(str(profile_root.resolve(strict=False)))


@dataclass(frozen=True)
class ModSettingsContext:
    evejs_root: Path
    mod_folder: Path
    client_root: Path | None = None
    profile_id: str = ""
    profile_root: Path | None = None
    profile_settings_root: Path | None = None
    profile_storage_root: Path | None = None
    profile_settings_storage_root: Path | None = None

    def __post_init__(self):
        root = self.evejs_root.resolve(strict=True)
        folder = self.mod_folder.resolve(strict=True)
        try:
            folder.relative_to(root)
        except ValueError as exc:
            raise ModSettingsError("The selected mod is outside its captured EveJS installation.") from exc
        object.__setattr__(self, "evejs_root", root)
        object.__setattr__(self, "mod_folder", folder)
        if self.client_root is not None:
            object.__setattr__(self, "client_root", self.client_root.resolve(strict=True))
        for name in ("profile_root", "profile_settings_root"):
            path = getattr(self, name)
            if path is not None:
                if not path.is_absolute() or os.path.normcase(str(path.resolve())) != os.path.normcase(str(path)):
                    raise ModSettingsError("Profile settings paths must not use redirected directories.")
        # These anchors are structural, never the nearest currently existing
        # parent. Creating a first config must not change its ownership contract.
        if self.profile_root is not None and self.profile_storage_root is None:
            object.__setattr__(self, "profile_storage_root", self.profile_root.parent.parent)
        if self.profile_settings_root is not None and self.profile_settings_storage_root is None:
            object.__setattr__(self, "profile_settings_storage_root", self.profile_settings_root.parent.parent)
        for name in ("profile_storage_root", "profile_settings_storage_root"):
            path = getattr(self, name)
            if path is not None:
                object.__setattr__(self, name, path.resolve(strict=True))

    @property
    def owner(self) -> ContributionOwner:
        root = self.evejs_root.resolve(strict=True)
        relative = self.mod_folder.resolve(strict=True).relative_to(root).as_posix()
        return ContributionOwner(root, relative, self.profile_id)

    @property
    def mod_data_root(self) -> Path | None:
        if self.profile_root is None:
            return None
        # Stable across mod versions, separate for equal IDs in distinct roots.
        global_owner = ContributionOwner(self.evejs_root, self.owner.mod_relative_path)
        return self.profile_root / "mods" / global_owner.id[:24]

    def target(self, file: SettingsFile) -> FileTarget:
        bases = {
            "evejs": self.evejs_root, "mod": self.mod_folder, "client": self.client_root,
            "profile": self.mod_data_root, "profile_settings": self.profile_settings_root,
        }
        base = bases.get(file.base)
        if base is None or (file.base.startswith("profile") and not self.profile_id):
            raise ModSettingsError("Select a profile and configure the required client paths first.")
        if not base.is_absolute():
            raise ModSettingsError("Settings context paths must be absolute.")
        anchors = {
            "evejs": self.evejs_root, "mod": self.evejs_root, "client": self.client_root,
            "profile": self.profile_storage_root, "profile_settings": self.profile_settings_storage_root,
        }
        return FileTarget.capture(base / file.path, anchors[file.base], file.format, encoding=file.encoding)

    def store_root(self, files: tuple[SettingsFile, ...]) -> Path:
        coordinators = {
            "client" if file.base in {"profile", "profile_settings", "client"} else "evejs"
            for file in files
        }
        if len(coordinators) > 1:
            raise ModSettingsError("Separate server and client settings into different forms before saving.")
        if "client" in coordinators:
            if self.client_root is None:
                raise ModSettingsError("Configure the physical EVE client path before editing client or profile settings.")
            return self.client_root.resolve(strict=True)
        return self.evejs_root.resolve(strict=True)


def _decode(field: ModSetting, stored: DocumentValue, format: str) -> object:
    if not stored.present:
        return field.default
    value = stored.value
    if field.storage == "numeric_boolean":
        if type(value) in (str, int) and value in ("0", 0, "1", 1):
            return value in ("1", 1)
        raise ModSettingsError(f"Setting {field.id} must contain 0 or 1.")
    if format != "ini" or field.kind == "string":
        return value
    if field.kind == "choice":
        for choice in field.choices:
            encoded = choice.value if isinstance(choice.value, str) else json.dumps(choice.value)
            if value == encoded:
                return choice.value
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def _encode(field: ModSetting, value: object) -> object:
    return int(value) if field.storage == "numeric_boolean" else value


@dataclass(frozen=True)
class ModSettingsSession:
    context: ModSettingsContext
    schema: ModSettingsSchema
    fields: tuple[ModSetting, ...]
    values: dict[str, object]
    targets: dict[str, FileTarget]
    observed: dict[str, DocumentValue]
    manifest_path: Path | None
    manifest_digest: str | None
    scope: str

    @classmethod
    def open(cls, context: ModSettingsContext, declaration: object, *, scope: str = "global", manifest_path: Path | None = None) -> "ModSettingsSession":
        if scope not in {"global", "profile"}:
            raise ModSettingsError("Select global or profile settings.")
        schema = parse_settings_schema(declaration)
        fields = tuple(field for field in schema.fields if field.scope == scope)
        if not fields:
            raise ModSettingsError("This mod exposes no settings for the selected scope.")
        files = tuple(file for file in schema.files if any(field.file_id == file.id for field in fields))
        context.store_root(files)  # validate coordination without creating state
        targets = {file.id: context.target(file) for file in files}
        content = {file_id: read_target(target) for file_id, target in targets.items()}
        observed = {
            field.id: read_value(content[field.file_id], targets[field.file_id].format, field.key, encoding=targets[field.file_id].encoding)
            for field in fields
        }
        values = {field.id: _decode(field, observed[field.id], targets[field.file_id].format) for field in fields}
        errors = validate_values(fields, values)
        if errors:
            raise ModSettingsError("\n".join(f"{key}: {error}" for key, error in errors.items()))
        digest = None
        if manifest_path is not None:
            manifest = FileTarget.capture(manifest_path, context.evejs_root, "json")
            manifest_bytes = read_target(manifest)
            if manifest_bytes is None:
                raise ModSettingsError("The mod settings declaration is no longer installed.")
            digest = hashlib.sha256(manifest_bytes).hexdigest()
        return cls(context, schema, fields, values, targets, observed, manifest_path, digest, scope)

    def _changed_fields(self, values: Mapping[str, object]) -> tuple[ModSetting, ...]:
        errors = validate_values(self.fields, values)
        if errors:
            raise ModSettingsError("\n".join(f"{key}: {error}" for key, error in errors.items()))
        return tuple(field for field in self.fields if type(values[field.id]) is not type(self.values[field.id]) or values[field.id] != self.values[field.id])

    def _store_and_owner(self):
        files = tuple(file for file in self.schema.files if file.id in self.targets)
        root = self.context.store_root(files)
        store = ContributionStore(root, allowed_roots={target.allowed_root for target in self.targets.values()})
        owner = self.context.owner
        if self.scope == "global":
            owner = ContributionOwner(owner.evejs_root, owner.mod_relative_path)
        return store, owner

    def _check_declaration(self):
        if self.manifest_path is not None:
            content = read_target(FileTarget.capture(self.manifest_path, self.context.evejs_root, "json"))
            if content is None or hashlib.sha256(content).hexdigest() != self.manifest_digest:
                raise ModSettingsError("The settings declaration changed. Reopen the form before saving.")

    def review_save(self, values: Mapping[str, object]) -> ConfigurationReview:
        """Stage explicit precedence choices; this never saves the draft."""
        changed = self._changed_fields(values)
        store, owner = self._store_and_owner()
        with acquire_mod_lifecycle_lock(store.root):
            self._check_declaration()
            edits = [KeyEdit(self.targets[field.file_id], field.key, _encode(field, values[field.id]), allow_override=True)
                     for field in changed]
            apply = store.plan_edits_locked(owner, edits)
            preserve = ContributionPlan(apply.storage_root, tuple(replace(file, after=file.before) for file in apply.files),
                                        apply.index_before, apply.index_before)
            owners = tuple(dict.fromkeys(item for file in apply.files for item in store.owners_for_path(file.target.path)))
            return ConfigurationReview(preserve, apply, owners, "settings")

    def apply_review(self, review: ConfigurationReview, plan: ContributionPlan) -> "ModSettingsSession":
        if review.kind != "settings" or plan not in (review.preserve, review.restore):
            raise ModSettingsError("The selected settings review is invalid.")
        store, _owner = self._store_and_owner()
        if any(file.target not in self.targets.values() for file in plan.files):
            raise ModSettingsError("The settings review contains an unrelated file.")
        with acquire_mod_lifecycle_lock(store.root):
            self._check_declaration()
            store.commit_locked(plan)
            if any(read_target(file.target) != file.after for file in plan.files):
                raise ModSettingsError("The saved settings changed during verification. Refresh before retrying.")
            return self._readback()

    def save(self, values: Mapping[str, object]) -> "ModSettingsSession":
        changed = self._changed_fields(values)
        if not changed:
            return self
        store, owner = self._store_and_owner()
        with acquire_mod_lifecycle_lock(store.root):
            self._check_declaration()
            edits = []
            for field in changed:
                target = self.targets[field.file_id]
                current = read_value(read_target(target), target.format, field.key, encoding=target.encoding)
                if not values_equal(current, self.observed[field.id]):
                    raise ContributionConflict(f"Setting {field.id} changed outside this form. Reopen it before saving.")
                edits.append(KeyEdit(target, field.key, _encode(field, values[field.id])))
            plan = store.plan_edits_locked(owner, edits)
            store.commit_locked(plan)
        return self._readback(values, changed)

    def _readback(self, expected=None, changed=()) -> "ModSettingsSession":
        # Read back through the original declaration. A failure is surfaced;
        # it must not be described as a successful save of unverified values.
        observed = {}
        verified_values = {}
        for field in self.fields:
            target = self.targets[field.file_id]
            observed[field.id] = read_value(read_target(target), target.format, field.key, encoding=target.encoding)
            verified_values[field.id] = _decode(field, observed[field.id], target.format)
        if expected is not None and any(type(verified_values[field.id]) is not type(expected[field.id]) or verified_values[field.id] != expected[field.id] for field in changed):
            raise ModSettingsError("The saved settings changed during verification. Refresh before retrying.")
        errors = validate_values(self.fields, verified_values)
        if errors:
            raise ModSettingsError("\n".join(f"{key}: {error}" for key, error in errors.items()))
        return ModSettingsSession(self.context, self.schema, self.fields, verified_values, self.targets, observed, self.manifest_path, self.manifest_digest, self.scope)
