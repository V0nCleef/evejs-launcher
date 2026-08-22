"""Qt Style Sheet (QSS) theme builder for EveJS Launcher V2."""
from __future__ import annotations

from PyQt6.QtGui import QFontDatabase

from .constants import COLORS, SEMANTIC_COLORS


def load_fonts() -> dict[str, str]:
    """Probe the system font database and return the best available families.

    Returns a dict with keys ``header``, ``body`` and ``mono``.
    """
    available = {f.lower(): f for f in QFontDatabase.families()}

    header = (
        available.get("rajdhani")
        or available.get("bahnschrift")
        or available.get("bahnschrift semilight semicondensed")
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
    s = SEMANTIC_COLORS
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

/* -- Deep Signal foundation ------------------------------------------------ */
QWidget[deepSignal="true"] {{
    background-color: transparent;
}}

QFrame[class="glassPanel"] {{
    background-color: rgba(8, 20, 31, 218);
    border: 1px solid {s['border']};
    border-radius: 12px;
}}
QFrame[class="glassPanel"][variant="quiet"] {{
    background-color: rgba(7, 17, 29, 184);
    border-color: rgba(52, 88, 106, 150);
}}
QFrame[class="glassPanel"][variant="elevated"] {{
    background-color: rgba(13, 29, 43, 232);
    border-color: {s['border_bright']};
}}
QFrame[class="glassPanel"][accent="warning"] {{
    border-color: {s['warning']};
}}
QFrame[class="glassPanel"][accent="danger"] {{
    border-color: {s['danger']};
}}
QFrame[class="glassPanel"][selected="true"] {{
    border-color: {s['accent']};
}}
QFrame[class="glassPanel"][interactive="true"]:hover {{
    background-color: rgba(18, 39, 54, 232);
    border-color: {s['accent_dim']};
}}

/* Deep Signal Audio & Voice settings. */
QFrame[class="audioSettingsPanel"] {{
    background-color: rgba(7, 17, 29, 210);
    border: 1px solid {s['border_bright']};
    border-radius: 9px;
}}
QFrame[class="audioSettingsPanel"][identity="true"] {{
    background-color: rgba(9, 23, 36, 228);
    border-color: {s['accent_dim']};
}}
QFrame[audioDivider="true"] {{
    background-color: {s['border']};
    border: none;
}}
QLabel[class="audioControlTitle"],
QLabel[class="audioEventName"] {{
    color: {s['text_primary']};
    font-family: '{header}';
    font-size: 12px;
    font-weight: 600;
}}
QLabel[class="audioControlHelp"],
QLabel[class="audioEventDescription"],
QLabel[class="audioIdentityTagline"],
QLabel[class="audioIdentityKey"] {{
    color: {s['text_muted']};
    font-size: 10px;
}}
QLabel[class="audioControlValue"] {{
    color: {s['text_primary']};
    font-family: '{mono}';
    font-size: 11px;
}}
QLabel[class="audioIdentityName"] {{
    color: {s['text_primary']};
    font-family: '{header}';
    font-size: 34px;
    font-weight: 500;
    letter-spacing: 3px;
}}
QLabel[class="audioIdentityValue"] {{
    color: {s['text_secondary']};
    font-size: 11px;
}}
QLabel[class="audioAvailability"] {{
    color: {s['success']};
    font-family: '{header}';
    font-size: 10px;
    font-weight: 700;
}}
QLabel[class="audioAvailability"][state="unavailable"] {{
    color: {s['text_muted']};
}}
QLabel[class="audioEventIndicator"] {{
    background-color: rgba(79, 224, 127, 28);
    border: 1px solid {s['success']};
    border-radius: 14px;
    color: {s['success']};
    font-size: 15px;
    font-weight: 700;
}}
QLabel[class="audioEventIndicator"][state="off"] {{
    background-color: rgba(143, 158, 173, 18);
    border-color: {s['border']};
    color: {s['text_muted']};
}}
QSlider[audioControl="true"]::groove:horizontal {{
    background-color: {s['surface_hover']};
    border-radius: 2px;
    height: 4px;
}}
QSlider[audioControl="true"]::sub-page:horizontal {{
    background-color: {s['accent']};
    border-radius: 2px;
}}
QSlider[audioControl="true"]::add-page:horizontal {{
    background-color: {s['surface_hover']};
    border-radius: 2px;
}}
QSlider[audioControl="true"]::handle:horizontal {{
    background-color: {s['text_primary']};
    border: 1px solid {s['accent_dim']};
    border-radius: 7px;
    width: 14px;
    margin: -5px 0;
}}
QSlider[audioControl="true"]::handle:horizontal:hover {{
    background-color: {s['accent']};
    border-color: {s['text_primary']};
}}
QSlider[audioControl="true"]:disabled {{
    opacity: 0.45;
}}

QFrame#deepSignalCommandDeck {{
    background-color: {s['background']};
    border: 1px solid {s['border_bright']};
    border-radius: 10px;
}}
QFrame#deepSignalHeroOverlay {{
    background-color: rgba(2, 7, 12, 178);
    border: none;
    border-radius: 10px;
}}
QFrame[class="signalMetric"],
QFrame[class="signalServices"] {{
    background-color: rgba(8, 20, 31, 224);
    border: 1px solid {s['border']};
    border-radius: 9px;
}}
QFrame[class="signalMetric"]:hover,
QFrame[class="signalServices"]:hover {{
    border-color: {s['accent_dim']};
}}
QFrame[class="serviceRow"] {{
    background-color: rgba(4, 12, 20, 170);
    border: 1px solid rgba(52, 88, 106, 145);
    border-radius: 7px;
}}
QFrame[class="serviceRow"]:hover,
QFrame[class="serviceRow"]:focus {{
    background-color: rgba(18, 39, 54, 205);
    border-color: {s['accent']};
}}

