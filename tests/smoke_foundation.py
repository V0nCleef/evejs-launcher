"""Quick smoke-test for foundation modules."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.constants import APP_NAME, APP_TITLE, COLORS, Page, Ports, Status
from src.theme import build_qss, load_fonts
from src.utils.cache import PortraitCache
from src.utils.logger import setup_logger

app = QApplication([])

# constants
assert COLORS["teal"] == "#00C8E0"
assert Status.READY.value == "ready"
assert Page.HOME == 0 and Page.SETTINGS == 3
assert Ports.GAME_TCP == 26000
assert Ports.GAME_MARKET_PROXY == 26001
assert Ports.CLIENT_HTTP_PROXY == 26002
assert Ports.MARKET_HTTP == 40110
assert Ports.MARKET_RPC == 40111
print(f"constants OK  | APP_NAME={APP_NAME!r}  APP_TITLE={APP_TITLE!r}")

# theme
fonts = load_fonts()
print(f"fonts OK      | {fonts}")
qss = build_qss(fonts)
assert len(qss) > 1000, "QSS looks too small"
for token in ["QPushButton", "QLineEdit:focus", "QScrollBar::handle:vertical",
              "QCheckBox::indicator", "QGroupBox::title", "QTabBar::tab",
              "QToolTip", "QMenu::item", "QProgressBar::chunk",
              "QFrame#navPanel", "QFrame#detailPanel", "QFrame#statusBar",
              COLORS["teal"], COLORS["void_black"]]:
    assert token in qss, f"missing QSS token: {token}"
print(f"theme OK      | {len(qss)} chars, all tokens present")

# logger
log = setup_logger("evejs.test")
log.info("smoke-test log line")
log.info("second line")
log2 = setup_logger("evejs.test")
assert log is log2 and len(log2.handlers) == 1, "duplicate handlers added"
print(f"logger OK     | file={log.handlers[0].baseFilename}")

# cache
from PyQt6.QtGui import QColor, QPixmap
pm = QPixmap(64, 64)
pm.fill(QColor(COLORS["teal"]))
PortraitCache.put("test_64", pm)
out = PortraitCache.get("test_64")
assert out is not None and not out.isNull() and out.width() == 64
assert PortraitCache.get("missing") is None
PortraitCache.set_limit(16)
PortraitCache.clear()
assert PortraitCache.get("test_64") is None
print("cache OK      | put/get/clear/set_limit verified")

print("\nALL FOUNDATION MODULES OK")
