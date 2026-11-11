from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication, QMainWindow

from src.core.installation_profiles import ensure_profiles, add_profile, select_profile, InstallationProfileError
from src.ui.installation_coordinator import InstallationBar


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def window(tmp_path):
    w = QMainWindow()
    w._cfg = add_profile(ensure_profiles({"evejs_root":str(tmp_path/"normal"),"hidden_characters":["Same Pilot"]}),
                        "Ironman",{"evejs_root":str(tmp_path/"ironman"),"game_port":26100})
    w._lifecycle_active = lambda:False
    w._open_settings_page = lambda:None
    w._tracker = SimpleNamespace(running_count=0)
    w._settings_page = SimpleNamespace(is_dirty=lambda:False,load_settings=lambda:None)
    w.applied = []
    def apply(cfg):
        w._cfg = dict(cfg)
        w.applied.append(cfg)
    w._on_settings_saved = apply
    return w


def test_selection_changes_only_registration_and_private_preferences(qapp,tmp_path,monkeypatch):
    w = window(tmp_path)
    saved = []
    monkeypatch.setattr("src.ui.installation_coordinator.config.save",lambda cfg:saved.append(cfg))
    bar = InstallationBar(w)
    # QWidget.window() is used by the real shell event filter when a combo
    # popup opens. The owner reference must not replace that Qt method.
    assert bar.window() is w
    bar.selector.setCurrentIndex(1)
    assert w._cfg["evejs_root"].endswith("ironman") and w._cfg["hidden_characters"] == []
    assert w._cfg["game_port"] == 26100 and len(saved) == 1
    bar.selector.setCurrentIndex(0)
    assert w._cfg["hidden_characters"] == ["Same Pilot"]
    assert not (tmp_path/"normal").exists() and not (tmp_path/"ironman").exists()
    w.close()


@pytest.mark.parametrize("busy",["_mod_lifecycle_lease","_docker_character_token","_client_launch_thread"])
def test_switch_is_blocked_during_a_retained_operation(qapp,tmp_path,monkeypatch,busy):
    w = window(tmp_path)
    monkeypatch.setattr("src.ui.installation_coordinator.QMessageBox.information",lambda *a:None)
    monkeypatch.setattr("src.ui.installation_coordinator.config.save",lambda cfg:pytest.fail("Busy selection must not save"))
    bar = InstallationBar(w)
    setattr(w,busy,object())
    bar.selector.setCurrentIndex(1)
    assert w._cfg["active_installation_profile"] == "default" and bar.selector.currentIndex() == 0
    w.close()


def test_duplicate_physical_root_and_ambiguous_names_are_rejected(tmp_path):
    cfg = ensure_profiles({"evejs_root":str(tmp_path/"normal")})
    with pytest.raises(InstallationProfileError,match="already registered"):
        add_profile(cfg,"Alias",{"evejs_root":str(tmp_path/"normal/../normal")})
    with pytest.raises(InstallationProfileError,match="name"):
        add_profile(cfg,"DEFAULT",{"evejs_root":str(tmp_path/"other")})


def test_equal_character_ids_keep_separate_overview_state(qapp,tmp_path,monkeypatch):
    from src.app import MainWindow
    from src.core.overview_state import add_pending_overview_import, pending_overview_source
    import src.app as app
    monkeypatch.setattr(app,"PROFILES_ROOT",tmp_path/"legacy/Profiles")
    w = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(w)
    w._cfg = add_profile(ensure_profiles({"evejs_root":"normal"}),"Ironman",{"evejs_root":"ironman"})
    other_id = w._cfg["installation_profiles"][-1]["id"]
    w._cfg = select_profile(w._cfg,other_id)
    scope = w._overview_kwargs()
    add_pending_overview_import(7,11,**scope)
    assert pending_overview_source(7,**scope) == 11
    third = add_profile(w._cfg,"Other world",{"evejs_root":"other"})
    w._cfg = select_profile(third,third["installation_profiles"][-1]["id"])
    assert pending_overview_source(7,**w._overview_kwargs()) is None
    w.close = lambda:None


@pytest.mark.parametrize("width", [1000, 1389])
def test_installation_controls_share_one_footer_row_before_version(qapp, tmp_path, width):
    from PyQt6.QtCore import QPoint
    from src.theme import build_qss
    from src.widgets.status_bar import StatusBar
    w = window(tmp_path)
    original = qapp.styleSheet()
    qapp.setStyleSheet(build_qss({"header":"Segoe UI", "body":"Segoe UI", "mono":"Consolas"}))
    bar = InstallationBar(w)
    footer = StatusBar(w)
    try:
        footer.set_installation_controls(bar)
        footer.resize(width, footer.height())
        footer.show()
        w.show()
        qapp.processEvents()
        assert footer.width() == width
        assert footer.height() == StatusBar.HEIGHT
        selector = bar.selector.mapTo(footer, QPoint())
        version = footer.version_label.mapTo(footer, QPoint())
        language = footer.language_combo.mapTo(footer, QPoint())
        assert abs(selector.y() + bar.selector.height() / 2 - (language.y() + footer.language_combo.height() / 2)) < 5
        assert bar.mapTo(footer, QPoint(bar.width(), 0)).x() < version.x()
        assert abs(selector.y() + bar.selector.height() / 2 - (version.y() + footer.version_label.height() / 2)) < 5
        assert bar.selector.minimumWidth() >= 120
        assert len(bar.management_menu.actions()) == 4
        assert bar.menu_button.isVisible() == bar._compact
        for button, action in zip(bar.management_buttons, bar.management_menu.actions()):
            assert button.isVisible() != bar._compact
            assert action.text() == button.text()
            if button.isVisible():
                assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 12
        assert bar.logs.isVisible()
        log_position = bar.logs.mapTo(footer, QPoint())
        assert abs(log_position.y() + bar.logs.height() / 2 - (selector.y() + bar.selector.height() / 2)) < 5
    finally:
        footer.close(); w.close(); qapp.setStyleSheet(original)


def test_compact_installation_menu_keeps_the_existing_button_actions(qapp, tmp_path):
    w = window(tmp_path)
    bar = InstallationBar(w)
    triggered = []
    for index, button in enumerate(bar.management_buttons):
        button.clicked.disconnect()
        button.clicked.connect(lambda _checked=False, value=index: triggered.append(value))
    bar.set_compact(True)
    for action in bar.management_menu.actions():
        action.trigger()
    assert triggered == [0, 1, 2, 3]
    w.close()
