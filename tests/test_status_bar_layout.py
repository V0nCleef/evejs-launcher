"""Geometry regressions for the launcher footer."""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from src.constants import CONTROL_HEIGHTS
from src.core.service_status import ServiceState
from src.theme import build_qss
from src.widgets.status_bar import StatusBar


def test_footer_keeps_a_safe_baseline_and_intentional_text_fit_at_minimum_width(
    qapp: QApplication,
) -> None:
    original_style = qapp.styleSheet()
    qapp.setStyleSheet(
        build_qss({"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"})
    )
    bar = StatusBar()
    bar.resize(1000, CONTROL_HEIGHTS["compact"])
    bar.set_server_state(ServiceState.ONLINE, pid=12345)
    bar.set_market_state(ServiceState.ONLINE, pid=67890)
    bar.set_client_count(12)
    bar.show()
    qapp.processEvents()

    try:
        assert bar.width() == 1000
        assert bar.height() == CONTROL_HEIGHTS["compact"]

        for section in (
            bar.server_section,
            bar.market_section,
            bar.clients_section,
        ):
            label = section.label
            assert label.height() >= label.fontMetrics().height() + 2
            assert label.geometry().right() <= section.contentsRect().right()
            assert label.text()
            assert (
                label.text() == label.toolTip()
                or (label.text().endswith("…") and label.toolTip().startswith(label.text()[:-1]))
            )

        assert bar.version_label.height() >= bar.version_label.fontMetrics().height() + 2
    finally:
        bar.close()
        bar.deleteLater()
        qapp.setStyleSheet(original_style)
