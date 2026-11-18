"""Select the copied client's renderer without executing EveJS batch files."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from pathlib import Path

from .discovery import _find_batch_assignment, _read_batch_text


_VARIABLES = ("EVEJS_CLIENT_RENDERER", "TRINITYPLATFORM")
_CONFIG_PATHS = (
    "tools/ClientSETUP/scripts/EvEJSConfig.bat",
    "scripts/windows/EvEJSConfig.bat",
    "scripts/EvEJSConfig.bat",
    "EvEJSConfig.bat",
)


@dataclass(frozen=True)
class ClientRenderer:
    requested: str
    effective: str
    source: str
    fallback: str = ""


def _saved_settings(root: Path) -> dict[str, str]:
    for relative in _CONFIG_PATHS:
        config = root / relative
        if not config.is_file():
            continue
        contents = _read_batch_text(config)
        if contents is None:
            raise ValueError("Cannot read EveJS renderer preferences in EvEJSConfig.bat.")
        # Batch files apply later assignments last. Only literal SET lines are
        # read; comments, commands, conditionals and expansions are not executed.
        lines = [line.lstrip().removeprefix("@") for line in contents.splitlines()]
        reversed_lines = "\n".join(reversed(lines))
        settings = {}
        for variable in _VARIABLES:
            value = _find_batch_assignment(reversed_lines, variable)
            if value is not None:
                settings[variable] = value
        return settings
    return {}


def resolve_client_renderer(
    evejs_root: str | Path,
    binary_directory: Path,
    environment: Mapping[str, str],
    *,
    override: str | None = None,
    override_source: str = "client mod",
) -> ClientRenderer:
    """Mirror Play.bat's saved/inherited preference and missing-DX12 fallback.

    An already installed graphics integration can supply its existing explicit
    override. That path remains independent of server configuration and journals.
    No config, client binary or saved profile is written by this resolver.
    """
    if override is not None:
        requested = override.strip().casefold()
        source = override_source
    else:
        values = {key.upper(): value for key, value in environment.items()
                  if key.upper() in _VARIABLES}
        sources = {key: f"environment:{key}" for key in values}
        for key, value in _saved_settings(Path(evejs_root)).items():
            values[key] = value
            sources[key] = f"EvEJSConfig.bat:{key}"
        variable = next((key for key in _VARIABLES if values.get(key, "").strip()), None)
        requested = values[variable].strip().casefold() if variable else "dx11"
        source = sources[variable] if variable else "default"

    if requested not in {"dx11", "dx12"}:
        # Avoid copying arbitrary batch values or helper output into diagnostics.
        raise ValueError("The EVE client renderer must be dx11 or dx12. Check EvEJSConfig.bat and renderer overrides.")
    if requested == "dx12" and not (binary_directory / "_trinity_dx12.dll").is_file():
        return ClientRenderer(
            requested, "dx11", source,
            "DX12 is unavailable: _trinity_dx12.dll is missing; using DX11 instead.",
        )
    return ClientRenderer(requested, requested, source)


def configure_client_renderer(
    evejs_root: str | Path,
    binary_directory: Path,
    environment: dict[str, str],
    *,
    override: str | None = None,
    override_source: str = "client mod",
) -> ClientRenderer:
    """Apply the same choice to the Windows or Proton child environment."""
    renderer = resolve_client_renderer(
        evejs_root, binary_directory, environment,
        override=override, override_source=override_source,
    )
    # Wine and Windows consume one canonical key, including when a mod used
    # different casing. Do not mutate the launcher's own process environment.
    for key in tuple(environment):
        if key.upper() == "TRINITYPLATFORM":
            del environment[key]
    environment["TRINITYPLATFORM"] = renderer.effective
    log = logging.getLogger(__name__)
    log.info(
        "EVE renderer requested=%s effective=%s source=%s",
        renderer.requested, renderer.effective, renderer.source,
    )
    if renderer.fallback:
        log.warning("%s", renderer.fallback)
    return renderer
