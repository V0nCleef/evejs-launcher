"""Read-only support collection using the production export and validators.

Only the explicit final report is shareable. Container names/IDs are confined to
the temporary local chooser file; no config, raw export, stderr or logs are saved.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from src.core.runtime.character_display import DISPLAY_SCRIPT
from src.core.runtime.character_roster import ROSTER_SCRIPT
from src.core.runtime.data import (
    _map_display_values, _map_roster_accounts, _project_export_stdout,
)
from src.core.runtime.data_diagnostics import DataDiagnostic
from src.core.runtime.docker_cli import (
    DockerCommandRunner, DockerCommandError, DockerStructuredOutputError,
)

_ID = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_LIMIT = 8 * 1024 * 1024


def _transport(error, stage):
    if isinstance(error, DockerCommandError):
        reason = "command_timeout" if error.result.timed_out else "command_failed"
    elif isinstance(error, DockerStructuredOutputError):
        reason = "too_large" if error.code == "too_large" else "parser_rejected"
    else:
        reason = "command_unavailable"
    return dict(status="unavailable", diagnostic=DataDiagnostic(stage, reason).as_dict())


def list_targets(runner):
    def parse(stdout):
        targets = []
        for line in stdout.splitlines():
            row = json.loads(line)
            identity, name = row.get("ID"), row.get("Names")
            if not isinstance(identity, str) or not _ID.fullmatch(identity):
                raise ValueError
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ValueError
            targets.append(dict(id=identity, name=name))
            if len(targets) > 32:
                raise ValueError
        return targets
    try:
        targets = runner.run_parsed(
            ("ps", "--no-trunc", "--filter", "label=com.docker.compose.service=server",
             "--format", "{{json .}}"),
            cwd=Path.cwd(), parser=parse, timeout=10,
        )
        return dict(status="ready", targets=targets)
    except (DockerCommandError, DockerStructuredOutputError, OSError):
        return dict(status="docker_unavailable", targets=[])


def _summary(stdout, stage):
    projector = _map_roster_accounts if stage == "roster" else _map_display_values
    projection = _project_export_stdout(
        stdout, output_limit=_LIMIT, projector=projector, stage=stage,
    )
    if projection.status != "ok":
        diagnostic = projection.diagnostic or DataDiagnostic(stage, "too_large")
        return dict(status="rejected", diagnostic=diagnostic.as_dict())
    result = dict(status="ok", warnings=[w.at(stage).as_dict() for w in projection.warnings])
    if stage == "roster":
        result.update(accounts=len(projection.value),
                      characters=sum(len(a.characters) for a in projection.value))
        # Only the collision count leaves this frame, never the account keys.
        old_names = {}
        for account in projection.value:
            key = account.username.strip().casefold()
            old_names[key] = old_names.get(key, 0) + 1
        result["legacyNormalizationCollisionGroups"] = sum(count > 1 for count in old_names.values())
        # Test the original strict roster projection against the SAME response.
        # Nothing private is returned by either validator.
        old = _project_export_stdout(
            stdout, output_limit=_LIMIT,
            projector=lambda payload: _map_roster_accounts(payload, tolerate_display=False),
            stage=stage,
        )
        result["strictStatisticsProjection"] = old.status
    else:
        result["characters"] = len(projection.value)
    return result


def collect(runner, container_id):
    if not isinstance(container_id, str) or not _ID.fullmatch(container_id):
        raise ValueError("An exact running container ID is required.")
    report = dict(schemaVersion=1, collectorRevision=2, collectedAt=datetime.now(timezone.utc).isoformat(),
                  readOnly=True, status="checking", stages={})
    try:
        running = runner.run_parsed(
            ("inspect", "--type", "container", "--format", "{{json .State.Running}}", container_id),
            cwd=Path.cwd(), parser=lambda raw: json.loads(raw), timeout=10,
        )
        if running is not True:
            report["status"] = "server_not_running"
            return report
    except (DockerCommandError, DockerStructuredOutputError, OSError) as error:
        report.update(status="container_unavailable", transport=_transport(error, "roster"))
        return report
    for stage, script in (("roster", ROSTER_SCRIPT), ("display", DISPLAY_SCRIPT)):
        try:
            report["stages"][stage] = runner.run_parsed(
                ("exec", "-e", "NODE_OPTIONS=", container_id, "node", "-e", script),
                cwd=Path.cwd(), parser=lambda stdout: _summary(stdout, stage), timeout=20,
            )
        except (DockerCommandError, DockerStructuredOutputError, OSError) as error:
            report["stages"][stage] = _transport(error, stage)
    report["status"] = "collected"
    report["validatedRosterAvailable"] = report["stages"]["roster"]["status"] == "ok"
    return report


def main(argv):
    parser = argparse.ArgumentParser(description="Read-only Docker roster support collector")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--collect-roster-diagnostics", action="store_true")
    mode.add_argument("--list-roster-diagnostic-targets", action="store_true")
    parser.add_argument("--container-id")
    parser.add_argument("--output", required=True)
    options = parser.parse_args(argv)
    output = Path(options.output)
    expected = "targets.private.json" if options.list_roster_diagnostic_targets else "EveJS-Roster-Diagnostics.json"
    if not output.is_absolute() or output.name != expected or not output.parent.is_dir():
        return 2
    try:
        runner = DockerCommandRunner(output_limit=_LIMIT)
        report = list_targets(runner) if options.list_roster_diagnostic_targets else collect(runner, options.container_id)
    except (OSError, ValueError):
        report = dict(schemaVersion=1, status="docker_unavailable", readOnly=True)
        if options.list_roster_diagnostic_targets:
            report["targets"] = []
    try:
        # Never overwrite an existing report or any installation file.
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    except OSError:
        return 2
    return 0