/* Deep Signal Operations instruments.  These selectors are intentionally
   page-scoped through semantic properties so the legacy utility pages retain
   their denser layout while Operations follows the approved cinematic deck. */
QFrame[class="signalInstrument"] {{
    background-color: rgba(5, 14, 23, 196);
    border: 1px solid rgba(75, 104, 122, 176);
    border-radius: 4px;
}}
QFrame[class="signalInstrument"]:hover,
QFrame[class="signalInstrument"]:focus {{
    background-color: rgba(8, 24, 36, 218);
    border-color: rgba(0, 200, 224, 218);
}}
QLabel[class="signalInstrumentName"] {{
    color: {s['text_primary']};
    font-family: '{header}';
    font-size: 14px;
    font-weight: 600;
}}
QLabel[class="signalInstrumentState"] {{
    color: {s['success']};
    font-family: '{header}';
    font-size: 11px;
    font-weight: 600;
}}
QFrame[class="recentActivity"] {{
    background-color: rgba(3, 10, 17, 152);
    border: 1px solid rgba(52, 88, 106, 112);
    border-radius: 3px;
}}
QLabel[class="activityTime"] {{
    color: {s['text_muted']};
    font-family: '{mono}';
    font-size: 10px;
}}
QLabel[class="activityMessage"] {{
    color: {s['text_secondary']};
    font-size: 11px;
}}
QLabel[class="activityState"] {{
    color: {s['success']};
    font-size: 12px;
}}
QLabel[class="activityState"][state="idle"] {{ color: {s['text_muted']}; }}
QLabel[class="activityState"][state="online"] {{ color: {s['success']}; }}
QLabel[class="activityState"][state="warning"] {{ color: {s['warning']}; }}
QLabel[class="activityState"][state="danger"] {{ color: {s['danger']}; }}

QLabel[class="overallSignal"] {{
    color: {s['text_primary']};
    font-family: '{header}';
    font-size: 38px;
    font-weight: 500;
}}
QLabel[class="overallSignal"][state="online"] {{ color: {s['success']}; }}
QLabel[class="overallSignal"][state="starting"],
QLabel[class="overallSignal"][state="degraded"] {{ color: {s['warning']}; }}
QLabel[class="overallSignal"][state="failed"] {{ color: {s['danger']}; }}

