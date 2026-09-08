"""Small offline guide reader with one reviewed navigation/package catalog."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QPalette, QTextCursor
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from src.i18n import translate_ui_phrase
from src.constants import SEMANTIC_COLORS as S


def authoring_bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


class ModAuthoringGuide(QDialog):
    """A modeless, read-only guide; local links stay in the reviewed catalog."""

    def __init__(self, parent: QWidget | None = None, *, bundle_root: Path | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modAuthoringGuide")
        self.setProperty("deepSignal", True)
        self.setWindowTitle(translate_ui_phrase("Mod authoring guide"))
        self.resize(1040, 760)
        self.setMinimumSize(720, 480)
        self._root = (bundle_root or authoring_bundle_root()).resolve(strict=True)
        catalog = json.loads((self._root / "docs/mod-authoring/navigation.json").read_text(encoding="utf-8"))
        self._pages = tuple((item["title"], self._root / item["path"]) for item in catalog["pages"])
        self._allowed = {path.resolve() for _title, path in self._pages}
        self._allowed.update((self._root / path).resolve() for path in catalog["examples"])
        if any(not path.is_relative_to(self._root) or not path.is_file() for path in self._allowed):
            raise ValueError("The bundled authoring guide is incomplete or has an invalid path.")
        self._history: list[Path] = []
        self._current: Path | None = None
        layout = QVBoxLayout(self)
        heading = QLabel(translate_ui_phrase("Mod authoring guide"))
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        toolbar = QHBoxLayout()
        self.back_button = QPushButton(translate_ui_phrase("Back"))
        self.back_button.clicked.connect(self._back)
        toolbar.addWidget(self.back_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText(translate_ui_phrase("Find in this page"))
        self.search.setAccessibleName(translate_ui_phrase("Find in this page"))
        self.search.returnPressed.connect(self._find)
        toolbar.addWidget(self.search, 1)
        find_button = QPushButton(translate_ui_phrase("Find next"))
        find_button.clicked.connect(self._find)
        toolbar.addWidget(find_button)
        layout.addLayout(toolbar)
        body = QHBoxLayout()
        self.navigation = QListWidget()
        self.navigation.setMinimumWidth(190)
        self.navigation.setMaximumWidth(245)
        self.navigation.addItems([title for title, _path in self._pages])
        self.navigation.currentRowChanged.connect(self._select_page)
        body.addWidget(self.navigation)
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._follow_link)
        self.browser.setStyleSheet(f"QTextBrowser {{ background: {S['surface']}; color: {S['text_primary']}; padding: 10px; border: 1px solid {S['border']}; }}")
        palette = self.browser.palette()
        palette.setColor(QPalette.ColorRole.Link, QColor(S["accent"]))
        palette.setColor(QPalette.ColorRole.LinkVisited, QColor(S["accent"]))
        self.browser.setPalette(palette)
        self.browser.document().setDefaultFont(QFont("Segoe UI", 10))
        self.browser.document().setDefaultStyleSheet(
            "body { font-family: 'Segoe UI'; font-size: 10pt; } "
            "h1 { font-size: 19pt; } h2 { font-size: 14pt; margin-top: 18px; } "
            "a { color: #54d7ef; } pre { background-color: #111f2b; padding: 10px; } "
            "code { font-family: Consolas; } td, th { padding: 6px; }"
        )
        body.addWidget(self.browser, 1)
        layout.addLayout(body, 1)
        self.location = QLabel()
        self.location.setProperty("class", "muted")
        layout.addWidget(self.location)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self._display(self._pages[0][1], remember=False)

    def _select_page(self, index: int) -> None:
        if 0 <= index < len(self._pages):
            self._display(self._pages[index][1])

    def _display(self, path: Path, *, remember: bool = True) -> None:
        path = path.resolve()
        if path not in self._allowed:
            return
        if remember and self._current is not None and self._current != path:
            self._history.append(self._current)
        self._current = path
        text = path.read_text(encoding="utf-8")
        if path.suffix.casefold() == ".md":
            self.browser.setMarkdown(text)
            # QTextDocument's Markdown importer supplies explicit anchor
            # colors; its default CSS alone does not override those runs.
            block = self.browser.document().begin()
            while block.isValid():
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    if fragment.isValid() and fragment.charFormat().isAnchor():
                        cursor = QTextCursor(self.browser.document())
                        cursor.setPosition(fragment.position())
                        cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                        style = fragment.charFormat()
                        style.setForeground(QColor(S["accent"]))
                        cursor.setCharFormat(style)
                    iterator += 1
                block = block.next()
        else:
            self.browser.setPlainText(text)
        self.browser.document().setBaseUrl(QUrl.fromLocalFile(str(path.parent) + "/"))
        self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        self.location.setText(path.relative_to(self._root).as_posix())
        self.navigation.blockSignals(True)
        self.navigation.setCurrentRow(next((index for index, (_title, value) in enumerate(self._pages) if value.resolve() == path), -1))
        self.navigation.blockSignals(False)
        self.back_button.setEnabled(bool(self._history))

    def _follow_link(self, url: QUrl) -> None:
        if url.scheme() in {"https", "http"}:
            QDesktopServices.openUrl(url)
            return
        if url.scheme() not in {"", "file"} or self._current is None:
            return
        path = Path(url.toLocalFile()) if url.isLocalFile() else self._current.parent / url.path()
        if not url.path():
            path = self._current
        self._display(path)
        if path.resolve() in self._allowed and url.fragment():
            self.browser.scrollToAnchor(url.fragment())

    def _back(self) -> None:
        if self._history:
            self._display(self._history.pop(), remember=False)

    def _find(self) -> None:
        text = self.search.text()
        if text and not self.browser.find(text):
            self.browser.moveCursor(QTextCursor.MoveOperation.Start)
            self.browser.find(text)
