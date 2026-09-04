"""Explicit Mods-folder onboarding and author-guide UI contracts."""
from __future__ import annotations

import os
from pathlib import Path
from string import Formatter

import pytest

from src.core.mod_manager import ActivationKind, Mod
from src.core.mod_management import ModNotManagedError
from src.core.service_status import DockerControlPolicy, RuntimeBackend
from src.i18n import LANGUAGES, current_language, set_language, translate_ui_phrase
from src.pages import mods_page as mods_page_module
from src.pages.mods_page import MOD_AUTHORING_GUIDE_URL, ModsPage
from src.widgets.ui_translation import retranslate_widget_tree


NEW_UI_PHRASES = (
    "CREATE MOD FOLDER",
    "Create Mod Folder",
    "Create the canonical Mods folder inside the configured EveJS root.",
    "Create the Mods folder first. Then place each mod's folder inside it and click Refresh.",
    "DIR",
    "File Explorer did not accept the folder URL: {path}",
    "Making a mod? Read the launcher compatibility guide for mod authors.",
    "MOD AUTHOR GUIDE ↗",
    "MOD FOLDER",
    "Mod Author Guide",
    "Mod Folder Error",
    "No EveJS root is configured.",
    "OPEN MOD FOLDER",
    "Open Mod Author Guide",
    "Open Mod Folder",
    "Open the configured Mods folder in File Explorer.",
    "Open the EveJS Launcher mod-authoring guide on GitHub.",
    "The mod-authoring guide could not be opened in your default browser.",
    "The configured EveJS root could not be resolved: {path}. {details}",
    "The configured EveJS root is not a folder: {path}",
    "The configured EveJS root is not an absolute path: {path}",
    "The Mods folder could not be created.\n\nDetails: {details}",
    "The Mods folder could not be opened.\n\nDetails: {details}",
    "The Mods folder could not be resolved: {path}. {details}",
    "The Mods folder does not exist: {path}",
    "The Mods folder is unavailable. Check the configured EveJS root in Settings.",
    "The Mods folder resolves outside the configured EveJS root: {folder} -> {resolved}",
    "The Mods path exists but is not a folder: {path}",
    "To add a mod, place the mod's folder inside this folder, then click Refresh.",
)


@pytest.fixture(autouse=True)
def _isolate_mod_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_language = current_language()
    set_language("en")
    monkeypatch.setattr(mods_page_module.config, "get_setting", lambda *_args: "")
    monkeypatch.setattr(
        mods_page_module.config,
        "CONFIG_DIR",
        tmp_path / "launcher-state",
    )

    def unmanaged(_mod):
        raise ModNotManagedError("fixture mod is externally installed")

    monkeypatch.setattr(
        mods_page_module,
        "read_managed_mod_registration",
        unmanaged,
    )
    yield
    set_language(prior_language)


def _make_loader(root: Path, name: str = "Fixture Mod") -> None:
    loader = root / "mods" / name / "loader.js"
    loader.parent.mkdir(parents=True)
    loader.write_text("module.exports = {};\n", encoding="utf-8")


def _placeholder_names(template: str) -> set[str]:
    return {
        field_name
        for _literal, field_name, _format_spec, _conversion in Formatter().parse(
            template
        )
        if field_name is not None
    }