QPushButton[class="secondary"][deepRole="launchStack"] {{
    background-color: rgba(105, 72, 0, 224);
    border: 1px solid {s['warning']};
    border-radius: 4px;
    color: #FFE39A;
    font-family: '{header}';
    font-size: 17px;
    font-weight: 600;
}}
QPushButton[class="secondary"][deepRole="launchStack"]:hover {{
    background-color: {s['warning']};
    border-color: #FFE39A;
    color: {s['background']};
}}
QPushButton[deepRole="launchGroup"] {{
    background-color: rgba(4, 21, 32, 208);
    border: 1px solid {s['accent']};
    border-radius: 4px;
    color: {s['text_primary']};
    font-family: '{header}';
    font-size: 17px;
    font-weight: 600;
}}
QPushButton[deepRole="launchGroup"]:hover {{
    background-color: rgba(0, 113, 139, 190);
    border-color: #7EEFFF;
    color: #FFFFFF;
}}
QPushButton[deepRole="launchStack"]:disabled,
QPushButton[deepRole="launchGroup"]:disabled {{
    background-color: rgba(11, 21, 31, 205);
    border-color: {s['border']};
    color: {s['text_muted']};
}}

QLabel[class="pageEyebrow"] {{
    color: {s['accent']};
    font-family: '{header}';
    font-size: 10px;
    font-weight: 700;
}}
QLabel[class="pageTitle"] {{
    color: {s['text_primary']};
    font-family: '{header}';
    font-size: 24px;
    font-weight: 500;
}}
QLabel[class="pageSubtitle"] {{
    color: {s['text_secondary']};
    font-size: 12px;
}}
QLabel[class="panelTitle"] {{
    color: {s['text_primary']};
    font-family: '{header}';
    font-size: 15px;
    font-weight: 700;
}}
QLabel[class="panelMeta"] {{
    color: {s['text_muted']};
    font-size: 11px;
}}
QLabel[class="signalPill"] {{
    background-color: rgba(22, 74, 87, 150);
    border: 1px solid {s['accent_dim']};
    border-radius: 9px;
    color: {s['accent']};
    padding: 2px 8px;
    font-family: '{header}';
    font-size: 10px;
    font-weight: 700;
}}
QLabel[class="signalPill"][state="online"],
QLabel[class="signalPill"][state="running"] {{
    border-color: {s['success']};
    color: {s['success']};
}}
QLabel[class="signalPill"][state="starting"],
QLabel[class="signalPill"][state="launching"],
QLabel[class="signalPill"][state="degraded"] {{
    border-color: {s['warning']};
    color: {s['warning']};
}}
QLabel[class="signalPill"][state="failed"],
QLabel[class="signalPill"][state="error"] {{
    border-color: {s['danger']};
    color: {s['danger']};
}}

QPushButton[class="signalPrimary"] {{
    background-color: {s['accent']};
    border: 1px solid {s['accent']};
    border-radius: 6px;
    color: {s['background']};
    padding: 9px 18px;
    font-family: '{header}';
    font-weight: 700;
}}
QPushButton[class="signalPrimary"]:hover {{
    background-color: {s['text_primary']};
    border-color: {s['text_primary']};
}}
QPushButton[class="signalPrimary"]:focus {{
    border: 2px solid {s['text_primary']};
}}
QPushButton[class="signalSecondary"] {{
    background-color: rgba(7, 17, 29, 170);
    border: 1px solid {s['border_bright']};
    border-radius: 6px;
    color: {s['text_primary']};
    padding: 9px 18px;
    font-family: '{header}';
    font-weight: 600;
}}
QPushButton[class="signalSecondary"]:hover {{
    background-color: rgba(22, 74, 87, 140);
    border-color: {s['accent']};
}}
QPushButton[class="signalSecondary"]:focus {{
    border: 2px solid {s['accent']};
}}
QPushButton[class="signalPrimary"]:disabled,
QPushButton[class="signalSecondary"]:disabled {{
    background-color: {s['surface']};
    border-color: {s['surface_elevated']};
    color: {s['text_muted']};
}}

