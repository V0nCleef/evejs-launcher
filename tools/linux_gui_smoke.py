"""Native X11 Qt render/close check, using disposable configuration only."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(QT_QPA_PLATFORM='xcb', XDG_CONFIG_HOME='/tmp/evejs-x11-config', XDG_STATE_HOME='/tmp/evejs-x11-state')

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from src import config, theme
from src.app import MainWindow

app = QApplication([])
config.save(dict(config.DEFAULT_CONFIG, update_auto_check=False, audio_master_muted=True,
                 audio_music_enabled=False, audio_voice_enabled=False, close_behavior='exit'))
app.setStyleSheet(theme.build_qss(theme.load_fonts()))
window = MainWindow()
window.show()


def capture():
    output = ROOT / '.qa/linux-main.png'
    if not window.grab().save(str(output)):
        raise RuntimeError('Linux window capture failed')
    from src.widgets.linux_maintenance_dialog import LinuxMaintenanceDialog
    window._qa_maintenance = LinuxMaintenanceDialog('', window)
    window._qa_maintenance.show()
    QTimer.singleShot(300, capture_maintenance)


def capture_maintenance():
    dialog = window._qa_maintenance
    if not dialog.grab().save(str(ROOT / '.qa/linux-maintenance.png')):
        raise RuntimeError('Linux maintenance capture failed')
    dialog.close()
    window.close()


QTimer.singleShot(800, capture)
QTimer.singleShot(10000, lambda: app.exit(124))
code = app.exec()
print(json.dumps({'nativeX11': True, 'exitCode': code, 'visibleSize': [window.width(), window.height()]}))
raise SystemExit(code)
