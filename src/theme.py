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
QPushButton:focus {{
    border: 2px solid {c['teal']};
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

QPushButton[class="secondary"] {{
    background-color: {c['carbon']};
    border: 1px solid {c['teal_dim']};
    color: {c['white']};
    font-weight: 600;
}}
QPushButton[class="secondary"]:hover {{
    background-color: {c['steel']};
    border-color: {c['teal']};
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

QPushButton[class="dangerOutline"] {{
    background-color: transparent;
    border: 1px solid {c['red']};
    color: {c['red']};
    font-weight: 600;
}}
QPushButton[class="dangerOutline"]:hover {{
    background-color: {c['red']};
    color: {c['void_black']};
}}
QPushButton[class="dangerOutline"]:disabled {{
    background-color: transparent;
    border-color: {c['steel']};
    color: {c['grey']};
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

QPushButton[class="compactGhost"] {{
    background-color: transparent;
    border: 1px solid {c['steel']};
    border-radius: 4px;
    color: {c['grey']};
    padding: 3px 8px;
    font-size: 11px;
    font-weight: 500;
}}
QPushButton[class="compactGhost"]:hover {{
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
QLabel[class="metricValue"] {{
    color: {c['white']};
    font-family: '{header}';
    font-size: 28px;
    font-weight: 700;
}}
QLabel[class="eyebrow"] {{
    color: {c['grey']};
    font-size: 10px;
    font-weight: 600;
}}
QLabel[class="sectionTitle"] {{
    color: {c['teal']};
    font-family: '{header}';
    font-size: 13px;
    font-weight: 700;
}}
QLabel[class="serviceState"] {{
    color: {c['white']};
    font-size: 13px;
    font-weight: 600;
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

/* ── Tool Deck ────────────────────────────────────────────────────────────── */
QFrame[class="toolCard"] {{
    background-color: {c['carbon']};
    border: 1px solid {c['steel']};
    border-radius: 8px;
}}
QFrame[class="toolCard"]:hover {{
    border-color: {c['teal_dim']};
}}
QFrame[class="toolIconPlate"] {{
    background-color: {c['deep_space']};
    border: 1px solid {c['teal_dim']};
    border-radius: 6px;
}}
QFrame[class="toolIconPlate"][accent="data"],
QFrame[class="toolIconPlate"][accent="market"] {{
    border-color: {c['gold']};
}}
QFrame[class="toolIconPlate"][accent="danger"] {{
    border-color: {c['red']};
}}
QLabel[class="toolIcon"] {{
    color: {c['teal']};
    font-family: '{header}';
    font-size: 13px;
    font-weight: 700;
}}
QLabel[class="toolName"] {{
    color: {c['white']};
    font-family: '{header}';
    font-size: 15px;
    font-weight: 700;
}}
QLabel[class="toolDescription"] {{
    color: {c['grey']};
    font-size: 11px;
}}
QLabel[class="toolCategoryPill"] {{
    background-color: {c['deep_space']};
    border: 1px solid {c['teal_dim']};
    border-radius: 3px;
    color: {c['teal']};
    padding: 2px 6px;
    font-size: 9px;
    font-weight: 700;
}}
QLabel[class="toolSource"] {{
    color: {c['grey']};
    font-family: '{mono}';
    font-size: 10px;
}}
QLabel[class="toolSectionTitle"] {{
    color: {c['teal']};
    font-family: '{header}';
    font-size: 13px;
    font-weight: 700;
}}
QLabel[class="toolSectionCount"],
QLabel[class="toolAvailableCount"] {{
    color: {c['grey']};
    font-size: 11px;
}}
QLabel[class="toolAvailability"],
QLabel[class="toolAvailabilityDot"] {{
    color: {c['grey']};
    font-size: 11px;
}}
QLabel[class="toolAvailabilityDot"][state="ready"],
QLabel[class="toolAvailabilityDot"][state="launched"] {{
    color: {c['green']};
}}
QLabel[class="toolAvailabilityDot"][state="missing"] {{
    color: {c['grey']};
}}
QLabel[class="toolAvailabilityDot"][state="error"] {{
    color: {c['red']};
}}
QLabel[class="toolAvailability"][state="launched"] {{
    color: {c['green']};
    font-weight: 600;
}}
QLabel[class="toolAvailability"][state="error"] {{
    color: {c['red']};
    font-weight: 600;
}}
QLabel[class="toolRiskBadge"] {{
    background-color: {c['deep_space']};
    border: 1px solid {c['steel']};
    border-radius: 3px;
    color: {c['grey']};
    padding: 2px 6px;
    font-size: 9px;
    font-weight: 600;
}}
QLabel[class="toolRiskBadge"][risk="system"] {{
    border-color: {c['gold']};
    color: {c['gold']};
}}
QLabel[class="toolRiskBadge"][risk="caution"] {{
    border-color: {c['gold']};
    color: {c['gold']};
}}
QLabel[class="toolRiskBadge"][risk="destructive"] {{
    border-color: {c['red']};
    color: {c['red']};
}}
QPushButton[class="toolPrimary"] {{
    background-color: {c['teal']};
    border: 1px solid {c['teal']};
    color: {c['void_black']};
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 700;
}}
QPushButton[class="toolPrimary"]:hover {{
    background-color: {c['teal_dim']};
}}
QPushButton[class="toolPrimary"]:focus {{
    border: 2px solid {c['white']};
}}
QPushButton[class="toolSecondary"] {{
    background-color: transparent;
    border: 1px solid {c['teal_dim']};
    color: {c['teal']};
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton[class="toolSecondary"]:hover {{
    background-color: {c['steel']};
    border-color: {c['teal']};
}}
QPushButton[class="toolSecondary"]:focus {{
    border: 2px solid {c['teal']};
}}
QPushButton[class="toolDanger"] {{
    background-color: {c['red']};
    border: 1px solid {c['red']};
    color: {c['void_black']};
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 700;
}}
QPushButton[class="toolDanger"]:hover {{
    background-color: {c['red']};
    color: {c['white']};
}}
QPushButton[class="toolDanger"]:focus {{
    border: 2px solid {c['white']};
}}
QPushButton[class="toolPrimary"]:disabled,
QPushButton[class="toolSecondary"]:disabled,
QPushButton[class="toolDanger"]:disabled {{
    background-color: {c['deep_space']};
    border-color: {c['carbon']};
    color: {c['grey']};
}}
QFrame[class="toolEmptyState"] {{
    background-color: {c['deep_space']};
    border: 1px dashed {c['steel']};
    border-radius: 8px;
}}
QLabel[class="toolEmptyIcon"] {{
    color: {c['teal_dim']};
    font-family: '{header}';
    font-size: 32px;
}}
QLabel[class="toolEmptyTitle"] {{
    color: {c['white']};
    font-family: '{header}';
    font-size: 16px;
    font-weight: 700;
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

/* ── Combo Box ────────────────────────────────────────────────────────────── */
QComboBox {{
    background-color: {c['deep_space']};
    border: 1px solid {c['steel']};
    border-radius: 4px;
    color: {c['white']};
    padding: 6px 28px 6px 10px;
}}
QComboBox:hover {{
    border-color: {c['teal_dim']};
}}
QComboBox:focus {{
    border-color: {c['teal']};
}}
QComboBox:disabled {{
    background-color: {c['carbon']};
    color: {c['grey']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {c['deep_space']};
    border: 1px solid {c['steel']};
    color: {c['white']};
    selection-background-color: {c['teal_dim']};
    selection-color: {c['void_black']};
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