/* -- Deep Signal Mods ----------------------------------------------------- */
QFrame[class="modsRuntimePanel"],
QFrame[class="modsManifestPanel"],
QFrame[class="modsActionRail"] {{
    background-color: rgba(7, 17, 29, 210);
    border: 1px solid {s['border_bright']};
    border-radius: 9px;
}}
QFrame[class="modsRuntimePanel"] {{
    background-color: rgba(8, 24, 36, 222);
    border-color: {s['accent_dim']};
}}
QLabel[class="modsRuntimeMark"],
QLabel[class="modIconPlate"] {{
    background-color: rgba(0, 153, 184, 24);
    border: 1px solid {s['accent_dim']};
    border-radius: 5px;
    color: {s['accent']};
    font-family: '{header}';
    font-size: 10px;
    font-weight: 700;
}}
QLabel[class="modsRuntimeEyebrow"],
QLabel[class="modsManifestMeta"] {{
    color: {s['text_muted']};
    font-family: '{header}';
    font-size: 9px;
    font-weight: 700;
}}
QLabel[class="modsRuntimeDescription"],
QLabel[class="modsActionDescription"],
QLabel[class="modsEmptyDescription"] {{
    color: {s['text_secondary']};
    font-size: 11px;
}}
QLabel[class="modsManifestTitle"],
QLabel[class="modsActionTitle"],
QLabel[class="modsEmptyTitle"] {{
    color: {s['text_primary']};
    font-family: '{header}';
    font-size: 12px;
    font-weight: 700;
}}
QFrame[modsDivider="true"] {{
    background-color: {s['border']};
    border: none;
}}
QFrame[class="modInstrument"] {{
    background-color: rgba(4, 12, 20, 176);
    border: 1px solid rgba(52, 88, 106, 158);
    border-radius: 7px;
}}
QFrame[class="modInstrument"]:hover {{
    background-color: rgba(10, 28, 41, 212);
    border-color: {s['accent_dim']};
}}
QFrame[class="modInstrument"][state="enabled"] {{
    border-color: rgba(0, 200, 224, 150);
}}
QLabel[class="modName"] {{
    color: {s['text_primary']};
    font-family: '{header}';
    font-size: 14px;
    font-weight: 600;
}}
QLabel[class="modPath"] {{
    color: {s['text_muted']};
    font-family: '{mono}';
    font-size: 9px;
}}
QLabel[class="modState"] {{
    background-color: rgba(143, 158, 173, 14);
    border: 1px solid {s['border']};
    border-radius: 9px;
    color: {s['text_muted']};
    padding: 2px 7px;
    font-family: '{header}';
    font-size: 9px;
    font-weight: 700;
}}
QLabel[class="modState"][state="enabled"] {{
    background-color: rgba(0, 200, 224, 20);
    border-color: {s['accent_dim']};
    color: {s['accent']};
}}
QLabel[class="modState"][state="error"] {{
    background-color: rgba(224, 79, 79, 22);
    border-color: {s['danger']};
    color: {s['danger']};
}}
QPushButton[class="modManagementAction"] {{
    background-color: rgba(7, 17, 29, 176);
    border: 1px solid {s['border_bright']};
    border-radius: 7px;
    color: {s['text_secondary']};
    padding: 0 12px;
    font-family: '{header}';
    font-size: 10px;
    font-weight: 700;
}}
QPushButton[class="modManagementAction"][managementRole="remove"] {{
    background-color: rgba(224, 79, 79, 18);
    border-color: rgba(224, 79, 79, 118);
    color: #F07A7A;
}}
QPushButton[class="modManagementAction"][managementRole="remove"]:hover {{
    background-color: rgba(224, 79, 79, 48);
    border-color: {s['danger']};
    color: {s['text_primary']};
}}
QPushButton[class="modManagementAction"][managementRole="remove"]:pressed {{
    background-color: rgba(224, 79, 79, 82);
    border-color: #F07A7A;
}}
QPushButton[class="modManagementAction"][managementRole="remove"]:focus {{
    border: 2px solid #F07A7A;
}}
QPushButton[class="modManagementAction"][managementRole="repair"] {{
    background-color: rgba(255, 184, 0, 16);
    border-color: rgba(255, 184, 0, 112);
    color: {s['warning']};
}}
QPushButton[class="modManagementAction"][managementRole="repair"]:hover {{
    background-color: rgba(255, 184, 0, 42);
    border-color: {s['warning']};
    color: {s['text_primary']};
}}
QPushButton[class="modManagementAction"][managementRole="repair"]:pressed {{
    background-color: rgba(255, 184, 0, 72);
}}
QPushButton[class="modManagementAction"][managementRole="repair"]:focus {{
    border: 2px solid {s['warning']};
}}
QPushButton[class="modManagementAction"]:disabled {{
    background-color: rgba(7, 17, 29, 116);
    border-color: {s['border']};
    color: {s['text_muted']};
}}
QFrame[class="modsEmptyState"] {{
    background-color: rgba(4, 12, 20, 120);
    border: 1px dashed {s['border']};
    border-radius: 7px;
}}
QPushButton[class="modsApply"] {{
    background-color: {s['warning']};
    border: 1px solid {s['warning']};
    border-radius: 5px;
    color: {s['background']};
    padding: 8px 18px;
    font-family: '{header}';
    font-weight: 700;
}}
QPushButton[class="modsApply"]:hover {{
    background-color: #FFD15A;
    border-color: #FFE39A;
}}
QPushButton[class="modsApply"]:focus {{
    border: 2px solid {s['text_primary']};
}}
QPushButton[class="modsApply"]:disabled {{
    background-color: {s['surface']};
    border-color: {s['surface_elevated']};
    color: {s['text_muted']};
}}

