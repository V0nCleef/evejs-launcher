"""Qt Style Sheet (QSS) theme builder for EveJS Launcher V2."""
from __future__ import annotations

from PyQt6.QtGui import QFontDatabase

from .constants import COLORS


def load_fonts() -> dict[str, str]:
    """Probe the system font database and return the best available families.

    Returns a dict with keys ``header``, ``body`` and ``mono``.
    """
    available = {f.lower(): f for f in QFontDatabase.families()}

    header = (
        available.get("rajdhani")
        or available.get("eve sans")
        or "Segoe UI"
    )
    body = (
        available.get("inter")
        or available.get("eve sans")
        or "Segoe UI"
    )
    mono = (
        available.get("jetbrains mono")
        or available.get("consolas")
        or "Courier New"
    )

    return {"header": header, "body": body, "mono": mono}


def build_qss(fonts: dict[str, str]) -> str:
    """Return the complete application QSS string.

    Args:
        fonts: Mapping with keys ``header``, ``body`` and ``mono``
               (as returned by :func:`load_fonts`).
    """
    c = COLORS
    header = fonts["header"]
    body = fonts["body"]
    mono = fonts["mono"]

    return f"""
/* ── Base ─────────────────────────────────────────────────────────────────── */
QMainWindow, QWidget {{
    background-color: {c['void_black']};
    color: {c['white']};
    font-family: '{body}';
    font-size: 13px;
}}

/* ── Push Buttons ─────────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {c['carbon']};
    border: 1px solid {c['steel']};
    border-radius: 4px;
    color: {c['white']};
    padding: 8px 16px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {c['steel']};
    border-color: {c['teal_dim']};
}}
QPushButton:pressed {{
    background-color: {c['deep_space']};
}}
QPushButton:disabled {{
    background-color: {c['deep_space']};
    color: {c['grey']};
    border-color: {c['carbon']};
}}

QPushButton[class="primary"] {{
    background-color: {c['teal']};
    border: none;
    color: {c['void_black']};
    font-weight: 600;
}}
QPushButton[class="primary"]:hover {{
    background-color: {c['teal_dim']};
    color: {c['void_black']};
}}
QPushButton[class="primary"]:pressed {{
    background-color: {c['teal_dim']};
}}

QPushButton[class="danger"] {{
    background-color: {c['red']};
    border: none;
    color: {c['void_black']};
    font-weight: 600;
}}
QPushButton[class="danger"]:hover {{
    background-color: {c['red']};
    opacity: 0.9;
}}
QPushButton[class="danger"]:pressed {{
    background-color: {c['red']};
    opacity: 0.8;
}}

QPushButton[class="ghost"] {{
    background-color: transparent;
    border: 1px solid {c['steel']};
    color: {c['grey']};
}}
QPushButton[class="ghost"]:hover {{
    border-color: {c['teal']};
    color: {c['teal']};
}}

QPushButton[class="nav"] {{
    background-color: transparent;
    border: none;
    border-radius: 0;
    color: {c['grey']};
    text-align: left;
    padding: 12px 16px;
    font-family: '{header}';
    font-size: 14px;
    font-weight: 600;
}}
QPushButton[class="nav"]:hover {{
    color: {c['white']};
    background-color: {c['carbon']};
}}
QPushButton[class="nav"][active="true"] {{
    color: {c['teal']};
    background-color: {c['carbon']};
    border-left: 3px solid {c['teal']};
}}

/* ── Labels ───────────────────────────────────────────────────────────────── */
QLabel {{
    background: transparent;
}}
QLabel[class="title"] {{
    color: {c['white']};
    font-family: '{header}';
    font-size: 22px;
    font-weight: 700;
}}
QLabel[class="secondary"] {{
    color: {c['grey']};
    font-size: 13px;
}}
QLabel[class="muted"] {{
    color: {c['grey']};
    font-size: 11px;
}}

/* ── Frames / Panels ──────────────────────────────────────────────────────── */
QFrame#navPanel {{
    background-color: {c['deep_space']};
    border-right: 1px solid {c['steel']};
}}
QFrame#detailPanel {{
    background-color: {c['deep_space']};
    border-left: 1px solid {c['steel']};
}}
QFrame#statusBar {{
    background-color: {c['deep_space']};
    border-top: 1px solid {c['steel']};
}}
QFrame[class="card"] {{
    background-color: {c['carbon']};
    border: 1px solid {c['steel']};
    border-radius: 6px;
}}
QFrame[class="card"]:hover {{
    border-color: {c['teal_dim']};
}}

/* ── Line Edit ────────────────────────────────────────────────────────────── */
QLineEdit {{
    background-color: {c['deep_space']};
    border: 1px solid {c['steel']};
    border-radius: 4px;
    color: {c['white']};
    padding: 6px 10px;
    selection-background-color: {c['teal_dim']};
}}
QLineEdit:focus {{
    border-color: {c['teal']};
}}
QLineEdit:disabled {{
    background-color: {c['carbon']};
    color: {c['grey']};
}}

/* ── Scroll Area ──────────────────────────────────────────────────────────── */
QScrollArea {{
    background-color: transparent;
    border: none;
}}

/* ── Scroll Bar ───────────────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background-color: {c['void_black']};
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {c['steel']};
    min-height: 24px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {c['teal_dim']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    background-color: {c['void_black']};
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background-color: {c['steel']};
    min-width: 24px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {c['teal_dim']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* ── Check Box ────────────────────────────────────────────────────────────── */
QCheckBox {{
    color: {c['white']};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {c['steel']};
    border-radius: 3px;
    background-color: {c['deep_space']};
}}
QCheckBox::indicator:hover {{
    border-color: {c['teal_dim']};
}}
QCheckBox::indicator:checked {{
    background-color: {c['teal']};
    border-color: {c['teal']};
}}
QCheckBox::indicator:checked:hover {{
    background-color: {c['teal_dim']};
}}

/* ── Spin Box ─────────────────────────────────────────────────────────────── */
QSpinBox {{
    background-color: {c['deep_space']};
    border: 1px solid {c['steel']};
    border-radius: 4px;
    color: {c['white']};
    padding: 4px 8px;
}}
QSpinBox:focus {{
    border-color: {c['teal']};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {c['carbon']};
    border: none;
    width: 16px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background-color: {c['steel']};
}}

/* ── Group Box ────────────────────────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {c['steel']};
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 12px;
    font-family: '{header}';
    font-size: 14px;
    font-weight: 600;
    color: {c['white']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    color: {c['teal']};
}}

/* ── Tab Widget ───────────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {c['steel']};
    border-radius: 4px;
    background-color: {c['deep_space']};
}}
QTabBar::tab {{
    background-color: {c['carbon']};
    border: 1px solid {c['steel']};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    color: {c['grey']};
    padding: 8px 16px;
    font-family: '{header}';
    font-weight: 600;
}}
QTabBar::tab:selected {{
    background-color: {c['deep_space']};
    color: {c['teal']};
}}
QTabBar::tab:hover:!selected {{
    color: {c['white']};
}}

/* ── Tool Tip ─────────────────────────────────────────────────────────────── */
QToolTip {{
    background-color: {c['carbon']};
    border: 1px solid {c['steel']};
    color: {c['white']};
    padding: 6px 8px;
    border-radius: 4px;
}}

/* ── Menu ─────────────────────────────────────────────────────────────────── */
QMenu {{
    background-color: {c['deep_space']};
    border: 1px solid {c['steel']};
    border-radius: 4px;
    padding: 4px;
}}
QMenu::item {{
    color: {c['white']};
    padding: 6px 24px 6px 12px;
    border-radius: 3px;
}}
QMenu::item:selected {{
    background-color: {c['teal_dim']};
    color: {c['void_black']};
}}
QMenu::separator {{
    height: 1px;
    background-color: {c['steel']};
    margin: 4px 8px;
}}

/* ── Progress Bar ─────────────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {c['deep_space']};
    border: 1px solid {c['steel']};
    border-radius: 4px;
    color: {c['white']};
    text-align: center;
    font-size: 11px;
}}
QProgressBar::chunk {{
    background-color: {c['teal']};
    border-radius: 3px;
}}
""".strip()
