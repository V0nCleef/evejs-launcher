"""Optional EveJS compatibility, kept separate from mod SemVer and EVE builds."""
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re


class EvejsModCompatibilityStatus(str, Enum):
    """One active server mod's declared compatibility with the selected root."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNDECLARED = "undeclared"
    UNKNOWN_INSTALLED_VERSION = "unknown_installed_version"


@dataclass(frozen=True)
class EvejsModCompatibility:
    """Read-only compatibility result for one active runtime mod."""

    mod_id: str
    mod_name: str
    mod_version: str
    status: EvejsModCompatibilityStatus
    installed_version: str | None
    declared_versions: tuple[str, ...] | None
    message: str

    @property
    def blocking(self) -> bool:
        """Declared support that cannot be confirmed must stop this start."""

        return self.status in {
            EvejsModCompatibilityStatus.UNSUPPORTED,
            EvejsModCompatibilityStatus.UNKNOWN_INSTALLED_VERSION,
        }


def assess_active_runtime_mods(mods, installed_version):
    """Describe EveJS compatibility for active, applicable server mods.

    The caller may pass all mods returned by the runtime's backend-specific
    discovery. Disabled, invalid, and non-server packages are ignored. A
    missing declaration preserves the installer's existing behavior; an
    explicit declaration with an unknown installed version is blocking, just
    like :func:`supports_evejs` cannot confirm that combination.
    """
    from .mod_manifest import ActivationKind

    try:
        normalized_installed = normalize_evejs_version(installed_version)
    except (TypeError, ValueError):
        normalized_installed = None

    runtime_kinds = {ActivationKind.LOADER_RENAME, ActivationKind.JSON_BOOLEAN}
    results = []
    for mod in mods:
        if not mod.valid or not mod.active or mod.activation_kind not in runtime_kinds:
            continue

        descriptor = mod.api_descriptor
        declared_versions = (
            descriptor.evejs_versions if descriptor is not None else None
        )
        mod_name = mod.name
        mod_version = mod.version or ""
        label = f"{mod_name} (mod version {mod_version})" if mod_version else mod_name

        if declared_versions is None:
            status = EvejsModCompatibilityStatus.UNDECLARED
            message = (
                f"{label} does not declare EveJS compatibility; its existing "
                "startup behavior is retained."
            )
        elif normalized_installed is None:
            status = EvejsModCompatibilityStatus.UNKNOWN_INSTALLED_VERSION
            declared = ", ".join(declared_versions)
            message = (
                f"{label} declares support for EveJS {declared}, but the selected "
                "EveJS installation version could not be determined. Check its "
                "package.json and config/version.json, or disable this mod in "
                "the Mods page before starting Game."
            )
        elif supports_evejs(declared_versions, normalized_installed):
            status = EvejsModCompatibilityStatus.SUPPORTED
            message = f"{label} declares support for EveJS {normalized_installed}."
        else:
            status = EvejsModCompatibilityStatus.UNSUPPORTED
            declared = ", ".join(declared_versions)
            message = (
                f"{label} has an author manifest listing EveJS {declared} in "
                f"compatibility.evejsVersions, while the selected installation "
                f"is EveJS {normalized_installed}. That selected version is not "
                "listed. This reports declared support only; it is not a runtime "
                "test and does not prove the mod is broken or incompatible in practice."
            )

        results.append(
            EvejsModCompatibility(
                mod_id=mod.id,
                mod_name=mod_name,
                mod_version=mod_version,
                status=status,
                installed_version=normalized_installed,
                declared_versions=declared_versions,
                message=message,
            )
        )
    return tuple(results)


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
