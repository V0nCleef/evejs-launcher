"""Runtime-selected Native and Docker character data providers.

Docker volume data is read only through the shipped, allowlisted Config Editor
export command.  A verified host bind is the sole Docker path allowed to use
the existing SQLite reader.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import math
from pathlib import Path, PureWindowsPath
from typing import Callable, Mapping, Protocol

from src.core import db
from src.core.db import Account, Character
from src.core.runtime.docker_cli import (
    DockerCommandError,
    DockerCommandResult,
    DockerCommandRunner,
)
from src.core.runtime.docker_compose import (
    ComposeConfig,
    ComposeInspector,
    ComposeTarget,
    ContainerRecord,
    RuntimeEndpoints,
    resolve_mount,
)
from src.core.service_status import DockerControlPolicy


_GAME_STORE_TARGET = "/var/lib/evejs/gameStore"
_CONFIG_PROXY_ENV = "EVEJS_CONFIG_CLI_DOCKER_PROXY=1"
_CONFIG_CLI_PATH = "/app/tools/ConfigEditor/config-manager-cli.js"
_EXPORT_COMMAND = "database-export"
_DEFAULT_TIMEOUT = 20.0
_DEFAULT_OUTPUT_LIMIT = 8 * 1024 * 1024
_MAX_PLAYERS = 10_000
_MAX_TEXT_LENGTH = 256
# Bounded number of stdout positions probed for the export document before the
# response is treated as malformed.
_MAX_DOCUMENT_OFFSETS = 32


log = logging.getLogger(__name__)


class DataSourceError(RuntimeError):
    """Private-safe runtime data failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message[:160])


class RuntimeDataSource(Protocol):
    """Backend-neutral account and character-detail interface."""

    def load_accounts(self) -> list[Account]:
        """Return every account and its characters."""

    def get_character_detail(self, char_id: int) -> dict | None:
        """Return one backend-neutral character detail object."""


@dataclass(frozen=True)
class RuntimeDataSelection:
    """Data provider plus target-specific endpoints and cache identity."""

    data_source: RuntimeDataSource
    endpoints: RuntimeEndpoints | None
    target_identity: str
    settings_identity: str | None = None
    monitor_generation: int | None = None


class NativeDataSource:
    """Preserve the launcher's existing host ``_local`` SQLite behavior."""

    def __init__(
        self,
        evejs_root: str,
        *,
        accounts_loader: Callable[[str], list[Account]] | None = None,
        detail_loader: Callable[[str, int], dict | None] | None = None,
    ) -> None:
        self.evejs_root = evejs_root
        self._accounts_loader = accounts_loader or db.load_accounts
        self._detail_loader = detail_loader or db.get_character_detail

    def load_accounts(self) -> list[Account]:
        return self._accounts_loader(self.evejs_root)

    def get_character_detail(self, char_id: int) -> dict | None:
        return self._detail_loader(self.evejs_root, char_id)


class SqliteGameStoreDataSource:
    """Read a verified Docker gameStore bind through the native SQLite model."""

    def __init__(self, game_store_path: Path) -> None:
        self.game_store_path = game_store_path.resolve()

    def load_accounts(self) -> list[Account]:
        return db.load_accounts_from_game_store(self.game_store_path)

    def get_character_detail(self, char_id: int) -> dict | None:
        return db.get_character_detail_from_game_store(self.game_store_path, char_id)


