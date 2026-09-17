"""Render actual launcher widgets with disposable, fictional guide data.

No gameplay, user configuration, client or live server is accessed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Render fonts and widget edges at twice their displayed resolution.
os.environ["QT_SCALE_FACTOR"] = "2"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from main import _build_palette
from src import config, theme
from src.core.local_mod_packages import LocalModPackages
from src.core.mod_activation_state import ModActivationProjection, ModActivationStatus
from src.core.mod_manifest import scan_mods
from src.core.mod_settings_schema import parse_settings_schema
from src.i18n import set_language
from src.pages.mods_page import ModRow
from src.widgets.mod_settings_dialog import ModSettingsDialog


def capture(app: QApplication, widget: QWidget, path: Path) -> None:
    widget.show()
    app.processEvents()
    screenshot = widget.grab().toImage()
    screenshot.setText("logicalWidth", str(widget.width()))
    screenshot.save(str(path))
    # The fixture intentionally shows a dirty draft. Do not invoke the real
    # discard-confirmation flow while rendering offscreen documentation.
    widget.hide()
    widget.deleteLater()
    app.processEvents()


def main() -> None:
    app = QApplication.instance() or QApplication([])
    if os.name == "nt":
        for font in ("segoeui.ttf", "bahnschrift.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font))
    app.setPalette(_build_palette())
    app.setStyleSheet(theme.build_qss(theme.load_fonts()))
    set_language("en")
    destination = ROOT / "docs/how-to-make-a-mod/images"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="evejs-guide-demo-") as folder:
        root = Path(folder)
        config.CONFIG_DIR = root / "launcher-state"
        config.CONFIG_FILE = config.CONFIG_DIR / "config.json"
        LocalModPackages(root).import_package(ROOT / "examples/mods/configure-demo")
        mod = next(item for item in scan_mods(root) if item.id == "configure-demo")
        projection = ModActivationProjection(ModActivationStatus.VERIFIED, False, False, False, None, None, "guide-demo")
        frame = QWidget()
        frame.resize(960, 142)
        layout = QVBoxLayout(frame)
        label = QLabel("DEMO  /  Mods — a settings package with Configure")
        label.setProperty("class", "secondary")
        layout.addWidget(label)
        row = ModRow(mod, projection=projection, projection_resolver=lambda _: projection,
                     local_removable=True, can_remove=True, delegated_activation=True)
        layout.addWidget(row)
        capture(app, frame, destination / "configure-row.png")
        frame = QWidget()
        frame.resize(1080, 142)
        layout = QVBoxLayout(frame)
        label = QLabel("DEMO / Scan Settings Demo 1.0.0 — update 1.2.3 available")
        label.setProperty("class", "secondary")
        layout.addWidget(label)
        row = ModRow(mod, projection=projection, projection_resolver=lambda _: projection,
                     local_removable=True, can_remove=True, delegated_activation=True)
        row.update_btn.set_update_available("1.2.3")
        row.update_btn._sync_motion(False)
        layout.addWidget(row)
        capture(app, frame, destination / "update-available.png")
        schema = parse_settings_schema(mod.settings_schema)
        dialog = ModSettingsDialog(mod.name, schema.fields, {"scanInterval": 10},
                                   scope_label="DEMO / Global preferences — no gameplay")
        dialog.resize(690, 450)
        dialog.setMinimumSize(520, 400)
        dialog._controls["scanInterval"].setValue(20)
        capture(app, dialog, destination / "configure-dialog.png")
        raw = json.loads((ROOT / "examples/mods/configure-demo/evejs-launcher.mod.json").read_text())
        raw["settings"]["fields"] = [
            {"id": "hints", "label": "Show hints", "type": "boolean", "default": True, "file": "prefs", "key": ["hints"]},
            {"id": "interval", "label": "Interval (seconds)", "type": "integer", "default": 10, "minimum": 1, "maximum": 300, "file": "prefs", "key": ["interval"]},
            {"id": "strength", "label": "Effect strength", "type": "number", "default": 0.5, "minimum": 0, "maximum": 1, "step": 0.1, "file": "prefs", "key": ["strength"]},
            {"id": "label", "label": "Profile label", "type": "string", "default": "Pilot A", "file": "prefs", "key": ["label"]},
            {"id": "quality", "label": "Quality", "type": "choice", "default": "high", "choices": [{"value": "low", "label": "Low"}, {"value": "high", "label": "High"}], "file": "prefs", "key": ["quality"]},
        ]
        schema = parse_settings_schema(raw["settings"])
        dialog = ModSettingsDialog("Form Controls Demo", schema.fields, {},
                                   scope_label="DEMO / Five field types — no gameplay")
        dialog.resize(690, 760)
        capture(app, dialog, destination / "controls.png")
        from mod_guide_scenes import render_scenes
        extra = render_scenes(app, root, destination, capture, ROOT)
    print(f"Rendered {4 + len(extra)} demo screenshots in {destination}")


if __name__ == "__main__":
    main()
