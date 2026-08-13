"""Reusable translucent Deep Signal panel."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QFrame, QSizePolicy, QVBoxLayout, QWidget

from src.constants import SPACING


class GlassPanel(QFrame):
    """A styled content panel with optional keyboard activation.

    The panel intentionally uses QSS-friendly dynamic properties instead of a
    widget-local stylesheet.  This allows live theme changes and keeps all
    Deep Signal colour decisions in :mod:`src.theme`.
    """

    activated = pyqtSignal()
    _VARIANTS = {"default", "quiet", "elevated"}

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        variant: str = "default",
        interactive: bool = False,
        padding: int = SPACING["lg"],
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", "glassPanel")
        self.setProperty("deepSignal", True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self._content_layout = QVBoxLayout(self)
        self._content_layout.setSpacing(SPACING["md"])
        self.set_padding(padding)
        self.set_variant(variant)
        self.set_interactive(interactive)

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    @property
    def variant(self) -> str:
        return str(self.property("variant"))

    @property
    def interactive(self) -> bool:
        return bool(self.property("interactive"))

    def add_widget(self, widget: QWidget, stretch: int = 0) -> None:
        self._content_layout.addWidget(widget, stretch)

    def set_padding(self, padding: int) -> None:
        padding = max(0, int(padding))
        self._content_layout.setContentsMargins(
            padding,
            padding,
            padding,
            padding,
        )

    def set_variant(self, variant: str) -> None:
        variant = str(variant).lower().strip()
        if variant not in self._VARIANTS:
            expected = ", ".join(sorted(self._VARIANTS))
            raise ValueError(f"Unknown glass panel variant {variant!r}; expected {expected}")
        self.setProperty("variant", variant)
        self._refresh_style()

    def set_interactive(self, interactive: bool) -> None:
        interactive = bool(interactive)
        self.setProperty("interactive", interactive)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if interactive
            else Qt.CursorShape.ArrowCursor
        )
        self.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus
            if interactive
            else Qt.FocusPolicy.NoFocus
        )
        self._refresh_style()

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", bool(selected))
        self._refresh_style()

    def set_accent(self, accent: str | None) -> None:
        self.setProperty("accent", accent or "")
        self._refresh_style()

    def _refresh_style(self) -> None:
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.interactive and event.key() in {
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Space,
        }:
            self.activated.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self.interactive
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.activated.emit()
        super().mouseReleaseEvent(event)


__all__ = ["GlassPanel"]
