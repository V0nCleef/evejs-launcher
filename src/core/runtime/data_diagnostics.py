"""Allowlisted data diagnostics: never retain values, identifiers or exceptions."""
from dataclasses import dataclass, replace

DOCKER_MODS_UNAVAILABLE_MESSAGE = (
    "The Docker mod setup needs attention. Open Mods to review missing folders "
    "or unfinished changes."
)

_STAGES = {"unknown", "roster", "display", "detail", "mods"}
_REASONS = {
    "unknown", "invalid_json", "invalid_collection", "invalid_field",
    "duplicate_identity", "account_mismatch", "command_failed",
    "command_timeout", "command_unavailable", "too_large", "parser_rejected",
    "unsupported_result", "inconsistent_identity", "mods_unavailable",
}
_FIELDS = {
    "unknown", "accounts", "players", "accountId", "accountName", "role",
    "banned", "isGM", "characterId", "characterName", "balance", "skillPoints",
    "shipName", "shipTypeID", "securityStatus", "securityRating",
    "solarSystemName", "solarSystemSecurity", "skippedRecords", "selectedPlayer",
}
_KINDS = {"unknown", "missing", "null", "boolean", "integer", "number", "string", "array", "object"}


def value_kind(value):
    return {type(None): "null", bool: "boolean", int: "integer", float: "number",
            str: "string", list: "array", dict: "object"}.get(type(value), "unknown")


@dataclass(frozen=True)
class DataDiagnostic:
    stage: str = "unknown"
    reason: str = "unknown"
    field: str = "unknown"
    value_type: str = "unknown"
    accounts: int | None = None
    players: int | None = None

    def __post_init__(self):
        for name, allowed in (("stage", _STAGES), ("reason", _REASONS),
                              ("field", _FIELDS), ("value_type", _KINDS)):
            value = getattr(self, name)
            if type(value) is not str or value not in allowed:
                object.__setattr__(self, name, "unknown")
        for name in ("accounts", "players"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 10_000:
                object.__setattr__(self, name, None)

    def at(self, stage):
        return replace(self, stage=stage)

    def summary(self):
        return (f"stage={self.stage} reason={self.reason} field={self.field} "
                f"type={self.value_type} accounts={self.accounts} players={self.players}")

    def as_dict(self):
        return {name: getattr(self, name) for name in
                ("stage", "reason", "field", "value_type", "accounts", "players")}
