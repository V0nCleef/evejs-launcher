"""Deterministic Docker Compose mod-bridge contracts (no daemon required)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.core.runtime.docker_compose import ComposeTarget
from src.core.runtime.docker_mods import (
    DockerModBridgeError,
    apply_docker_mod_override,
    attach_docker_mod_override,
    build_docker_mod_override,
    docker_mod_override_path,
)
from src.core.service_status import DockerControlPolicy


FIXTURES = Path(__file__).parent / "fixtures" / "docker"


def _loader(root: Path, mod_name: str) -> None:
    loader = root / "mods" / mod_name / "loader.js"
    loader.parent.mkdir(parents=True, exist_ok=True)
    loader.write_text("module.exports = {};\n", encoding="utf-8")


def _json_scalar(content: str, key: str) -> str:
    prefix = f"{key}: "
    line = next(line.strip() for line in content.splitlines() if line.strip().startswith(prefix))
    value = json.loads(line[len(prefix):])
    assert isinstance(value, str)
    return value


def test_override_output_and_hash_are_deterministic_in_selected_preload_order(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Fixture EveJS With Spaces"
    selected = ("Zeta Mod", "alpha")

    first = build_docker_mod_override(root, selected)
    second = build_docker_mod_override(root, selected)

    expected_node_options = (
        '--require "/app/mods/Zeta Mod/loader.js" '
        '--require "/app/mods/alpha/loader.js"'
    )
    assert first == second
    assert first.selected_mods == selected
    assert first.node_options == expected_node_options
    assert _json_scalar(first.content, "NODE_OPTIONS") == expected_node_options
    assert _json_scalar(first.content, "source") == (root / "mods").resolve().as_posix()
    assert first.content_hash == hashlib.sha256(first.content.encode("utf-8")).hexdigest()
    assert first.content.endswith("\n")


def test_compose_target_appends_launcher_override_after_existing_files(
    tmp_path: Path,
) -> None:
    base = (tmp_path / "compose.yaml").resolve()
    existing = (tmp_path / "compose.existing.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    existing.write_text("services: {}\n", encoding="utf-8")
    _loader(tmp_path, "alpha")
    apply_docker_mod_override(
        tmp_path,
        ("alpha",),
        policy=DockerControlPolicy.MANAGED,
    )

    original = ComposeTarget(base, tmp_path.resolve(), "fixture", (existing,))
    merged = attach_docker_mod_override(original)

    assert original.override_files == (existing,)
    assert merged.override_files == (existing, docker_mod_override_path(tmp_path))
    assert merged.base_argv("docker") == (
        "docker",
        "compose",
        "-f",
        str(base),
        "-f",
        str(existing),
        "-f",
        str(docker_mod_override_path(tmp_path)),
        "--project-directory",
        str(tmp_path.resolve()),
        "-p",
        "fixture",
    )


def test_apply_detects_recreation_changes_and_empty_mods_remove_owned_override(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "alpha")
    _loader(tmp_path, "beta")

    first = apply_docker_mod_override(
        tmp_path,
        ("alpha", "beta"),
        policy=DockerControlPolicy.MANAGED,
    )
    unchanged = apply_docker_mod_override(
        tmp_path,
        ("alpha", "beta"),
        policy=DockerControlPolicy.MANAGED,
    )
    reordered = apply_docker_mod_override(
        tmp_path,
        ("beta", "alpha"),
        policy=DockerControlPolicy.MANAGED,
    )
    emptied = apply_docker_mod_override(
        tmp_path,
        (),
        policy=DockerControlPolicy.MANAGED,
    )
    empty_again = apply_docker_mod_override(
        tmp_path,
        (),
        policy=DockerControlPolicy.MANAGED,
    )

    assert first.changed and first.requires_recreation
    assert not unchanged.changed and not unchanged.requires_recreation
    assert reordered.changed and reordered.requires_recreation
    assert first.content_hash != reordered.content_hash
    assert emptied.changed and emptied.requires_recreation
    assert emptied.content_hash is None
    assert not docker_mod_override_path(tmp_path).exists()
    assert not empty_again.changed and not empty_again.requires_recreation


def test_apply_validates_loaders_and_connect_only_rejects_before_mutation(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "Supported Mod")

    with pytest.raises(PermissionError, match="Connect-only"):
        apply_docker_mod_override(
            tmp_path,
            ("Supported Mod",),
            policy=DockerControlPolicy.CONNECT_ONLY,
        )
    assert not docker_mod_override_path(tmp_path).exists()

    with pytest.raises(DockerModBridgeError, match="loader"):
        apply_docker_mod_override(
            tmp_path,
            ("Missing Mod",),
            policy=DockerControlPolicy.MANAGED,
        )
    assert not docker_mod_override_path(tmp_path).exists()


@pytest.mark.parametrize("mod_name", ["../escape", "nested/mod", "nested\\mod", ".", ""])
def test_mod_names_cannot_escape_the_single_mods_bind(tmp_path: Path, mod_name: str) -> None:
    with pytest.raises(DockerModBridgeError):
        build_docker_mod_override(tmp_path, (mod_name,))


def test_fixture_proves_expected_node_options_and_writable_mod_bind() -> None:
    expected = json.loads(
        (FIXTURES / "compose-config-mod-bridge.json").read_text(encoding="utf-8")
    )
    bridge = build_docker_mod_override(
        Path("C:/Fixture Space/EveJS"),
        ("alpha", "Zeta Mod"),
    )

    server = expected["services"]["server"]
    assert _json_scalar(bridge.content, "NODE_OPTIONS") == server["environment"]["NODE_OPTIONS"]
    assert _json_scalar(bridge.content, "source") == server["volumes"][0]["source"]
    assert 'target: "/app/mods"' in bridge.content
    assert "read_only: false" in bridge.content