/* ── Tool Deck ────────────────────────────────────────────────────────────── */
QLabel[class="toolRuntimePill"],
QLabel[class="toolAvailableCount"] {{
    background-color: rgba(22, 74, 87, 132);
    border: 1px solid {s['accent_dim']};
    border-radius: 9px;
    color: {s['accent']};
    padding: 2px 8px;
    font-family: '{header}';
    font-size: 9px;
    font-weight: 700;
}}
QLabel[class="toolRuntimePill"][state="online"],
QLabel[class="toolAvailableCount"][state="online"] {{
    border-color: {s['success']};
    color: {s['success']};
}}
QLabel[class="toolRuntimePill"][state="idle"],
QLabel[class="toolAvailableCount"][state="idle"] {{
    border-color: {s['border']};
    color: {s['text_muted']};
}}
QFrame[class="toolFilterRail"] {{
    background-color: rgba(7, 17, 29, 210);
    border: 1px solid {s['border_bright']};
    border-radius: 9px;
}}
QLabel[class="toolFilterLabel"] {{
    color: {s['text_muted']};
    font-family: '{header}';
    font-size: 9px;
    font-weight: 700;
}}
QFrame[class="toolFilterRail"] QLineEdit,
QFrame[class="toolFilterRail"] QComboBox {{
    background-color: rgba(3, 10, 17, 188);
    border: 1px solid {s['border']};
    border-radius: 5px;
    color: {s['text_primary']};
}}
QFrame[class="toolFilterRail"] QLineEdit:focus,
QFrame[class="toolFilterRail"] QComboBox:focus {{
    border-color: {s['accent']};
}}
QFrame[class="toolCard"] {{
    background-color: rgba(7, 17, 29, 210);
    border: 1px solid {s['border']};
    border-radius: 9px;
}}
QFrame[class="toolCard"]:hover {{
    background-color: rgba(10, 28, 41, 222);
    border-color: {s['accent_dim']};
}}
QFrame[class="toolIconPlate"] {{
    background-color: rgba(0, 153, 184, 22);
    border: 1px solid {s['accent_dim']};
    border-radius: 6px;
}}
QFrame[class="toolIconPlate"][accent="data"],
QFrame[class="toolIconPlate"][accent="market"] {{
    border-color: {s['accent_dim']};
}}
QFrame[class="toolIconPlate"][accent="danger"] {{
    border-color: {s['danger']};
}}
QLabel[class="toolIcon"] {{
    color: {s['accent']};
    font-family: '{header}';
    font-size: 13px;
    font-weight: 700;
}}
QLabel[class="toolName"] {{
    color: {s['text_primary']};
    font-family: '{header}';
    font-size: 15px;
    font-weight: 700;
}}
QLabel[class="toolDescription"] {{
    color: {s['text_secondary']};
    font-size: 11px;
}}
QLabel[class="toolCategoryPill"] {{
    background-color: rgba(22, 74, 87, 120);
    border: 1px solid {s['accent_dim']};
    border-radius: 3px;
    color: {s['accent']};
    padding: 2px 6px;
    font-size: 9px;
    font-weight: 700;
}}
QLabel[class="toolSource"] {{
    color: {s['text_muted']};
    font-family: '{mono}';
    font-size: 10px;
}}
QLabel[class="toolSectionTitle"] {{
    color: {s['accent']};
    font-family: '{header}';
    font-size: 13px;
    font-weight: 700;
}}
QLabel[class="toolSectionCount"],
QLabel[class="toolAvailability"],
QLabel[class="toolAvailabilityDot"] {{
    color: {s['text_muted']};
    font-size: 11px;
}}
QLabel[class="toolSectionCount"] {{
    color: {s['text_muted']};
    font-size: 11px;
}}
QLabel[class="toolAvailabilityDot"][state="ready"],
QLabel[class="toolAvailabilityDot"][state="launched"] {{
    color: {s['success']};
}}
QLabel[class="toolAvailabilityDot"][state="missing"] {{
    color: {s['text_muted']};
}}
QLabel[class="toolAvailabilityDot"][state="error"] {{
    color: {s['danger']};
}}
QLabel[class="toolAvailability"][state="launched"] {{
    color: {s['success']};
    font-weight: 600;
}}
QLabel[class="toolAvailability"][state="error"] {{
    color: {s['danger']};
    font-weight: 600;
}}
QLabel[class="toolRiskBadge"] {{
    background-color: rgba(4, 12, 20, 170);
    border: 1px solid {s['border']};
    border-radius: 3px;
    color: {s['text_muted']};
    padding: 2px 6px;
    font-size: 9px;
    font-weight: 600;
}}
QLabel[class="toolRiskBadge"][risk="system"] {{
    border-color: {s['border_bright']};
    color: {s['text_secondary']};
}}
QLabel[class="toolRiskBadge"][risk="caution"] {{
    border-color: {s['border_bright']};
    color: {s['text_secondary']};
}}
QLabel[class="toolRiskBadge"][risk="destructive"] {{
    border-color: {s['danger']};
    color: {s['danger']};
}}
QPushButton[class="toolPrimary"] {{
    background-color: {s['warning']};
    border: 1px solid {s['warning']};
    color: {s['background']};
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 700;
}}
QPushButton[class="toolPrimary"]:hover {{
    background-color: #FFD15A;
    border-color: #FFE39A;
}}
QPushButton[class="toolPrimary"]:focus {{
    border: 2px solid {s['text_primary']};
}}
QPushButton[class="toolSecondary"] {{
    background-color: transparent;
    border: 1px solid {s['accent_dim']};
    color: {s['accent']};
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton[class="toolSecondary"]:hover {{
    background-color: rgba(22, 74, 87, 140);
    border-color: {s['accent']};
}}
QPushButton[class="toolSecondary"]:focus {{
    border: 2px solid {s['accent']};
}}
QPushButton[class="toolDanger"] {{
    background-color: {s['danger']};
    border: 1px solid {s['danger']};
    color: {s['background']};
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 700;
}}
QPushButton[class="toolDanger"]:hover {{
    background-color: {s['danger']};
    color: {s['text_primary']};
}}
QPushButton[class="toolDanger"]:focus {{
    border: 2px solid {s['text_primary']};
}}
QPushButton[class="toolPrimary"]:disabled,
QPushButton[class="toolSecondary"]:disabled,
QPushButton[class="toolDanger"]:disabled {{
    background-color: {s['surface']};
    border-color: {s['surface_elevated']};
    color: {s['text_muted']};
}}
QFrame[class="toolEmptyState"] {{
    background-color: rgba(7, 17, 29, 184);
    border: 1px dashed {s['border_bright']};
    border-radius: 9px;
}}
QLabel[class="toolEmptyIcon"] {{
    color: {s['accent_dim']};
    font-family: '{header}';
    font-size: 32px;
}}
QLabel[class="toolEmptyTitle"] {{
    color: {s['text_primary']};
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
