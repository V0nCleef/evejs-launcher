"""Localized Qt dialogs that preserve user and diagnostic values verbatim."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from PyQt6.QtWidgets import (
    QFileDialog as QtFileDialog,
    QInputDialog as QtInputDialog,
    QMessageBox as QtMessageBox,
)

from src.i18n import translate_ui_phrase


_STANDARD_BUTTON_TEXT = {
    QtMessageBox.StandardButton.Ok: "OK",
    QtMessageBox.StandardButton.Save: "Save",
    QtMessageBox.StandardButton.SaveAll: "Save All",
    QtMessageBox.StandardButton.Open: "Open",
    QtMessageBox.StandardButton.Yes: "Yes",
    QtMessageBox.StandardButton.YesToAll: "Yes to All",
    QtMessageBox.StandardButton.No: "No",
    QtMessageBox.StandardButton.NoToAll: "No to All",
    QtMessageBox.StandardButton.Abort: "Abort",
    QtMessageBox.StandardButton.Retry: "Retry",
    QtMessageBox.StandardButton.Ignore: "Ignore",
    QtMessageBox.StandardButton.Close: "Close",
    QtMessageBox.StandardButton.Cancel: "Cancel",
    QtMessageBox.StandardButton.Discard: "Discard",
    QtMessageBox.StandardButton.Help: "Help",
    QtMessageBox.StandardButton.Apply: "Apply",
    QtMessageBox.StandardButton.Reset: "Reset",
    QtMessageBox.StandardButton.RestoreDefaults: "Restore Defaults",
}


def _qt_plain_button_caption(caption: str) -> str:
    """Remove Qt mnemonic markers while retaining escaped literal ampersands."""
    plain: list[str] = []
    index = 0
    while index < len(caption):
        character = caption[index]
        if character != "&":
            plain.append(character)
            index += 1
            continue
        if index + 1 < len(caption) and caption[index + 1] == "&":
            plain.append("&")
            index += 2
            continue
        index += 1
    return "".join(plain)


def _translate_custom_button_caption(caption: str) -> str:
    """Translate a reviewed Qt caption without mangling unknown custom text."""
    translated = translate_ui_phrase(caption)
    if translated != caption:
        return translated

    plain_source = _qt_plain_button_caption(caption)
    translated_plain = translate_ui_phrase(plain_source)
    if translated_plain == plain_source:
        return caption
    # A catalog ampersand is literal copy. Escape it before handing the label
    # back to Qt, where a single ampersand would otherwise become a mnemonic.
    return translated_plain.replace("&", "&&")


class LocalizedMessageBox(QtMessageBox):
    """Drop-in QMessageBox whose launcher-owned framing follows the app locale."""

    def _localize_contents(self) -> None:
        # Body/detail text may contain raw backend diagnostics. Only reverse-
        # match a formatted body when it has substantial launcher-owned
        # literal framing; a generic phrase such as "x of y" is not enough.
        self.setWindowTitle(
            translate_ui_phrase(
                self.windowTitle(),
                allow_templates=True,
                template_min_literal=3,
            )
        )
        self.setText(
            translate_ui_phrase(
                self.text(),
                allow_templates=True,
                template_min_literal=20,
            )
        )
        if self.informativeText():
            self.setInformativeText(
                translate_ui_phrase(
                    self.informativeText(),
                    allow_templates=True,
                    template_min_literal=20,
                )
            )
        if self.detailedText():
            # Reviewed detail text may be translated. Raw backend diagnostics
            # are absent from the corpus and therefore pass through unchanged.
            self.setDetailedText(
                translate_ui_phrase(
                    self.detailedText(),
                    allow_templates=True,
                    template_min_literal=20,
                )
            )

        standard_buttons = self.standardButtons()
        for standard_button, source in _STANDARD_BUTTON_TEXT.items():
            if standard_buttons & standard_button:
                button = self.button(standard_button)
                if button is not None:
                    button.setText(translate_ui_phrase(source))

        for button in self.buttons():
            source = button.text()
            translated = _translate_custom_button_caption(source)
            if translated != source:
                button.setText(translated)

    def exec(self) -> int:  # noqa: A003 - Qt API compatibility
        self._localize_contents()
        return super().exec()

    @classmethod
    def _show(
        cls,
        icon: QtMessageBox.Icon,
        parent: Any,
        title: str,
        text: str,
        buttons: QtMessageBox.StandardButton,
        default_button: QtMessageBox.StandardButton,
    ) -> QtMessageBox.StandardButton:
        box = cls(parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        if default_button != QtMessageBox.StandardButton.NoButton:
            box.setDefaultButton(default_button)
        return QtMessageBox.StandardButton(box.exec())

    @classmethod
    def information(
        cls,
        parent: Any,
        title: str,
        text: str,
        buttons: QtMessageBox.StandardButton = QtMessageBox.StandardButton.Ok,
        default_button: QtMessageBox.StandardButton = QtMessageBox.StandardButton.NoButton,
    ) -> QtMessageBox.StandardButton:
        return cls._show(
            QtMessageBox.Icon.Information,
            parent,
            title,
            text,
            buttons,
            default_button,
        )

    @classmethod
    def warning(
        cls,
        parent: Any,
        title: str,
        text: str,
        buttons: QtMessageBox.StandardButton = QtMessageBox.StandardButton.Ok,
        default_button: QtMessageBox.StandardButton = QtMessageBox.StandardButton.NoButton,
    ) -> QtMessageBox.StandardButton:
        return cls._show(
            QtMessageBox.Icon.Warning,
            parent,
            title,
            text,
            buttons,
            default_button,
        )

    @classmethod
    def critical(
        cls,
        parent: Any,
        title: str,
        text: str,
        buttons: QtMessageBox.StandardButton = QtMessageBox.StandardButton.Ok,
        default_button: QtMessageBox.StandardButton = QtMessageBox.StandardButton.NoButton,
    ) -> QtMessageBox.StandardButton:
        return cls._show(
            QtMessageBox.Icon.Critical,
            parent,
            title,
            text,
            buttons,
            default_button,
        )

    @classmethod
    def question(
        cls,
        parent: Any,
        title: str,
        text: str,
        buttons: QtMessageBox.StandardButton = (
            QtMessageBox.StandardButton.Yes | QtMessageBox.StandardButton.No
        ),
        default_button: QtMessageBox.StandardButton = QtMessageBox.StandardButton.NoButton,
    ) -> QtMessageBox.StandardButton:
        return cls._show(
            QtMessageBox.Icon.Question,
            parent,
            title,
            text,
            buttons,
            default_button,
        )


class LocalizedFileDialog(QtFileDialog):
    """Translate native file-dialog captions while leaving paths untouched."""

    @staticmethod
    def getExistingDirectory(
        parent: Any = None,
        caption: str = "",
        directory: str = "",
        options: Any = QtFileDialog.Option.ShowDirsOnly,
    ) -> str:
        return QtFileDialog.getExistingDirectory(
            parent,
            translate_ui_phrase(caption),
            directory,
            options,
        )

    @staticmethod
    def getOpenFileName(
        parent: Any = None,
        caption: str = "",
        directory: str = "",
        filter: str = "",  # noqa: A002 - Qt API compatibility
        initialFilter: str = "",  # noqa: N803 - Qt API compatibility
        options: Any = QtFileDialog.Option(0),
    ) -> tuple[str, str]:
        return QtFileDialog.getOpenFileName(
            parent,
            translate_ui_phrase(caption),
            directory,
            translate_ui_phrase(filter),
            initialFilter,
            options,
        )

    @staticmethod
    def getSaveFileName(
        parent: Any = None,
        caption: str = "",
        directory: str = "",
        filter: str = "",  # noqa: A002 - Qt API compatibility
        initialFilter: str = "",  # noqa: N803 - Qt API compatibility
        options: Any = QtFileDialog.Option(0),
    ) -> tuple[str, str]:
        return QtFileDialog.getSaveFileName(
            parent,
            translate_ui_phrase(caption),
            directory,
            translate_ui_phrase(filter),
            initialFilter,
            options,
        )


class LocalizedInputDialog(QtInputDialog):
    """Translate input-dialog framing but never mutate entered/user values."""

    @staticmethod
    def getText(parent: Any, title: str, label: str, *args: Any, **kwargs: Any):
        return QtInputDialog.getText(
            parent,
            translate_ui_phrase(
                title,
                allow_templates=True,
                template_min_literal=3,
            ),
            translate_ui_phrase(
                label,
                allow_templates=True,
                template_min_literal=8,
            ),
            *args,
            **kwargs,
        )

    @staticmethod
    def getItem(
        parent: Any,
        title: str,
        label: str,
        items: Iterable[str],
        *args: Any,
        **kwargs: Any,
    ):
        return QtInputDialog.getItem(
            parent,
            translate_ui_phrase(
                title,
                allow_templates=True,
                template_min_literal=3,
            ),
            translate_ui_phrase(
                label,
                allow_templates=True,
                template_min_literal=8,
            ),
            list(items),
            *args,
            **kwargs,
        )