class DockerExportDataSource:
    """Read volume-backed Docker data through one exact CLI export contract."""

    def __init__(
        self,
        target: ComposeTarget,
        server_record: ContainerRecord,
        runner: object,
        target_identity: str,
        *,
        control_policy: DockerControlPolicy,
        timeout: float = _DEFAULT_TIMEOUT,
        output_limit: int = _DEFAULT_OUTPUT_LIMIT,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if output_limit <= 0:
            raise ValueError("output_limit must be positive")
        self.target = target
        self.server_record = server_record
        self.runner = runner
        self.target_identity = target_identity
        self.control_policy = control_policy
        self.timeout = float(timeout)
        self.output_limit = int(output_limit)

    def load_accounts(self) -> list[Account]:
        payload = self._load_export(None)
        return _map_export_accounts(payload)

    def get_character_detail(self, char_id: int) -> dict | None:
        requested_id = _positive_id(char_id)
        payload = self._load_export(requested_id)
        return _map_export_detail(payload, requested_id)

    def _load_export(self, char_id: int | None) -> Mapping[str, object]:
        command = self._command(char_id)
        args = self.target.compose_args(self.runner.executable, *command)
        self._assert_allowlisted(args, char_id)
        try:
            result = self.runner.run(
                args,
                cwd=self.target.project_directory,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise DataSourceError(
                "docker_cli_unavailable",
                "Docker character data is unavailable because the Docker CLI was not found.",
            ) from exc
        except DockerCommandError as exc:
            raise _command_failure(exc) from exc
        except OSError as exc:
            raise DataSourceError(
                "docker_cli_unavailable",
                "Docker character data is unavailable because the Docker CLI could not start.",
            ) from exc

        if not isinstance(result, DockerCommandResult):
            raise DataSourceError(
                "export_failed",
                "Docker character export returned an unsupported command result.",
            )
        if not result.ok:
            raise DataSourceError(
                "export_failed",
                "Docker character export did not complete successfully.",
            )
        if result.truncated or len(result.stdout.encode("utf-8")) > self.output_limit:
            raise DataSourceError(
                "export_too_large",
                "Docker character export exceeded the safe response limit.",
            )
        return _decode_export_payload(result.stdout)

    def _command(self, char_id: int | None) -> tuple[str, ...]:
        raw_state = (self.server_record.raw_state or "").casefold()
        if not self.server_record.exists:
            raise DataSourceError(
                "container_unavailable",
                "Docker character data is unavailable until the Server container exists.",
            )
        if raw_state == "running":
            prefix = ("exec", "-T")
        elif raw_state in {"created", "exited", "dead"}:
            if self.control_policy is DockerControlPolicy.CONNECT_ONLY:
                raise DataSourceError(
                    "connect_only_stopped",
                    "Docker character data requires a running Server container in Connect-only mode.",
                )
            prefix = ("run", "--rm", "--no-deps", "-T")
        else:
            raise DataSourceError(
                "container_busy",
                "Docker character data is unavailable while the Server container changes state.",
            )
        command = (
            *prefix,
            "-e",
            _CONFIG_PROXY_ENV,
            "server",
            "node",
            _CONFIG_CLI_PATH,
            _EXPORT_COMMAND,
        )
        return command if char_id is None else (*command, str(char_id))

    def _assert_allowlisted(
        self,
        args: tuple[str, ...],
        char_id: int | None,
    ) -> None:
        expected = self.target.compose_args(
            self.runner.executable,
            *self._command(char_id),
        )
        if args != expected:
            raise RuntimeError("Docker data command is outside the exact export allowlist.")


def native_data_selection(
    evejs_root: str,
    *,
    accounts_loader: Callable[[str], list[Account]] | None = None,
    detail_loader: Callable[[str, int], dict | None] | None = None,
) -> RuntimeDataSelection:
    """Create the unchanged Native provider with a root-specific cache identity."""
    return RuntimeDataSelection(
        NativeDataSource(
            evejs_root,
            accounts_loader=accounts_loader,
            detail_loader=detail_loader,
        ),
        None,
        _target_digest("native", evejs_root),
    )


def select_docker_data_source(
    target: ComposeTarget,
    config: ComposeConfig,
    records: Mapping[str, ContainerRecord],
    runner: object,
    *,
    control_policy: DockerControlPolicy,
    settings_identity: str | None = None,
    monitor_generation: int | None = None,
) -> RuntimeDataSelection:
    """Select the authoritative bind or in-container Docker data contract."""
    server = config.services.get("server")
    if server is None:
        raise DataSourceError(
            "unsupported_layout",
            "The selected Docker project has no supported Server data layout.",
        )
    try:
        mount = resolve_mount(server.mounts, _GAME_STORE_TARGET)
    except ValueError as exc:
        raise DataSourceError(
            "unsupported_layout",
            "The selected Docker project has an ambiguous Server data layout.",
        ) from exc
    if mount is None:
        raise DataSourceError(
            "unsupported_layout",
            "The selected Docker project does not expose a supported Server data layout.",
        )

    identity = docker_project_identity(target, config.project_name, config=config)
    if mount.type.casefold() == "bind":
        game_store_path = _resolve_bind_source(target, mount.source)
        if not (game_store_path / "gamestore.sqlite").is_file():
            raise DataSourceError(
                "bind_unavailable",
                "The selected Docker project's authoritative game data is not initialized.",
            )
        source: RuntimeDataSource = SqliteGameStoreDataSource(game_store_path)
    elif mount.type.casefold() == "volume":
        source = DockerExportDataSource(
            target,
            records.get("server", ContainerRecord.absent("server")),
            runner,
            identity,
            control_policy=control_policy,
        )
    else:
        raise DataSourceError(
            "unsupported_layout",
            "The selected Docker project uses an unsupported Server data mount.",
        )
    return RuntimeDataSelection(
        source,
        config.endpoints,
        identity,
        settings_identity,
        monitor_generation,
    )


def inspect_docker_data_source(
    target: ComposeTarget,
    *,
    control_policy: DockerControlPolicy,
    settings_identity: str | None = None,
    monitor_generation: int | None = None,
    runner_factory: Callable[[], object] | None = None,
    inspector_factory: Callable[[object], object] | None = None,
) -> RuntimeDataSelection:
    """Inspect and select Docker data entirely within the caller's worker."""
    make_runner = runner_factory or (
        lambda: DockerCommandRunner(output_limit=_DEFAULT_OUTPUT_LIMIT)
    )
    make_inspector = inspector_factory or ComposeInspector
    try:
        runner = make_runner()
        inspector = make_inspector(runner)
        report = inspector.preflight(target)
    except FileNotFoundError as exc:
        raise DataSourceError(
            "docker_cli_unavailable",
            "Docker character data is unavailable because the Docker CLI was not found.",
        ) from exc
    except (OSError, TypeError, ValueError) as exc:
        raise DataSourceError(
            "docker_preflight_failed",
            "Docker character data could not be inspected safely.",
        ) from exc

    if not report.ok or report.config is None or report.records is None:
        raise DataSourceError(
            "docker_preflight_failed",
            "The selected Docker project did not pass safe character-data inspection.",
        )
    return select_docker_data_source(
        target,
        report.config,
        report.records,
        runner,
        control_policy=control_policy,
        settings_identity=settings_identity,
        monitor_generation=monitor_generation,
    )


def docker_project_identity(
    target: ComposeTarget,
    effective_project_name: str | None,
    *,
    config: ComposeConfig | None = None,
) -> str:
    """Return a private-safe identity for the exact effective Compose target.

    Mutating workers must be invalidated when either the ordered file chain or
    any Compose file changes in place.  Hashing the bytes here keeps paths and
    configuration private while letting a fresh preflight detect drift from a
    monitor observation made against older content.
    """
    compose_files = (target.compose_file, *target.override_files)
    compose_material: list[str] = []
    for path in compose_files:
        try:
            content_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            content_digest = "unreadable"
        compose_material.extend((str(path), content_digest))
    effective_material = ""
    if isinstance(config, ComposeConfig):
        services = []
        for service_name in sorted(config.services):
            service = config.services[service_name]
            services.append(
                {
                    "name": service_name,
                    "image": service.image,
                    "pullPolicy": service.pull_policy,
                    "healthcheck": service.has_healthcheck,
                    "dependencies": sorted(service.dependencies),
                    "stopGracePeriod": service.stop_grace_period,
                    "mounts": [
                        {
                            "type": mount.type,
                            "source": mount.source,
                            "target": mount.target,
                        }
                        for mount in service.mounts
                    ],
                }
            )
        endpoints = (
            {
                name: {
                    "service": endpoint.service,
                    "host": endpoint.host,
                    "port": endpoint.port,
                    "target": endpoint.target,
                    "protocol": endpoint.protocol,
                }
                for name, endpoint in sorted(vars(config.endpoints).items())
            }
            if isinstance(config.endpoints, RuntimeEndpoints)
            else {}
        )
        effective_material = json.dumps(
            {
                "services": services,
                "endpoints": endpoints,
                "capabilities": {
                    "init": config.capabilities.init,
                    "marketTools": config.capabilities.market_tools,
                },
                "effectiveConfigDigest": config.effective_config_digest,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    return _target_digest(
        "docker",
        str(target.project_directory),
        target.project_name or "",
        effective_project_name or "",
        effective_material,
        *compose_material,
    )


def docker_settings_identity(
    project_root: str,
    compose_file: str,
    project_name: str | None,
) -> str:
    """Return a private-safe identity for the selected monitor settings."""
    return _target_digest(
        "docker-settings",
        str(project_root),
        str(compose_file),
        project_name or "",
    )


def _target_digest(kind: str, *parts: str) -> str:
    material = "\0".join((kind, *parts)).encode("utf-8", errors="surrogatepass")
    return f"{kind}:{hashlib.sha256(material).hexdigest()}"


def _resolve_bind_source(target: ComposeTarget, source: str) -> Path:
    candidate = Path(source)
    if not candidate.is_absolute() and not PureWindowsPath(source).is_absolute():
        candidate = target.project_directory / candidate
    return candidate.resolve()


def _command_failure(error: DockerCommandError) -> DataSourceError:
    result = error.result
    if result.timed_out:
        return DataSourceError(
            "export_timeout",
            "Docker character export timed out before returning safe data.",
        )
    if result.returncode == 127:
        return DataSourceError(
            "export_unsupported",
            "The selected EveJS image does not support character export.",
        )
    if result.returncode is None:
        return DataSourceError(
            "docker_cli_unavailable",
            "Docker character data is unavailable because the Docker CLI could not start.",
        )
    return DataSourceError(
        "export_failed",
        "Docker character export did not complete successfully.",
    )


def _document_offsets(stdout: str) -> list[int]:
    """Return bounded candidate start positions for the export document."""
    offsets: list[int] = []
    position = 0
    while position <= len(stdout) and len(offsets) < _MAX_DOCUMENT_OFFSETS:
        if stdout.startswith("{", position):
            offsets.append(position)
        newline = stdout.find("\n", position)
        if newline < 0:
            break
        position = newline + 1
        while stdout.startswith((" ", "\t", "\r"), position):
            position += 1
    return offsets


def _decode_export_payload(stdout: str) -> Mapping[str, object]:
    """Decode the export document from a stream that may carry other output.

    Anything the container loads before the CLI — a mod preload hook attached
    through ``NODE_OPTIONS``, a Node deprecation warning — writes to the same
    stdout the export uses.  The document is the first complete JSON object
    that carries the export's own player collection, so decode from there
    instead of requiring the whole buffer to be the payload.
    """
    decoder = json.JSONDecoder()
    for offset in _document_offsets(stdout):
        try:
            payload, _end = decoder.raw_decode(stdout, offset)
        except (ValueError, UnicodeError):
            continue
        if isinstance(payload, Mapping) and "players" in payload:
            return payload
    raise DataSourceError(
        "malformed_export",
        "Docker character export returned malformed JSON.",
    )


def _parse_export_player(
    player: object,
) -> tuple[str, int, str, bool, Character] | None:
    """Map one exported record, or ``None`` when it is not a live character.

    A character biomassed in game keeps its row with ``accountId`` cleared, so
    the export still lists it under a placeholder account.  The Native SQLite
    reader skips exactly those rows; skipping them here holds both readers to
    one contract instead of letting a retired pilot invalidate the roster.
    """
    if not isinstance(player, Mapping) or player.get("accountId") is None:
        return None
    try:
        char_id = _positive_id(player.get("characterId"))
        username = _required_text(player.get("accountName"))
        account_id = _nonnegative_int(player.get("accountId"))
        banned = _strict_bool(player.get("banned"))
        role = "gm" if _strict_bool(player.get("isGM")) else "0"
        character = Character(
            char_id=char_id,
            name=_required_text(player.get("characterName")),
            isk=_number_as_int(player.get("balance", 0)),
            skill_points=_number_as_int(player.get("skillPoints", 0)),
            ship_name=_optional_text(player.get("shipName"), "—"),
            ship_type_id=_number_as_int(player.get("shipTypeID", 0)),
            location=_optional_text(player.get("solarSystemName"), "—"),
            security_status=_finite_float(player.get("securityStatus", 0.0)),
        )
    except (TypeError, ValueError, OverflowError):
        return None
    return username, account_id, role, banned, character


def _map_export_accounts(payload: Mapping[str, object]) -> list[Account]:
    players = payload.get("players")
    if not isinstance(players, list) or len(players) > _MAX_PLAYERS:
        raise DataSourceError(
            "malformed_export",
            "Docker character export contains an invalid player collection.",
        )

    accounts: dict[tuple[str, int], Account] = {}
    character_ids: set[int] = set()
    skipped = 0
    for player in players:
        parsed = _parse_export_player(player)
        if parsed is None:
            skipped += 1
            continue
        username, account_id, role, banned, character = parsed
        if character.char_id in character_ids:
            skipped += 1
            continue
        key = (username, account_id)
        account = accounts.get(key)
        if account is None:
            account = Account(username, account_id, role, banned)
            accounts[key] = account
        elif account.role != role or account.banned is not banned:
            skipped += 1
            continue
        character_ids.add(character.char_id)
        account.characters.append(character)

    if skipped:
        # Counts only — an export record can carry private character data.
        log.info(
            "Skipped %s of %s exported character records that are retired or "
            "failed validation",
            skipped,
            len(players),
        )

    result = sorted(accounts.values(), key=lambda account: account.username.casefold())
    for account in result:
        account.characters.sort(key=lambda character: character.name.casefold())
    return result


def _map_export_detail(
    payload: Mapping[str, object],
    requested_id: int,
) -> dict | None:
    selected_id = payload.get("selectedCharacterId")
    selected_player = payload.get("selectedPlayer")
    if selected_player is None:
        return None
    try:
        if _positive_id(selected_id) != requested_id:
            raise ValueError
        if not isinstance(selected_player, Mapping):
            raise ValueError
        if _positive_id(selected_player.get("characterId")) != requested_id:
            raise ValueError
        character = selected_player.get("character")
        if not isinstance(character, Mapping):
            raise ValueError
        return dict(character)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DataSourceError(
            "malformed_export",
            "Docker character export contains invalid selected-player data.",
        ) from exc


def _required_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    text = value.strip()
    if not text or len(text) > _MAX_TEXT_LENGTH or any(ord(char) < 32 for char in text):
        raise ValueError
    return text


def _optional_text(value: object, default: str) -> str:
    if value in (None, ""):
        return default
    return _required_text(value)


def _positive_id(value: object) -> int:
    number = _integer(value)
    if number <= 0:
        raise ValueError
    return number


def _nonnegative_int(value: object) -> int:
    number = _integer(value)
    if number < 0:
        raise ValueError
    return number


def _number_as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    if not math.isfinite(float(value)):
        raise ValueError
    return int(value)


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError
    text = str(value)
    if not text.isdigit() or len(text) > 19:
        raise ValueError
    return int(text)


def _strict_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError
    return value


def _finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return number
