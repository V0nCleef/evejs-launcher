"""Focused contracts for the non-blocking LYRA caption overlay."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from src.widgets.shipboard_caption import ShipboardCaption


def test_caption_normalizes_text_is_click_through_and_repositions(qapp) -> None:
    parent = QWidget()
    parent.resize(1000, 640)
    parent.show()
    caption = ShipboardCaption(parent)

    caption.show_caption(" Launching\n character,   Aria Vale. ")
    qapp.processEvents()

    assert caption.isVisibleTo(parent)
    assert caption.message_label.text() == "Launching character, Aria Vale."
    assert "LYRA says" in caption.accessibleDescription()
    assert caption.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert caption.geometry().bottom() <= parent.height() - caption.BOTTOM_MARGIN
    assert abs(caption.geometry().center().x() - parent.rect().center().x()) <= 1

    parent.resize(1366, 768)
    caption.reposition()
    assert abs(caption.geometry().center().x() - parent.rect().center().x()) <= 1


def test_empty_caption_does_not_interrupt_current_message(qapp) -> None:
    parent = QWidget()
    caption = ShipboardCaption(parent)
    caption.show_caption("Server stack online.")

    caption.show_caption(" \n ")

    assert caption.message_label.text() == "Server stack online."
    assert caption._timer.isActive()
