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
    docker_mod_transaction_path,
    finalize_docker_mod_override,
    rollback_docker_mod_override,
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


def test_empty_override_explicitly_clears_inherited_node_options(
    tmp_path: Path,
) -> None:
    override = build_docker_mod_override(tmp_path, ())

    assert override.selected_mods == ()
    assert override.node_options == ""
    assert _json_scalar(override.content, "NODE_OPTIONS") == ""
    assert "volumes:" not in override.content
    assert override.content_hash == hashlib.sha256(
        override.content.encode("utf-8")
    ).hexdigest()


def test_owned_override_normalizes_over_limit_json_integer(tmp_path: Path) -> None:
    rendered = build_docker_mod_override(tmp_path, ()).content
    lines = rendered.splitlines()
    index = next(
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("NODE_OPTIONS: ")
    )
    indentation = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
    lines[index] = indentation + "NODE_OPTIONS: " + ("9" * 5000)
    path = docker_mod_override_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(DockerModBridgeError, match="NODE_OPTIONS value is invalid"):
        apply_docker_mod_override(
            tmp_path,
            (),
            policy=DockerControlPolicy.MANAGED,
        )


def test_compose_target_appends_launcher_override_after_existing_files(
    tmp_path: Path,
) -> None:
    base = (tmp_path / "compose.yaml").resolve()
    existing = (tmp_path / "compose.existing.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    existing.write_text("services: {}\n", encoding="utf-8")
    _loader(tmp_path, "alpha")
    result = apply_docker_mod_override(
        tmp_path,
        ("alpha",),
        policy=DockerControlPolicy.MANAGED,
    )
    finalize_docker_mod_override(result, policy=DockerControlPolicy.MANAGED)

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


def test_apply_detects_changes_and_empty_mods_keep_a_clearing_override(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "alpha")
    _loader(tmp_path, "beta")

    first = apply_docker_mod_override(
        tmp_path,
        ("alpha", "beta"),
        policy=DockerControlPolicy.MANAGED,
    )
    finalize_docker_mod_override(first, policy=DockerControlPolicy.MANAGED)
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
    finalize_docker_mod_override(reordered, policy=DockerControlPolicy.MANAGED)
    emptied = apply_docker_mod_override(
        tmp_path,
        (),
        policy=DockerControlPolicy.MANAGED,
    )
    finalize_docker_mod_override(emptied, policy=DockerControlPolicy.MANAGED)
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
    assert emptied.content_hash == build_docker_mod_override(tmp_path, ()).content_hash
    assert docker_mod_override_path(tmp_path).read_text(encoding="utf-8") == (
        build_docker_mod_override(tmp_path, ()).content
    )
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


@pytest.mark.parametrize(
    "mod_name",
    ["../escape", "nested/mod", "nested\\mod", ".", "", " leading", "trailing "],
)
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


def test_attach_rejects_header_spoof_with_extra_compose_semantics(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "alpha")
    base = (tmp_path / "compose.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    path = docker_mod_override_path(tmp_path)
    path.parent.mkdir(parents=True)
    material = build_docker_mod_override(tmp_path, ("alpha",)).content
    path.write_bytes(
        (
            material
            + "  server:\n"
            + "    command: [\"definitely-not-the-game-server\"]\n"
        ).encode("utf-8")
    )
    target = ComposeTarget(base, tmp_path.resolve(), "fixture")

    with pytest.raises(DockerModBridgeError, match="exact launcher renderer"):
        attach_docker_mod_override(target)


def test_attach_rejects_override_symlink_even_when_target_is_exact(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "alpha")
    base = (tmp_path / "compose.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    material = build_docker_mod_override(tmp_path, ("alpha",)).content
    target_file = tmp_path / "outside.yaml"
    target_file.write_bytes(material.encode("utf-8"))
    path = docker_mod_override_path(tmp_path)
    path.parent.mkdir(parents=True)
    try:
        path.symlink_to(target_file)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    with pytest.raises(DockerModBridgeError, match="safe regular file"):
        attach_docker_mod_override(
            ComposeTarget(base, tmp_path.resolve(), "fixture")
        )


def test_attach_rejects_unsafe_override_parent_link(tmp_path: Path) -> None:
    _loader(tmp_path, "alpha")
    base = (tmp_path / "compose.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "compose.mods.yaml").write_bytes(
        build_docker_mod_override(tmp_path, ("alpha",)).content.encode("utf-8")
    )
    parent = tmp_path / ".evejs-launcher"
    try:
        parent.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(DockerModBridgeError, match="directory.*unsafe"):
        attach_docker_mod_override(
            ComposeTarget(base, tmp_path.resolve(), "fixture")
        )


def test_disabled_loader_blocks_attach_but_apply_can_commit_empty_selection(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "alpha")
    base = (tmp_path / "compose.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    applied = apply_docker_mod_override(
        tmp_path,
        ("alpha",),
        policy=DockerControlPolicy.MANAGED,
    )
    finalize_docker_mod_override(applied, policy=DockerControlPolicy.MANAGED)
    loader = tmp_path / "mods" / "alpha" / "loader.js"
    loader.rename(loader.with_name("loader.js.disabled"))
    target = ComposeTarget(base, tmp_path.resolve(), "fixture")

    with pytest.raises(DockerModBridgeError, match="active loader"):
        attach_docker_mod_override(target)

    emptied = apply_docker_mod_override(
        tmp_path,
        (),
        policy=DockerControlPolicy.MANAGED,
    )
    assert emptied.changed
    finalize_docker_mod_override(emptied, policy=DockerControlPolicy.MANAGED)
    assert attach_docker_mod_override(target).override_files == (
        docker_mod_override_path(tmp_path),
    )


def test_empty_clearing_override_attaches_without_a_mods_directory(
    tmp_path: Path,
) -> None:
    base = (tmp_path / "compose.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    applied = apply_docker_mod_override(
        tmp_path,
        (),
        policy=DockerControlPolicy.MANAGED,
    )
    finalize_docker_mod_override(applied, policy=DockerControlPolicy.MANAGED)

    merged = attach_docker_mod_override(
        ComposeTarget(base, tmp_path.resolve(), "fixture")
    )

    assert merged.override_files == (docker_mod_override_path(tmp_path),)


def test_rollback_restores_exact_prior_bytes_and_removes_first_write(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "alpha")
    _loader(tmp_path, "beta")
    first = apply_docker_mod_override(
        tmp_path,
        ("alpha",),
        policy=DockerControlPolicy.MANAGED,
    )
    finalize_docker_mod_override(first, policy=DockerControlPolicy.MANAGED)
    first_bytes = docker_mod_override_path(tmp_path).read_bytes()
    second = apply_docker_mod_override(
        tmp_path,
        ("beta",),
        policy=DockerControlPolicy.MANAGED,
    )

    rollback_docker_mod_override(
        second,
        policy=DockerControlPolicy.MANAGED,
    )
    assert docker_mod_override_path(tmp_path).read_bytes() == first_bytes

    other_root = tmp_path / "other"
    other_root.mkdir()
    _loader(other_root, "alpha")
    only = apply_docker_mod_override(
        other_root,
        ("alpha",),
        policy=DockerControlPolicy.MANAGED,
    )
    rollback_docker_mod_override(
        only,
        policy=DockerControlPolicy.MANAGED,
    )
    assert not docker_mod_override_path(other_root).exists()
    assert not docker_mod_transaction_path(other_root).exists()
    assert first.previous_content is None


def test_rollback_refuses_to_overwrite_post_apply_drift(tmp_path: Path) -> None:
    _loader(tmp_path, "alpha")
    result = apply_docker_mod_override(
        tmp_path,
        ("alpha",),
        policy=DockerControlPolicy.MANAGED,
    )
    path = docker_mod_override_path(tmp_path)
    drifted = result.committed_content + b"# drift\n"
    path.write_bytes(drifted)

    with pytest.raises(DockerModBridgeError):
        rollback_docker_mod_override(
            result,
            policy=DockerControlPolicy.MANAGED,
        )

    assert path.read_bytes() == drifted


def test_pending_transaction_blocks_ordinary_and_wrong_token_attach(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "alpha")
    base = (tmp_path / "compose.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    result = apply_docker_mod_override(
        tmp_path,
        ("alpha",),
        policy=DockerControlPolicy.MANAGED,
    )
    target = ComposeTarget(base, tmp_path.resolve(), "fixture")

    assert docker_mod_transaction_path(tmp_path).is_file()
    with pytest.raises(DockerModBridgeError, match="unfinished"):
        attach_docker_mod_override(target)
    with pytest.raises(DockerModBridgeError, match="unfinished"):
        attach_docker_mod_override(target, transaction_token="0" * 64)

    authorized = attach_docker_mod_override(
        target,
        transaction_token=result.transaction_token,
    )
    assert authorized.override_files == (docker_mod_override_path(tmp_path),)
    assert docker_mod_transaction_path(tmp_path).is_file()

    finalize_docker_mod_override(result, policy=DockerControlPolicy.MANAGED)
    assert not docker_mod_transaction_path(tmp_path).exists()
    assert attach_docker_mod_override(target).override_files == (
        docker_mod_override_path(tmp_path),
    )
    with pytest.raises(DockerModBridgeError, match="stale"):
        attach_docker_mod_override(
            target,
            transaction_token=result.transaction_token,
        )


def test_authorized_attach_rejects_missing_or_changed_transaction_override(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "alpha")
    base = (tmp_path / "compose.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    result = apply_docker_mod_override(
        tmp_path,
        ("alpha",),
        policy=DockerControlPolicy.MANAGED,
    )
    target = ComposeTarget(base, tmp_path.resolve(), "fixture")
    path = docker_mod_override_path(tmp_path)
    path.unlink()

    with pytest.raises(DockerModBridgeError, match="no longer matches"):
        attach_docker_mod_override(
            target,
            transaction_token=result.transaction_token,
        )

    path.write_bytes(build_docker_mod_override(tmp_path, ()).content.encode("utf-8"))
    with pytest.raises(DockerModBridgeError, match="no longer matches"):
        attach_docker_mod_override(
            target,
            transaction_token=result.transaction_token,
        )


def test_explicit_apply_resumes_exact_stranded_desired_transaction(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "alpha")
    first = apply_docker_mod_override(
        tmp_path,
        ("alpha",),
        policy=DockerControlPolicy.MANAGED,
    )

    resumed = apply_docker_mod_override(
        tmp_path,
        ("alpha",),
        policy=DockerControlPolicy.MANAGED,
    )

    assert resumed.changed and resumed.requires_recreation
    assert resumed.transaction_token == first.transaction_token
    assert resumed.committed_content == first.committed_content
    finalize_docker_mod_override(resumed, policy=DockerControlPolicy.MANAGED)
    assert not docker_mod_transaction_path(tmp_path).exists()


def test_attach_accepts_a_crlf_rewrite_of_the_exact_owned_override(
    tmp_path: Path,
) -> None:
    """A ``core.autocrlf`` checkout must not fail every Docker path closed."""
    _loader(tmp_path, "alpha")
    base = (tmp_path / "compose.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    path = docker_mod_override_path(tmp_path)
    path.parent.mkdir(parents=True)
    material = build_docker_mod_override(tmp_path, ("alpha",)).content
    path.write_bytes(material.replace("\n", "\r\n").encode("utf-8"))
    target = ComposeTarget(base, tmp_path.resolve(), "fixture")

    attached = attach_docker_mod_override(target)

    assert attached.override_files == (path.resolve(),)


@pytest.mark.parametrize("separator", ["\r", "\x0b", "\x0c", "\x85", " "])
def test_attach_still_rejects_every_other_rewritten_line_separator(
    tmp_path: Path,
    separator: str,
) -> None:
    """Only CRLF is folded; ``splitlines`` accepts more than YAML does."""
    _loader(tmp_path, "alpha")
    base = (tmp_path / "compose.yaml").resolve()
    base.write_text("services: {}\n", encoding="utf-8")
    path = docker_mod_override_path(tmp_path)
    path.parent.mkdir(parents=True)
    material = build_docker_mod_override(tmp_path, ("alpha",)).content
    path.write_bytes(material.replace("\n", separator).encode("utf-8"))
    target = ComposeTarget(base, tmp_path.resolve(), "fixture")

    with pytest.raises(DockerModBridgeError):
        attach_docker_mod_override(target)
