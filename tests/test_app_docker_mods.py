"""Application-level Docker mod bridge and recreation guards."""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMainWindow, QMessageBox

from src import app as app_module
from src.app import MainWindow
from src.core.runtime.docker_controller import DockerLifecycleAction
from src.core.runtime.docker_mods import docker_mod_override_path


def _loader(root: Path, mod_name: str) -> None:
    loader = root / "mods" / mod_name / "loader.js"
    loader.parent.mkdir(parents=True, exist_ok=True)
    loader.write_text("module.exports = {};\n", encoding="utf-8")


def _window(root: Path, policy: str = "managed") -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": policy,
        "evejs_root": str(root),
        "docker_compose_file": str(root / "compose.yaml"),
        "docker_project_name": "fixture",
    }
    window._mods_page = type(
        "Mods",
        (),
        {"selected_mod_names": lambda _self: ("Fixture Mod",)},
    )()
    window._restart_docker_monitor_for_compose_change = lambda: None
    return window


def test_docker_target_factory_appends_existing_mod_override(qapp, tmp_path: Path) -> None:
    _loader(tmp_path, "Fixture Mod")
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    docker_mod_override_path(tmp_path).parent.mkdir(parents=True)
    docker_mod_override_path(tmp_path).write_text(
        "# Managed by EveJS Launcher. Manual edits will be replaced.\nservices: {}\n",
        encoding="utf-8",
    )
    window = _window(tmp_path)

    target = window._docker_log_target_factory()()

    assert target.override_files == (docker_mod_override_path(tmp_path),)


def test_connect_only_mod_apply_rejects_before_filesystem_or_lifecycle(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path, "connect_only")
    denials: list[str] = []
    window._docker_unavailable = denials.append
    window._begin_docker_lifecycle = lambda _action: pytest.fail("lifecycle mutation")
    monkeypatch.setattr(
        app_module,
        "apply_docker_mod_override",
        lambda *_args, **_kwargs: pytest.fail("Compose-state mutation"),
    )

    window._on_mods_apply_restart()

    assert denials == ["Connect-only Docker mode cannot change mod or Compose state."]
    assert not docker_mod_override_path(tmp_path).exists()


def test_active_lifecycle_blocks_managed_mod_override_before_mutation(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    window._lifecycle_thread = object()
    notices: list[str] = []
    window._docker_unavailable = notices.append
    window._restart_docker_monitor_for_compose_change = lambda: pytest.fail(
        "monitor restart"
    )
    window._begin_docker_lifecycle = lambda _action: pytest.fail(
        "lifecycle mutation"
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: pytest.fail("confirmation after occupied slot"),
    )
    monkeypatch.setattr(
        app_module,
        "apply_docker_mod_override",
        lambda *_args, **_kwargs: pytest.fail("Compose-state mutation"),
    )

    window._on_mods_apply_restart()

    assert notices == [
        "Another service or Docker tool operation is already running."
    ]
    assert not docker_mod_override_path(tmp_path).exists()


def test_managed_mod_apply_requires_confirmation_before_override_write(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[DockerLifecycleAction] = []
    window._begin_docker_lifecycle = lambda action: lifecycle.append(action) or True
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    window._on_mods_apply_restart()

    assert lifecycle == []
    assert not docker_mod_override_path(tmp_path).exists()


def test_managed_mod_apply_writes_override_then_requests_exact_recreation(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[DockerLifecycleAction] = []
    window._begin_docker_lifecycle = lambda action: lifecycle.append(action) or True
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    window._on_mods_apply_restart()

    assert docker_mod_override_path(tmp_path).is_file()
    assert lifecycle == [DockerLifecycleAction.RECREATE_GAME]


def test_unchanged_managed_mod_override_does_not_recreate_again(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[DockerLifecycleAction] = []
    window._begin_docker_lifecycle = lambda action: lifecycle.append(action) or True
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    messages: list[str] = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "information",
        lambda _parent, _title, message: messages.append(message),
    )

    window._on_mods_apply_restart()
    window._on_mods_apply_restart()

    assert lifecycle == [DockerLifecycleAction.RECREATE_GAME]
    assert messages == ["Docker mod preload configuration is already current."]
