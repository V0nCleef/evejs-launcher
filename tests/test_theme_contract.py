"""Contract tests for reusable dashboard visual tokens and focus styles."""
from __future__ import annotations

from src.constants import CONTROL_HEIGHTS, RADII, SPACING
from src.theme import build_qss


def test_visual_token_scales_are_small_and_semantic() -> None:
    assert SPACING == {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
    assert RADII["control"] == 4
    assert RADII["card"] == 6
    assert CONTROL_HEIGHTS["compact"] == 36
    assert CONTROL_HEIGHTS["action"] == 44


def test_theme_exposes_dashboard_roles_and_keyboard_focus() -> None:
    qss = build_qss({"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"})

    for role in (
        'QLabel[class="metricValue"]',
        'QLabel[class="eyebrow"]',
        'QLabel[class="sectionTitle"]',
        'QLabel[class="serviceState"]',
        'QPushButton[class="secondary"]',
        'QPushButton[class="dangerOutline"]',
        'QPushButton[class="compactGhost"]',
    ):
        assert role in qss
    assert "QPushButton:focus" in qss
    assert "QLineEdit:focus" in qss
    assert "border-color: #00C8E0" in qss


def test_theme_exposes_tool_deck_roles_and_focus_states() -> None:
    qss = build_qss({"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"})

    for role in (
        'QFrame[class="toolCard"]',
        'QFrame[class="toolIconPlate"]',
        'QLabel[class="toolCategoryPill"]',
        'QLabel[class="toolAvailabilityDot"][state="ready"]',
        'QLabel[class="toolAvailabilityDot"][state="missing"]',
        'QLabel[class="toolRiskBadge"][risk="system"]',
        'QLabel[class="toolRiskBadge"][risk="caution"]',
        'QLabel[class="toolRiskBadge"][risk="destructive"]',
        'QPushButton[class="toolPrimary"]',
        'QPushButton[class="toolSecondary"]',
        'QPushButton[class="toolDanger"]',
        'QFrame[class="toolEmptyState"]',
    ):
        assert role in qss
    assert 'QPushButton[class="toolPrimary"]:focus' in qss
    assert 'QPushButton[class="toolDanger"]:focus' in qss
    assert "QComboBox:focus" in qss
