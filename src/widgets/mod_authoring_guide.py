"""Offline, illustrated walkthrough with explicit prose-only translations."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import zipfile

from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QImage, QPalette, QPixmap, QTextCursor, QTextDocument, QTextFormat
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QListWidget, QPushButton, QScrollArea, QTextBrowser, QVBoxLayout, QWidget,
)

from src.core.mod_guide import catalog_paths, render_page
from src.i18n import LANGUAGES, current_language
from src.constants import SEMANTIC_COLORS as S


def authoring_bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


class GuideBrowser(QTextBrowser):
    """Only reviewed local images may load; never fetch remote guide resources."""

    resized = pyqtSignal()

    def __init__(self, assets: set[Path], parent: QWidget) -> None:
        super().__init__(parent)
        self.assets = assets
        self.originals: dict[Path, QImage] = {}

    def original(self, path: Path) -> QImage:
        if path not in self.originals:
            # Keep startup independent of the total illustrated guide size.
            if len(self.originals) >= 12:
                self.originals.pop(next(iter(self.originals)))
            self.originals[path] = QImage(str(path))
        return self.originals[path]

    def loadResource(self, resource_type: int, name: QUrl):
        resolved = self.document().baseUrl().resolved(name)
        if resource_type == QTextDocument.ResourceType.ImageResource and resolved.isLocalFile():
            path = Path(resolved.toLocalFile()).resolve()
            if path in self.assets:
                return self.original(path)
        return None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class ModAuthoringGuide(QDialog):
    """A modeless guide. Changing its language never changes launcher settings."""

    def __init__(self, parent: QWidget | None = None, *, bundle_root: Path | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modAuthoringGuide")
        self.setStyleSheet(f"QDialog#modAuthoringGuide {{ background: {S['surface']}; }}")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1140, 800)
        self.setMinimumSize(1140, 800)
        self._root = (bundle_root or authoring_bundle_root()).resolve(strict=True)
        catalog = json.loads((self._root / "docs/how-to-make-a-mod/navigation.json").read_text(encoding="utf-8"))
        bundled = {(self._root / value).resolve() for value in catalog_paths(catalog)}
        if any(not path.is_relative_to(self._root) or not path.is_file() for path in bundled):
            raise ValueError("The bundled guide is incomplete or has an invalid path.")
        self._source = json.loads((self._root / "docs/how-to-make-a-mod/guide-content.json").read_text(encoding="utf-8"))
        self._content = {(self._root / page["path"]).resolve(): page for page in self._source["pages"]}
        self._contents = catalog["pages"]
        self._pages = tuple((row["title"], self._root / row["path"]) for row in catalog["pages"])
        self._allowed = {(self._root / row["path"]).resolve() for row in catalog["pages"]}
        self._example_files = tuple(catalog['examples'])
        self._handoff_files = bundled - self._allowed
        self._assets = {(self._root / path).resolve() for path in catalog["assets"]}
        self._language = current_language()
        self._history: list[Path] = []
        self._current: Path | None = None
        self._zoom = 0
        self._fitting = False
        self._image_viewer: QDialog | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        self.heading = QLabel()
        self.heading.setObjectName("sectionTitle")
        self.heading.setProperty("class", "sectionTitle")
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        toolbar = QHBoxLayout()
        self.back_button = QPushButton()
        self.back_button.clicked.connect(self._back)
        toolbar.addWidget(self.back_button)
        self.search = QLineEdit()
        self.search.returnPressed.connect(self._find)
        toolbar.addWidget(self.search, 1)
        self.find_button = QPushButton()
        self.find_button.clicked.connect(self._find)
        toolbar.addWidget(self.find_button)
        self.zoom_out = QPushButton("A−")
        self.zoom_out.setProperty("class", "compactGhost")
        self.zoom_out.setFixedWidth(42)
        self.zoom_out.clicked.connect(lambda: self._change_zoom(-1))
        toolbar.addWidget(self.zoom_out)
        self.zoom_in = QPushButton("A+")
        self.zoom_in.setProperty("class", "compactGhost")
        self.zoom_in.setFixedWidth(42)
        self.zoom_in.clicked.connect(lambda: self._change_zoom(1))
        toolbar.addWidget(self.zoom_in)
        self.language_label = QLabel()
        toolbar.addWidget(self.language_label)
        self.language = QComboBox()
        for option in LANGUAGES:
            self.language.addItem(option.native_name, option.code)
        self.language.setCurrentIndex(self.language.findData(self._language))
        self.language.currentIndexChanged.connect(self._change_language)
        self.language_label.setBuddy(self.language)
        toolbar.addWidget(self.language)
        layout.addLayout(toolbar)
        options = QHBoxLayout()
        options.addStretch()
        self.copy_ai = QPushButton()
        self.copy_ai.setProperty('class', 'signalPrimary')
        self.copy_ai.setMinimumHeight(38)
        self.copy_ai.clicked.connect(self._copy_for_ai)
        options.addWidget(self.copy_ai)
        self.export_examples = QPushButton()
        self.export_examples.clicked.connect(self._export_examples)
        options.addWidget(self.export_examples)
        layout.addLayout(options)
        body = QHBoxLayout()
        sidebar = QVBoxLayout()
        self.navigation = QListWidget()
        self.navigation.setMinimumWidth(235)
        self.navigation.setMaximumWidth(300)
        self.navigation.setWordWrap(True)
        self.navigation.setSpacing(4)
        self.navigation.currentRowChanged.connect(self._select_page)
        sidebar.addWidget(self.navigation)
        body.addLayout(sidebar)
        content = QVBoxLayout()
        self.reference_note = QLabel()
        self.reference_note.setWordWrap(True)
        self.reference_note.setProperty("class", "muted")
        content.addWidget(self.reference_note)
        self.browser = GuideBrowser(self._assets, self)
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._follow_link)
        self.browser.resized.connect(self._fit_images)
        self.browser.setStyleSheet(f"QTextBrowser {{ background: {S['surface']}; color: {S['text_primary']}; padding: 18px; border: 1px solid {S['border']}; }}")
        palette = self.browser.palette()
        palette.setColor(QPalette.ColorRole.Link, QColor(S["accent"]))
        palette.setColor(QPalette.ColorRole.LinkVisited, QColor(S["accent"]))
        self.browser.setPalette(palette)
        self.browser.document().setDefaultFont(QFont("Segoe UI", 11))
        self.browser.document().setDefaultStyleSheet(
            "h1 { font-size: 21pt; } h2 { font-size: 15pt; margin-top: 20px; } "
            "a { color: #54d7ef; } pre { background-color: #111f2b; padding: 10px; } "
            "code { font-family: Consolas; } td, th { padding: 7px; }"
        )
        content.addWidget(self.browser, 1)
        body.addLayout(content, 1)
        layout.addLayout(body, 1)
        self.location = QLabel()
        self.location.setProperty("class", "muted")
        layout.addWidget(self.location)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.close)
        layout.addWidget(self.buttons)
        self._translate_chrome()
        self._display(self._pages[0][1], remember=False)

    def _text(self, key: str) -> str:
        return self._source["ui"][key][self._language]

    def _translate_chrome(self) -> None:
        self.setWindowTitle(self._text("title"))
        self.heading.setText(self._text("title"))
        self.back_button.setText(self._text("back"))
        self.search.setPlaceholderText(self._text("find"))
        self.search.setAccessibleName(self._text("find"))
        self.find_button.setText(self._text("next"))
        self.language_label.setText(self._text("language"))
        self.language.setAccessibleName(self._text("language"))
        self.navigation.setAccessibleName(self._text("walkthrough"))
        self.export_examples.setText(self._text('exportExamples'))
        self.copy_ai.setText(self._text('copyAI'))
        self.reference_note.setText(self._text("referenceNote"))
        for button, key in ((self.zoom_in, "zoomIn"), (self.zoom_out, "zoomOut")):
            button.setToolTip(self._text(key))
            button.setAccessibleName(self._text(key))
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText(self._text("close"))
        self._populate_navigation()

    def _populate_navigation(self) -> None:
        self.navigation.blockSignals(True)
        self.navigation.clear()
        for row in self._contents:
            title = row.get("titles", {}).get(self._language, row["title"])
            if row["path"] == "docs/how-to-make-a-mod/00-start-here.md":
                title = self._text("home")
            self.navigation.addItem(title)
        self.navigation.blockSignals(False)

    def _select_page(self, index: int) -> None:
        rows = self._contents
        if 0 <= index < len(rows):
            self._display(self._root / rows[index]["path"])

    def _change_language(self, _index: int) -> None:
        self._language = self.language.currentData()
        self._translate_chrome()
        if self._current:
            self._display(self._current, remember=False)

    def _display(self, path: Path, *, remember: bool = True) -> None:
        path = path.resolve()
        if path not in self._allowed:
            return
        if remember and self._current is not None and self._current != path:
            self._history.append(self._current)
        self._current = path
        page = self._content.get(path)
        self.copy_ai.setText(self._text('copyAI'))
        self.export_examples.setVisible(page['id'] == 'example-files')
        self.reference_note.setVisible(page is None)
        text = render_page(self._source, page, self._language)
        # Resolve local images on the first render, not only after revisiting.
        self.browser.clear()
        self.browser.document().setBaseUrl(QUrl.fromLocalFile(str(path.parent) + "/"))
        if path.suffix.casefold() == ".md":
            self.browser.setMarkdown(text)
            self._style_links()
        else:
            self.browser.setPlainText(text)
        self._apply_text_zoom()
        self._fit_images()
        self.browser.moveCursor(QTextCursor.MoveOperation.Start)
        self.location.setText(path.relative_to(self._root).as_posix())
        row_index = next((i for i, row in enumerate(self._contents) if (self._root / row["path"]).resolve() == path), -1)
        self.navigation.blockSignals(True)
        self.navigation.setCurrentRow(row_index)
        self.navigation.blockSignals(False)
        self.back_button.setEnabled(bool(self._history))

    def _write_examples(self, destination: Path) -> None:
        with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in self._example_files:
                archive.write(self._root / relative, relative)

    def _handoff_text(self) -> str:
        page = self._content[self._current]
        text = self._text('aiInstruction') + '\n\n'
        text += render_page(self._source, page, self._language)
        for relative in page.get('handoffFiles', []):
            path = (self._root / relative).resolve()
            if path in self._handoff_files and path.suffix.lower() in {'.md', '.json', '.js', '.ps1', '.py', '.disabled'}:
                text += '\n\n---\n' + relative + '\n\n' + path.read_text(encoding='utf-8')
        return text

    def _copy_for_ai(self) -> None:
        QApplication.clipboard().setText(self._handoff_text())
        self.copy_ai.setText(self._text('copiedAI'))

    def _export_examples(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, self._text('exportExamples'), 'EveJS-mod-examples.zip', 'ZIP (*.zip)')
        if filename:
            try:
                self._write_examples(Path(filename))
            except (OSError, zipfile.BadZipFile) as exc:
                QMessageBox.warning(self, self._text('exportExamples'), str(exc))


    def _fragments(self):
        block = self.browser.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    yield fragment
                iterator += 1
            block = block.next()

    def _style_links(self) -> None:
        for fragment in self._fragments():
            if fragment.charFormat().isAnchor():
                cursor = QTextCursor(self.browser.document())
                cursor.setPosition(fragment.position())
                cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                style = fragment.charFormat()
                style.setForeground(QColor(S["accent"]))
                cursor.setCharFormat(style)

    def _fit_images(self) -> None:
        if self._fitting:
            return
        self._fitting = True
        try:
            for fragment in self._fragments():
                if not fragment.charFormat().isImageFormat():
                    continue
                style = fragment.charFormat().toImageFormat()
                url = self.browser.document().baseUrl().resolved(QUrl(style.name()))
                path = Path(url.toLocalFile()).resolve() if url.isLocalFile() else None
                if path not in self._assets:
                    continue
                image = self.browser.original(path)
                if not isinstance(image, QImage) or image.isNull():
                    continue
                natural_width = int(image.text("logicalWidth") or image.width())
                width = min(natural_width, max(120, self.browser.viewport().width() - 48))
                # QTextDocument's default image reduction aliases small text.
                # Resample from the full-resolution original for this display.
                ratio = self.browser.devicePixelRatioF()
                preview = image.scaledToWidth(round(width * ratio), Qt.TransformationMode.SmoothTransformation)
                preview.setDevicePixelRatio(ratio)
                self.browser.document().addResource(QTextDocument.ResourceType.ImageResource, url, preview)
                style.setWidth(width)
                style.setHeight(image.height() * width / image.width())
                style.setAnchor(True)
                style.setAnchorHref(url.toString())
                style.setToolTip(self._text("openImage"))
                cursor = QTextCursor(self.browser.document())
                cursor.setPosition(fragment.position())
                cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                cursor.setCharFormat(style)
        finally:
            self._fitting = False

    def _follow_link(self, url: QUrl) -> None:
        if url.scheme() in {"https", "http"}:
            QDesktopServices.openUrl(url)
            return
        if url.scheme() not in {"", "file"} or self._current is None:
            return
        path = Path(url.toLocalFile()) if url.isLocalFile() else self._current.parent / url.path()
        if not url.path():
            path = self._current
        if path.resolve() in self._assets:
            self._open_image(path.resolve())
            return
        self._display(path)
        if path.resolve() in self._allowed and url.fragment():
            self.browser.scrollToAnchor(url.fragment())

    def _open_image(self, path: Path) -> None:
        if self._image_viewer is not None:
            self._image_viewer.close()
        viewer = QDialog(self)
        viewer.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        viewer.setWindowTitle(self._text("openImage"))
        viewer.resize(1100, 720)
        layout = QVBoxLayout(viewer)
        toolbar = QHBoxLayout()
        image = self.browser.original(path)
        label = QLabel()
        scroll = QScrollArea()
        scroll.setWidget(label)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scale = min(1.0, 1040 / image.width())
        percentage = QLabel()

        def display(value: float) -> None:
            nonlocal scale
            scale = max(0.25, min(2.0, value))
            pixmap = QPixmap.fromImage(image.scaledToWidth(round(image.width() * scale), Qt.TransformationMode.SmoothTransformation))
            pixmap.setDevicePixelRatio(1)
            label.setPixmap(pixmap)
            label.resize(pixmap.size())
            percentage.setText(f"{round(scale * 100)}%")

        for text, key, action in (("A−", "zoomOut", lambda: display(scale / 1.25)),
                                  ("A+", "zoomIn", lambda: display(scale * 1.25)),
                                  ("100%", "openImage", lambda: display(1))):
            button = QPushButton(text)
            button.setToolTip(self._text(key))
            button.clicked.connect(action)
            toolbar.addWidget(button)
        toolbar.addWidget(percentage)
        toolbar.addStretch()
        close = QPushButton(self._text("close"))
        close.clicked.connect(viewer.close)
        toolbar.addWidget(close)
        layout.addLayout(toolbar)
        layout.addWidget(scroll)
        display(scale)
        self._image_viewer = viewer
        viewer.destroyed.connect(lambda: setattr(self, "_image_viewer", None)
                                 if self._image_viewer is viewer else None)
        viewer.show()

    def _change_zoom(self, amount: int) -> None:
        value = max(-1, min(6, self._zoom + amount))
        self._zoom = value
        self._apply_text_zoom()
        self._fit_images()

    def _apply_text_zoom(self) -> None:
        # The application's pixel-based QSS overrides QTextEdit.zoomIn().
        # Explicit document sizes also scale headings, code and table cells.
        original_size = int(QTextFormat.Property.UserProperty) + 1
        for fragment in self._fragments():
            style = fragment.charFormat()
            if style.isImageFormat():
                continue
            if style.hasProperty(original_size):
                base = float(style.property(original_size))
            else:
                adjustment = int(style.property(QTextFormat.Property.FontSizeAdjustment) or 0)
                base = style.fontPointSize() or {1: 13, 2: 16, 3: 21}.get(adjustment, 11)
                style.setProperty(original_size, base)
            style.setFontPointSize(base * (1.1 ** self._zoom))
            cursor = QTextCursor(self.browser.document())
            cursor.setPosition(fragment.position())
            cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(style)
        self.zoom_out.setEnabled(self._zoom > -1)
        self.zoom_in.setEnabled(self._zoom < 6)

    def _back(self) -> None:
        if self._history:
            self._display(self._history.pop(), remember=False)

    def _find(self) -> None:
        text = self.search.text()
        if text and not self.browser.find(text):
            self.browser.moveCursor(QTextCursor.MoveOperation.Start)
            self.browser.find(text)