def test_automatic_client_package_is_enabled_without_server_apply(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS"
    package = root / "mods" / "DLSS5"
    package.mkdir(parents=True)
    manifest = package / "evejs-launcher.client-mod.json"
    manifest.write_text("{}", encoding="utf-8")
    client_mod = Mod(
        name="EveJS DLSS5",
        path=package,
        active=True,
        id="evejs-dlss5",
        version="0.4.0-test",
        activation_kind=ActivationKind.CLIENT_PACKAGE,
        supported_backends=("client",),
        restart_scope="client_launch",
        manifest_path=manifest,
        valid=True,
        evejs_root=root,
    )
    monkeypatch.setattr(
        mods_page_module,
        "discover_dlss5_client_mod",
        lambda _root: client_mod,
    )

    page = ModsPage()
    page.set_evejs_root(str(root))

    assert len(page._rows) == 1
    row = page._rows[0]
    assert row.kind_badge.text() == "GPU"
    assert row.state_label.text() == "ENABLED · AUTO"
    assert row.toggle.isChecked()
    assert not row.toggle.isEnabled()
    assert not page.apply_btn.isEnabled()


def test_refresh_and_constructor_never_create_a_missing_mod_folder(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS 用户"
    root.mkdir()
    monkeypatch.setattr(
        mods_page_module.config,
        "get_setting",
        lambda key, *_args: str(root) if key == "evejs_root" else "",
    )

    page = ModsPage()
    page.refresh_mods()

    assert not (root / "mods").exists()
    assert not page.create_mod_folder_btn.isHidden()
    assert page.create_mod_folder_btn.isEnabled()
    assert page.open_mod_folder_btn.isHidden()
    assert page.folder_guidance.text() == (
        "Create the Mods folder first. Then place each mod's folder inside it "
        "and click Refresh."
    )


def test_existing_folder_shows_open_action_and_install_guidance_with_rows(
    qapp,
    tmp_path: Path,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    _make_loader(root)

    page = ModsPage()
    page.set_evejs_root(str(root))

    assert len(page.mods()) == 1
    assert page.create_mod_folder_btn.isHidden()
    assert not page.open_mod_folder_btn.isHidden()
    assert page.open_mod_folder_btn.isEnabled()
    assert page.folder_guidance.text() == (
        "To add a mod, place the mod's folder inside this folder, then click "
        "Refresh."
    )
    assert "mod authors" in page.author_guide_label.text()


def test_create_button_creates_only_canonical_child_and_refreshes_controls(
    qapp,
    tmp_path: Path,
) -> None:
    root = tmp_path / "伊芙服务器"
    root.mkdir()
    page = ModsPage()
    page.set_evejs_root(str(root))

    page.create_mod_folder_btn.click()

    assert [entry.name for entry in root.iterdir()] == ["mods"]
    assert (root / "mods").is_dir()
    assert page.create_mod_folder_btn.isHidden()
    assert not page.open_mod_folder_btn.isHidden()
    assert "place the mod's folder" in page.folder_guidance.text()


def test_page_open_rechecks_folder_created_outside_the_launcher(
    qapp,
    tmp_path: Path,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    page = ModsPage()
    page.set_evejs_root(str(root))
    assert not page.create_mod_folder_btn.isHidden()

    (root / "mods").mkdir()
    page.show()
    qapp.processEvents()

    assert page.create_mod_folder_btn.isHidden()
    assert not page.open_mod_folder_btn.isHidden()
    page.close()


def test_open_folder_uses_unicode_safe_local_file_url(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS 日本語"
    mods = root / "mods"
    mods.mkdir(parents=True)
    opened = []
    monkeypatch.setattr(
        mods_page_module.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url) or True,
    )
    page = ModsPage()
    page.set_evejs_root(str(root))

    page.open_mod_folder_btn.click()

    assert len(opened) == 1
    assert opened[0].isLocalFile()
    assert Path(opened[0].toLocalFile()) == mods.resolve()


def test_author_guide_uses_pinned_exact_url(
    qapp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = []
    monkeypatch.setattr(
        mods_page_module.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url) or True,
    )
    page = ModsPage()

    page.mod_author_guide_btn.click()

    assert [url.toString() for url in opened] == [MOD_AUTHORING_GUIDE_URL]
    assert MOD_AUTHORING_GUIDE_URL == (
        "https://github.com/V0nCleef/evejs-launcher/blob/v1.0.45/"
        "docs/MOD_AUTHORING.md"
    )


def test_root_change_replaces_folder_state_without_stale_actions(
    qapp,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-mods"
    present = tmp_path / "present-mods"
    missing.mkdir()
    (present / "mods").mkdir(parents=True)
    page = ModsPage()

    page.set_evejs_root(str(missing))
    assert not page.create_mod_folder_btn.isHidden()
    assert page.open_mod_folder_btn.isHidden()

    page.set_evejs_root(str(present))
    assert page.create_mod_folder_btn.isHidden()
    assert not page.open_mod_folder_btn.isHidden()
    assert page.open_mod_folder_btn.toolTip() == str((present / "mods").resolve())


def test_invalid_or_relative_root_never_falls_back_to_working_directory(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mods_page_module.QMessageBox,
        "critical",
        lambda *_args: None,
    )
    page = ModsPage()
    relative_name = f"relative-evejs-{tmp_path.name}"
    cwd_candidate = Path.cwd() / relative_name
    assert not cwd_candidate.exists()

    page.set_evejs_root(relative_name)
    page._create_mod_folder()

    assert not cwd_candidate.exists()
    assert not page.create_mod_folder_btn.isEnabled()
    assert page.open_mod_folder_btn.isHidden()
    assert "not an absolute path" in page.folder_guidance.toolTip()


def test_existing_mods_file_is_an_error_and_creation_preserves_it(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    collision = root / "mods"
    collision.write_text("do not replace", encoding="utf-8")
    messages = []
    monkeypatch.setattr(
        mods_page_module.QMessageBox,
        "critical",
        lambda _parent, title, body: messages.append((title, body)),
    )
    page = ModsPage()
    page.set_evejs_root(str(root))

    page._create_mod_folder()

    assert collision.read_text(encoding="utf-8") == "do not replace"
    assert not page.create_mod_folder_btn.isEnabled()
    assert messages and str(collision) in messages[0][1]


def test_out_of_root_mods_link_is_rejected_when_links_are_available(
    qapp,
    tmp_path: Path,
) -> None:
    root = tmp_path / "EveJS"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "mods").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory links are unavailable: {exc}")

    page = ModsPage()
    page.set_evejs_root(str(root))

    assert not page.create_mod_folder_btn.isEnabled()
    assert page.open_mod_folder_btn.isHidden()
    assert "outside the configured EveJS root" in page.folder_guidance.toolTip()


def test_mods_link_back_to_root_is_not_accepted_as_the_mods_child(
    qapp,
    tmp_path: Path,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    try:
        (root / "mods").symlink_to(root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory links are unavailable: {exc}")

    page = ModsPage()
    page.set_evejs_root(str(root))

    assert not page.create_mod_folder_btn.isEnabled()
    assert page.open_mod_folder_btn.isHidden()
    assert "outside the configured EveJS root" in page.folder_guidance.toolTip()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_windows_junction_outside_root_is_rejected_without_touching_target(
    qapp,
    tmp_path: Path,
) -> None:
    from src.core.platform_win import create_directory_link, remove_directory_link

    root = tmp_path / "EveJS"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    link = root / "mods"
    try:
        create_directory_link(outside, link)
    except RuntimeError as exc:
        pytest.skip(f"directory junctions are unavailable: {exc}")

    try:
        page = ModsPage()
        page.set_evejs_root(str(root))

        assert not page.create_mod_folder_btn.isEnabled()
        assert page.open_mod_folder_btn.isHidden()
        assert "outside the configured EveJS root" in page.folder_guidance.toolTip()
        assert sentinel.read_text(encoding="utf-8") == "keep"
    finally:
        if os.path.lexists(str(link)):
            remove_directory_link(link)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_connect_only_keeps_open_and_guide_but_disables_folder_creation(
    qapp,
    tmp_path: Path,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    page = ModsPage()
    page.set_evejs_root(str(root))

    page.set_runtime_context(
        RuntimeBackend.DOCKER_COMPOSE,
        DockerControlPolicy.CONNECT_ONLY,
    )

    assert not page.create_mod_folder_btn.isEnabled()
    assert "Connect-only" in page.create_mod_folder_btn.toolTip()
    assert page.mod_author_guide_btn.isEnabled()


def test_folder_controls_and_guidance_retranslate_live(
    qapp,
    tmp_path: Path,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    page = ModsPage()
    page.set_evejs_root(str(root))

    set_language("ja")
    retranslate_widget_tree(page, "ja")

    assert page.folder_mark.text() == "フォルダ"
    assert page.create_mod_folder_btn.text() == "MOD フォルダーを作成"
    assert "まず MOD フォルダー" in page.folder_guidance.text()
    assert page.mod_author_guide_btn.text() == "MOD 作者ガイド ↗"
    assert page.open_mod_folder_btn.accessibleDescription() == (
        "設定済みの MOD フォルダーをエクスプローラーで開きます。"
    )

    (root / "mods").mkdir()
    page.refresh_mods()
    assert page.open_mod_folder_btn.text() == "MOD フォルダーを開く"
    assert "MOD を追加するには" in page.folder_guidance.text()


def test_launcher_owned_folder_diagnostic_retranslates_and_preserves_raw_path(
    qapp,
    tmp_path: Path,
) -> None:
    page = ModsPage()
    raw_root = f"相対-EveJS-{tmp_path.name}"
    page.set_evejs_root(raw_root)

    assert page.folder_guidance.toolTip() == (
        f"The configured EveJS root is not an absolute path: {raw_root}"
    )

    set_language("ja")
    retranslate_widget_tree(page, "ja")
    assert page.folder_guidance.toolTip() == (
        f"設定された EveJS ルートは絶対パスではありません：{raw_root}"
    )
    assert page.create_mod_folder_btn.toolTip() == page.folder_guidance.toolTip()

    set_language("ru")
    retranslate_widget_tree(page, "ru")
    assert page.folder_guidance.toolTip() == (
        f"Настроенный корневой путь EveJS не является абсолютным: {raw_root}"
    )
    assert page.create_mod_folder_btn.toolTip() == page.folder_guidance.toolTip()


def test_create_error_translates_launcher_detail_and_preserves_raw_path(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = []
    monkeypatch.setattr(
        mods_page_module.QMessageBox,
        "critical",
        lambda _parent, title, body: warnings.append((title, body)),
    )
    raw_root = f"относительный-EveJS-{tmp_path.name}"
    page = ModsPage()
    page.set_evejs_root(raw_root)
    set_language("ru")

    page._create_mod_folder()

    assert len(warnings) == 1
    assert raw_root in warnings[0][1]
    assert "Настроенный корневой путь EveJS не является абсолютным" in warnings[0][1]


def test_open_and_guide_failures_are_visible_and_preserve_raw_details(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS 用户"
    (root / "mods").mkdir(parents=True)
    warnings = []
    monkeypatch.setattr(
        mods_page_module.QDesktopServices,
        "openUrl",
        lambda _url: False,
    )
    monkeypatch.setattr(
        mods_page_module.QMessageBox,
        "warning",
        lambda _parent, title, body: warnings.append((title, body)),
    )
    set_language("ja")
    page = ModsPage()
    page.set_evejs_root(str(root))

    page._open_mod_folder()
    page._open_mod_author_guide()

    assert len(warnings) == 2
    assert str((root / "mods").resolve()) in warnings[0][1]
    assert "エクスプローラーがフォルダー URL を受け付けませんでした" in warnings[0][1]
    assert warnings[1] == (
        "Mod Author Guide",
        "The mod-authoring guide could not be opened in your default browser.",
    )
    assert (root / "mods").is_dir()


@pytest.mark.parametrize(
    "language",
    tuple(option.code for option in LANGUAGES),
)
def test_new_mod_folder_ui_has_complete_translations_and_placeholder_parity(
    language: str,
) -> None:
    for source in NEW_UI_PHRASES:
        translated = translate_ui_phrase(source, language)
        assert _placeholder_names(translated) == _placeholder_names(source)
        if language == "en":
            assert translated == source
        else:
            assert translated != source
