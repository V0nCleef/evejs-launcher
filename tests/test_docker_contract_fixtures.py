"""Executable privacy-safe contract checks for deferred Docker support fixtures."""
from __future__ import annotations

import json
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "docker"
COMPOSE_FIXTURES = (
    "compose-v0122-shaped.yaml",
    "compose-v0123-shaped.yaml",
)
JSON_FIXTURES = (
    "compose-ps-array.json",
    "compose-ps-object.json",
    "compose-config-mod-bridge.json",
    "compose-config-loopback-ports.json",
    "compose-config-nested-mounts.json",
    "compose-config-invalid-nonloopback.json",
    "compose-config-invalid-duplicate-target.json",
)
SYNTHETIC_TEXT = (
    "fixture-evejs",
    "example.invalid/evejs-server:0.12.3-fixture",
    "C:/Fixture Space/EveJS/_local/gameStore",
)


def read_json_fixture(name: str) -> object:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_docker_contract_fixtures_are_present_synthetic_and_parseable() -> None:
    expected_files = {*COMPOSE_FIXTURES, *JSON_FIXTURES, "compose-ps-ndjson.jsonl"}

    assert {path.name for path in FIXTURE_DIR.iterdir()} == expected_files

    compose_texts = {
        name: (FIXTURE_DIR / name).read_text(encoding="utf-8")
        for name in COMPOSE_FIXTURES
    }
    for text in compose_texts.values():
        assert all(value in text for value in SYNTHETIC_TEXT)
        assert "environment:" not in text
        assert "secrets:" not in text

    current_compose = compose_texts["compose-v0123-shaped.yaml"]
    assert "name: fixture-evejs" in current_compose
    assert all(f"  {service}:" in current_compose for service in ("init", "market", "server"))
    assert "  market-tools:" in current_compose
    assert "profiles: [tools]" in current_compose
    assert "condition: service_completed_successfully" in current_compose
    assert "condition: service_healthy" in current_compose
    assert "fixture-evejs-data:/var/lib/evejs" in current_compose
    assert "C:/Fixture Space/EveJS/_local/gameStore:/var/lib/evejs/gameStore" in current_compose
    for publication in (
        "127.0.0.1:32600:26000",
        "127.0.0.1:32601:26001",
        "127.0.0.1:32602:26002",
        "127.0.0.1:34443:26003",
        "127.0.0.1:35222:5222",
        "127.0.0.1:40110:40110",
    ):
        assert publication in current_compose

    parsed_json = {name: read_json_fixture(name) for name in JSON_FIXTURES}
    assert isinstance(parsed_json["compose-ps-array.json"], list)
    assert isinstance(parsed_json["compose-ps-object.json"], dict)
    assert all(isinstance(parsed_json[name], dict) for name in JSON_FIXTURES[2:])

    ndjson_records = [
        json.loads(line)
        for line in (FIXTURE_DIR / "compose-ps-ndjson.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert ndjson_records

    ps_records = (
        parsed_json["compose-ps-array.json"]
        + [parsed_json["compose-ps-object.json"]]
        + ndjson_records
    )
    assert all(
        set(record) == {"Service", "Name", "ID", "State", "Health", "ExitCode", "Publishers"}
        for record in ps_records
    )
    assert {record["Service"] for record in ps_records} == {"init", "market", "server"}
    assert all(record["Name"].startswith("fixture-evejs-") for record in ps_records)
    assert all(isinstance(record["Publishers"], list) for record in ps_records)

    ports = parsed_json["compose-config-loopback-ports.json"]
    assert ports["project"] == "fixture-evejs"
    assert {publication["hostIp"] for publication in ports["publishers"]} == {"127.0.0.1"}
    assert {publication["published"] for publication in ports["publishers"]} == {32600, 32601, 32602, 34443, 35222, 40110}

    mounts = parsed_json["compose-config-nested-mounts.json"]
    assert mounts["project"] == "fixture-evejs"
    assert mounts["service"] == "server"
    assert mounts["mounts"][0]["target"] == "/var/lib/evejs"
    assert mounts["mounts"][1] == {
        "type": "bind",
        "source": "C:/Fixture Space/EveJS/_local/gameStore",
        "target": "/var/lib/evejs/gameStore",
    }

    nonloopback = parsed_json["compose-config-invalid-nonloopback.json"]
    assert nonloopback["publishers"][0]["hostIp"] == "0.0.0.0"
    duplicate = parsed_json["compose-config-invalid-duplicate-target.json"]
    assert [mount["target"] for mount in duplicate["mounts"]].count(
        "/var/lib/evejs/gameStore"
    ) == 2

    forbidden_fragments = (
        "C:/Users/",
        "G:/",
        "TOKEN_SHOULD_NOT_APPEAR",
        "environment:",
        "secrets:",
    )
    for path in FIXTURE_DIR.iterdir():
        content = path.read_text(encoding="utf-8")
        assert all(fragment not in content for fragment in forbidden_fragments)
