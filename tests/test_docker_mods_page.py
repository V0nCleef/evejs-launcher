"""Mods-page backend capability and recreation messaging contracts."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt

from src.core.service_status import DockerControlPolicy, RuntimeBackend
from src.pages.mods_page import ModsPage


def _loader(root: Path, mod_name: str, *, active: bool = True) -> Path:
    filename = "loader.js" if active else "loader.js.disabled"
    loader = root / "mods" / mod_name / filename
    loader.parent.mkdir(parents=True, exist_ok=True)
    loader.write_text("module.exports = {};\n", encoding="utf-8")
    return loader


def test_native_mod_controls_keep_existing_restart_contract(qapp, tmp_path: Path) -> None:
    _loader(tmp_path, "Fixture Mod")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))

    assert page.apply_btn.text() == "Apply && Restart Server"
    assert page.apply_btn.isEnabled()
    assert page._rows[0].toggle.isEnabled()
    assert "Native" in page.lbl_backend.text()


def test_managed_docker_exposes_supported_preloads_with_recreation_message(
    qapp,
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "Zeta Mod")
    _loader(tmp_path, "alpha")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))

    page.set_runtime_context(
        RuntimeBackend.DOCKER_COMPOSE,
        DockerControlPolicy.MANAGED,
    )

    assert page.apply_btn.text() == "Apply && Recreate Server"
    assert page.apply_btn.isEnabled()
    assert all(row.toggle.isEnabled() for row in page._rows)
    assert "recreat" in page.lbl_backend.text().casefold()
    assert page.selected_mod_names() == ("alpha", "Zeta Mod")


def test_connect_only_lists_mods_but_cannot_mutate_files_or_emit_apply(
    qapp,
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "Fixture Mod")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    emitted: list[str] = []
    page.apply_restart_clicked.connect(lambda: emitted.append("apply"))

    page.set_runtime_context(
        RuntimeBackend.DOCKER_COMPOSE,
        DockerControlPolicy.CONNECT_ONLY,
    )
    row = page._rows[0]
    row._on_toggled(True)
    page._on_apply_clicked()

    assert loader.exists()
    assert not (loader.parent / "loader.js.disabled").exists()
    assert not row.toggle.isEnabled()
    assert "Connect-only" in row.toggle.toolTip()
    assert not page.apply_btn.isEnabled()
    assert "Connect-only" in page.lbl_backend.text()
    assert emitted == []


def test_root_change_rebuilds_cards_from_new_root(qapp, tmp_path: Path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _loader(old_root, "Old Mod")
    _loader(new_root, "New Mod")
    page = ModsPage()
    page.set_evejs_root(str(old_root))

    page.set_evejs_root(str(new_root))

    assert [row.mod.name for row in page._rows] == ["New Mod"]


def test_deep_signal_manifest_is_accessible_and_privacy_safe_at_minimum_width(
    qapp,
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "Fixture Mod")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    page.resize(756, 560)
    page.show()
    qapp.processEvents()

    try:
        row = page._rows[0]

        assert page.page_header.title_label.text() == "MODS"
        assert page.page_header.eyebrow_label.text() == "EXTENSION CONTROL"
        assert page.runtime_panel.property("class") == "modsRuntimePanel"
        assert page.manifest_panel.property("class") == "modsManifestPanel"
        assert page.action_rail.property("class") == "modsActionRail"
        assert page.runtime_state_label.text() == "NATIVE HOST"
        assert page._scroll.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert page._scroll.horizontalScrollBar().maximum() == 0

        assert page.refresh_btn.accessibleName() == "Refresh mods"
        assert page.refresh_btn.accessibleDescription()
        assert page.apply_btn.accessibleName()
        assert page.apply_btn.accessibleDescription()
        assert row.accessibleName() == "Mod Fixture Mod"
        assert row.toggle.accessibleName() == "Enable Fixture Mod"
        assert row.toggle.accessibleDescription()
        assert row.state_label.text() == "ENABLED"

        assert str(tmp_path) not in row.path_label.text()
        assert row.path_label.toolTip() == str(loader.parent)
    finally:
        page.close()
        page.deleteLater()
