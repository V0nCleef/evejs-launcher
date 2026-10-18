"""Record transient Show events, including windows gone before the next paint."""
from types import SimpleNamespace
from copy import deepcopy
import time

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QWidget

from src import config
from src.app import MainWindow
from src.core.mod_activation_state import project_mod_activation
from src.core.mod_inventory import ModInventory
from src.core.mod_manifest import Mod
from src.core.tool_catalog import supported_tool_definitions
from src.pages import mods_page
from src.pages.tools_page import ToolsPage


class WindowShows(QObject):
    def __init__(self):
        super().__init__()
        self.events = []

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            self.events.append((type(obj).__name__, getattr(obj, "text", lambda: "")()))
        return False


def test_mod_controls_never_show_as_independent_windows(qapp, tmp_path):
    mod = Mod("Example", tmp_path / "mods/Example", True,
        evejs_root=tmp_path, settings_schema={"schemaVersion": 1})
    projection = project_mod_activation(mod, None)
    mod.api_descriptor = SimpleNamespace(launcher_api=SimpleNamespace(capabilities=("verify",)))
    parent = QWidget()
    observer = WindowShows()
    qapp.installEventFilter(observer)
    row = None
    try:
        row = mods_page.ModRow(mod, projection=projection, projection_resolver=lambda _: projection,
            delegated_activation=True, can_move_up=True, can_move_down=True,
            client_script_delivery="client-script-patch", parent=parent)
        assert observer.events == []
        for control in (row.delivery_notice, row.configure_btn, row.helper_btn,
                        row.move_up_btn, row.move_down_btn):
            assert control.window() is parent
            assert not control.isHidden(), "The fix must preserve available controls"
    finally:
        qapp.removeEventFilter(observer)
        parent.close()
        parent.deleteLater()


def test_startup_inventory_and_visible_mods_refresh_never_show_extra_windows(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_setting", lambda *_: "")
    monkeypatch.setattr(mods_page, "reported_delivery", lambda *_: None)
    root = tmp_path / "Example EveJS"
    root.mkdir()
    mods = []
    for name in ("Example A", "Example B"):
        folder = root / "mods" / name
        folder.mkdir(parents=True)
        (folder / "loader.js").write_text("// Example mod\n")
        mods.append(Mod(name, folder, True, evejs_root=root, settings_schema={"schemaVersion": 1}))
    page = mods_page.ModsPage(defer_inventory=True)
    page.set_evejs_root(str(root))
    inventory = ModInventory(str(root), mods=tuple(mods))
    observer = WindowShows()
    qapp.installEventFilter(observer)
    try:
        page.show_inventory(inventory)  # Initial scan while the Mods page is hidden.
        assert observer.events == []
        qapp.removeEventFilter(observer)
        page.show()
        qapp.processEvents()
        qapp.installEventFilter(observer)
        for _ in range(3):
            page.show_inventory(inventory)
            qapp.processEvents()
        assert observer.events == []
        assert len(page._rows) == 2
        assert all(row.configure_btn.isVisible() for row in page._rows)
    finally:
        qapp.removeEventFilter(observer)
        page.close()
        page.deleteLater()


def test_full_launcher_startup_and_mods_navigation_show_only_main_window(qapp, tmp_path, monkeypatch):
    root = tmp_path / "Example EveJS"
    (root / "server").mkdir(parents=True)
    (root / "server/index.js").write_text("// Never executed\n")
    # Startup builds Tools too. An empty tools folder misses its risk badges.
    for definition in supported_tool_definitions():
        wrapper = root / "tools" / definition.relative_entrypoint
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("@echo off\n")
    for name in ("Example A", "Example B"):
        folder = root / "mods" / name
        folder.mkdir(parents=True)
        (folder / "loader.js").write_text("// Example mod\n")
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(evejs_root=str(root), client_path="", update_auto_check=False,
        auto_start_server=False, auto_start_market=False, animations_enabled=False,
        audio_music_enabled=False, audio_voice_enabled=False)
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda *_: None)
    observer = WindowShows()
    qapp.installEventFilter(observer)
    window = None
    try:
        window = MainWindow()
        window.show()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            qapp.processEvents()
            thread = window._mod_coordinator._thread
            if thread is None:
                break
            thread.wait(5)
        assert window._mod_coordinator._thread is None
        assert observer.events == [("MainWindow", "")]
        observer.events.clear()
        for page_index in (2, 0, 2):
            window._stack.setCurrentIndex(page_index)
            window._mods_page.refresh_mods()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                qapp.processEvents()
                thread = window._mod_coordinator._thread
                if thread is None:
                    break
                thread.wait(5)
            assert window._mod_coordinator._thread is None
        assert observer.events == []
        assert len(window._mods_page._rows) == 2
    finally:
        qapp.removeEventFilter(observer)
        if window is not None:
            qapp.removeEventFilter(window)
            window.close()
            window.deleteLater()
        qapp.processEvents()


def test_tools_badges_never_show_as_desktop_windows_on_build_or_refresh(qapp, tmp_path):
    for definition in supported_tool_definitions():
        wrapper = tmp_path / "tools" / definition.relative_entrypoint
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("@echo off\n")
    observer = WindowShows()
    qapp.installEventFilter(observer)
    page = None
    try:
        page = ToolsPage(str(tmp_path))
        assert observer.events == []
        qapp.removeEventFilter(observer)
        page.show()
        qapp.processEvents()
        qapp.installEventFilter(observer)
        for _ in range(2):
            page.refresh_tools()
            qapp.processEvents()
        assert observer.events == []
        assert any(page.card_for(d.id).risk_label.isVisible()
                   for d in supported_tool_definitions() if d.prerequisite_label)
    finally:
        qapp.removeEventFilter(observer)
        if page is not None:
            page.close()
            page.deleteLater()
