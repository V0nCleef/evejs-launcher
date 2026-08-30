"""Community Chinese phrase translation without semantic-widget mutation."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.i18n import set_language
from src.constants import Status
from src.widgets.character_card import CharacterCard
from src.widgets.localized_dialogs import LocalizedMessageBox
from src.widgets.ui_translation import (
    mark_translatable,
    register_translatable_widget_tree,
    retranslate_widget_tree,
    set_translatable_text_template,
    set_translatable_tooltip,
)


@pytest.fixture(autouse=True)
def reset_language() -> None:
    """Keep process-global UI language from leaking into later test modules."""

    set_language("en")
    yield
    set_language("en")


def test_community_chinese_phrases_translate_and_switch_back_losslessly(
    qapp: QApplication,
) -> None:
    set_language("en")
    root = QWidget()
    layout = QVBoxLayout(root)
    heading = mark_translatable(QLabel("LATEST RELEASE"))
    launch = mark_translatable(QPushButton("Launch"))
    uppercase_launch = mark_translatable(QPushButton("LAUNCH"))
    group = mark_translatable(QGroupBox("Danger Zone"))
    search = QLineEdit()
    search.setPlaceholderText("Search tools…")
    mark_translatable(search)
    semantic_combo = QComboBox()
    semantic_combo.addItems(["Runtime", "Launch"])
    # These deliberately collide with real UI phrases.  They represent a
    # character/mod/ship name and must remain untouched unless opted in.
    user_values = [QLabel("Settings"), QLabel("Launch"), QLabel("Market")]
    ignored = mark_translatable(QLabel("Settings"))
    ignored.setProperty("i18nIgnore", True)
    for widget in (
        heading,
        launch,
        uppercase_launch,
        group,
        search,
        semantic_combo,
        *user_values,
        ignored,
    ):
        layout.addWidget(widget)

    retranslate_widget_tree(root, "zh_CN")

    assert heading.text() == "最新版本"
    assert launch.text() == "启动"
    assert uppercase_launch.text() == "启动"
    assert group.title() == "危险区域"
    assert search.placeholderText() == "搜索工具…"
    assert [semantic_combo.itemText(index) for index in range(2)] == [
        "Runtime",
        "Launch",
    ]
    assert [value.text() for value in user_values] == [
        "Settings",
        "Launch",
        "Market",
    ]
    assert ignored.text() == "Settings"

    retranslate_widget_tree(root, "en")

    assert launch.text() == "Launch"
    assert uppercase_launch.text() == "LAUNCH"
    assert heading.text() == "LATEST RELEASE"
    root.deleteLater()


def test_dynamic_count_text_is_rebased_before_retranslation(
    qapp: QApplication,
) -> None:
    set_language("en")
    label = mark_translatable(QLabel("1 client running"))

    retranslate_widget_tree(label, "zh_CN")
    assert label.text() == "1 个客户端运行中"

    label.setText("2 clients running")
    retranslate_widget_tree(label, "zh_CN")
    assert label.text() == "2 个客户端运行中"

    retranslate_widget_tree(label, "en")
    assert label.text() == "2 clients running"
    label.deleteLater()


def test_registration_does_not_claim_user_widgets_added_later(
    qapp: QApplication,
) -> None:
    set_language("en")
    root = QWidget()
    layout = QVBoxLayout(root)
    static_heading = QLabel("Settings")
    layout.addWidget(static_heading)

    register_translatable_widget_tree(root)

    character_name = QLabel("Settings")
    mod_name = QLabel("Market")
    ship_name = QLabel("Launch")
    for widget in (character_name, mod_name, ship_name):
        layout.addWidget(widget)

    retranslate_widget_tree(root, "zh_CN")

    assert static_heading.text() == "设置"
    assert character_name.text() == "Settings"
    assert mod_name.text() == "Market"
    assert ship_name.text() == "Launch"

    retranslate_widget_tree(root, "en")
    assert static_heading.text() == "Settings"
    root.deleteLater()


def test_character_card_never_translates_colliding_user_data(
    qapp: QApplication,
) -> None:
    set_language("zh_CN")
    card = CharacterCard(
        username="Market",
        char_name="Settings",
        char_id=42,
        ship="Launch",
        status=Status.READY,
    )

    assert card.char_name == "Settings"
    assert card.username == "Market"
    assert card.ship == "Launch"
    assert card._name_label.toolTip() == "Settings"
    assert card._account_label.text() == "Market"
    assert card._ship_label.text() == "Launch"
    assert card._launch_btn.text() == "启动"

    set_language("en")
    retranslate_widget_tree(card, "en")
    assert card._launch_btn.text() == "LAUNCH"
    card.deleteLater()


def test_localized_message_box_translates_owned_frame_but_preserves_diagnostic(
    qapp: QApplication,
) -> None:
    set_language("zh_CN")
    source = (
        "Delete Character?\n\nPilot 42\n\n"
        "EveJS will run its native character cleanup. The launcher will keep "
        "a recoverable backup of every affected table and portrait. Account "
        "profile/settings folders are preserved."
    )
    box = LocalizedMessageBox()
    box.setWindowTitle("Confirm EveJS Deletion")
    box.setText(source)
    diagnostic = "Could not create a backup of every affected table."
    box.setDetailedText(diagnostic)

    box._localize_contents()

    assert box.windowTitle() != "Confirm EveJS Deletion"
    assert box.text().startswith("删除Character？\n\nPilot 42")
    assert box.detailedText() == diagnostic
    box.deleteLater()


def test_localized_message_box_handles_qt_escaped_custom_button_caption(
    qapp: QApplication,
) -> None:
    set_language("zh_CN")
    box = LocalizedMessageBox()
    install = box.addButton(
        "Download && Install",
        QMessageBox.ButtonRole.ActionRole,
    )
    unknown = box.addButton(
        r"C:\用户 && Settings",
        QMessageBox.ButtonRole.ActionRole,
    )

    try:
        box._localize_contents()

        assert install.text() == "下载并安装"
        assert unknown.text() == r"C:\用户 && Settings"
    finally:
        set_language("en")
        box.deleteLater()


def test_exact_runtime_setter_does_not_guess_broad_templates(
    qapp: QApplication,
) -> None:
    set_language("zh_CN")
    label = QLabel()
    diagnostic = "Unexpected backup of every table"

    set_translatable_tooltip(label, diagnostic)

    assert label.toolTip() == diagnostic
    label.deleteLater()


def test_explicit_runtime_template_setter_translates_and_switches_losslessly(
    qapp: QApplication,
) -> None:
    set_language("zh_CN")
    label = QLabel()

    set_translatable_text_template(label, "Update v1.2.3")
    assert label.text() != "Update v1.2.3"

    retranslate_widget_tree(label, "en")
    assert label.text() == "Update v1.2.3"
    label.deleteLater()
