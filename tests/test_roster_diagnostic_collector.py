import json
import subprocess
from pathlib import Path

import pytest

from src.core.runtime.character_roster import ROSTER_SCRIPT
from src.core.runtime.character_display import DISPLAY_SCRIPT
from src.core.runtime.data_diagnostics import DataDiagnostic
from src.core.runtime.docker_cli import DockerCommandRunner
from src.core.runtime.roster_diagnostic_collector import collect, list_targets, main
from test_roster_recovery_diagnostics import payload


IDENTITY = "a" * 64
PRIVATE = "private-sentinel-password-token-character"


def runner(roster=None, display=None, running=True):
    roster = payload() if roster is None else roster
    display = {"players": []} if display is None else display
    calls = []
    def execute(argv, cwd, timeout):
        calls.append(argv[1:])
        if argv[1] == "inspect":
            value = running
        elif argv[1] == "ps":
            value = {"ID": IDENTITY, "Names": "fixture-server-1"}
        else:
            assert argv[1:6] == ("exec", "-e", "NODE_OPTIONS=", IDENTITY, "node")
            assert argv[6] == "-e"
            assert argv[7] in (ROSTER_SCRIPT, DISPLAY_SCRIPT)
            value = roster if argv[7] == ROSTER_SCRIPT else display
        if isinstance(value, BaseException):
            raise value
        return subprocess.CompletedProcess(argv, 0, value if isinstance(value, str) else json.dumps(value), PRIVATE)
    return DockerCommandRunner(executable="docker.exe", execute=execute), calls


def test_report_contains_only_safe_summaries_and_readonly_commands():
    roster = payload()
    roster["accounts"][0]["accountName"] = PRIVATE
    roster["players"][0]["accountName"] = PRIVATE
    roster["players"][0]["characterName"] = PRIVATE
    roster["players"][0]["password"] = PRIVATE
    roster["players"][0]["skillPoints"] = PRIVATE
    fake, calls = runner(roster)
    report = collect(fake, IDENTITY)
    encoded = json.dumps(report)
    assert report["validatedRosterAvailable"] is True
    assert report["stages"]["roster"]["strictStatisticsProjection"] == "invalid"
    assert report["stages"]["roster"]["warnings"][0]["field"] == "skillPoints"
    for forbidden in (PRIVATE, IDENTITY, "fixture-account", "Fixture Pilot", "/app/", "gamestore.sqlite"):
        assert forbidden not in encoded
    assert [call[0] for call in calls] == ["inspect", "exec", "exec"]


def test_stopped_server_is_never_started():
    fake, calls = runner(running=False)
    assert collect(fake, IDENTITY)["status"] == "server_not_running"
    assert len(calls) == 1


def test_failure_stages_are_distinct_and_keep_collecting_display():
    fake, calls = runner(roster={"players": []}, display=PRIVATE)
    report = collect(fake, IDENTITY)
    assert report["stages"]["roster"]["diagnostic"]["reason"] == "invalid_collection"
    assert report["stages"]["display"]["diagnostic"]["reason"] == "invalid_json"
    assert len(calls) == 3 and PRIVATE not in json.dumps(report)


def test_transport_error_never_leaks_exception_or_stderr():
    fake, calls = runner(roster=subprocess.TimeoutExpired(PRIVATE, 20, output=PRIVATE, stderr=PRIVATE))
    report = collect(fake, IDENTITY)
    assert report["stages"]["roster"]["diagnostic"]["reason"] == "command_timeout"
    assert PRIVATE not in json.dumps(report)


@pytest.mark.parametrize("identity", ["", "server", "-e", "a" * 12, PRIVATE, "a" * 64 + "\n"])
def test_collector_rejects_unvalidated_targets_before_any_command(identity):
    fake, calls = runner()
    with pytest.raises(ValueError):
        collect(fake, identity)
    assert not calls


def test_diagnostic_metadata_cannot_be_a_secret_channel():
    diagnostic = DataDiagnostic(PRIVATE, PRIVATE, PRIVATE, PRIVATE, True, 10001)
    assert PRIVATE not in repr(diagnostic)
    assert diagnostic.accounts is None and diagnostic.players is None


def test_target_listing_is_local_only():
    fake, _ = runner()
    assert list_targets(fake)["targets"] == [{"id": IDENTITY, "name": "fixture-server-1"}]


def test_reporter_shaped_case_collision_is_validated_without_exposing_names():
    from test_roster_exact_account_identity import reported_shape
    exported = reported_shape()
    fake, _ = runner(roster=exported)
    report = collect(fake, IDENTITY)
    assert report["validatedRosterAvailable"]
    assert report["stages"]["roster"]["accounts"] == 11
    assert report["stages"]["roster"]["characters"] == 12
    assert report["stages"]["roster"]["legacyNormalizationCollisionGroups"] == 1
    assert all(a["accountName"] not in json.dumps(report) for a in exported["accounts"])


def test_cli_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    output = tmp_path / "EveJS-Roster-Diagnostics.json"
    output.write_text("preserve me")
    fake, _ = runner()
    monkeypatch.setattr("src.core.runtime.roster_diagnostic_collector.DockerCommandRunner", lambda **kw: fake)
    assert main(["--collect-roster-diagnostics", "--container-id", IDENTITY, "--output", str(output)]) == 2
    assert output.read_text() == "preserve me"


def test_cli_accepts_only_report_filenames(tmp_path):
    output = tmp_path / "gamestore.sqlite"
    assert main(["--collect-roster-diagnostics", "--container-id", IDENTITY, "--output", str(output)]) == 2
    assert not output.exists()
