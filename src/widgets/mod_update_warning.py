"""Explicit consent before downloading and installing third-party mod code."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from src.constants import SEMANTIC_COLORS as S
from src.i18n import translate_ui_phrase as tr


class ModUpdateWarningDialog(QDialog):
    def __init__(self, mod, release, parent=None):
        super().__init__(parent)
        self.setObjectName('modUpdateWarning')
        self.setWindowTitle(tr('Third-party mod update'))
        self.setModal(True)
        self.resize(700, 610)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(18)

        def label(text, name='', parent_layout=root):
            widget = QLabel(text)
            widget.setObjectName(name)
            widget.setTextFormat(Qt.TextFormat.PlainText)
            widget.setWordWrap(True)
            parent_layout.addWidget(widget)
            return widget

        label(tr('THIRD-PARTY SOFTWARE'), 'eyebrow')
        label(tr('Review before updating'), 'heading')
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(16)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        details = QFrame()
        details.setObjectName('details')
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(18, 16, 18, 16)
        detail_layout.setSpacing(8)
        self.mod_label = label(mod.name, 'modName', detail_layout)
        label(f"{mod.version}  →  {release.version}", 'versions', detail_layout)
        label(tr('GitHub source'), 'sourceHeading', detail_layout)
        self.source_label = label(release.page_url, 'source', detail_layout)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(details)

        warning = QFrame()
        warning.setObjectName('warning')
        warning_layout = QVBoxLayout(warning)
        warning_layout.setContentsMargins(18, 16, 18, 16)
        warning_layout.setSpacing(12)
        label(tr('Third-party code can be harmful'), 'warningTitle', warning_layout)
        self.warning_labels = [label(tr(text), parent_layout=warning_layout) for text in (
            'This will download and install a third-party mod update from the author’s GitHub repository, not a launcher update.',
            'This launcher does not check mods for malicious code. A new release can contain harmful code, even if an earlier version was safe.',
        )]
        body.addWidget(warning)
        label(tr('Only continue if you trust the author and this release. No update starts until you confirm.'), parent_layout=body)
        body.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_btn = QPushButton(tr('Cancel'))
        self.cancel_btn.clicked.connect(self.reject)
        self.cancel_btn.setDefault(True)
        buttons.addWidget(self.cancel_btn)
        self.confirm_btn = QPushButton(tr('I understand — update mod'))
        self.confirm_btn.setObjectName('confirm')
        self.confirm_btn.setAutoDefault(False)
        self.confirm_btn.clicked.connect(self.accept)
        buttons.addWidget(self.confirm_btn)
        root.addLayout(buttons)
        self.cancel_btn.setFocus()
        self.setStyleSheet(f'''
            QDialog#modUpdateWarning, QScrollArea, QScrollArea > QWidget > QWidget {{
                background: {S['background']}; color: {S['text_primary']}; }}
            QLabel {{ color: {S['text_secondary']}; background: transparent;
                font-family: 'Segoe UI'; font-size: 14px; }}
            QLabel#eyebrow {{ color: {S['warning']}; font-size: 11px; font-weight: 700; }}
            QLabel#heading {{ color: {S['text_primary']}; font-size: 27px; font-weight: 600; }}
            QFrame#details {{ background: {S['surface']}; border: 1px solid {S['border']}; border-radius: 10px; }}
            QLabel#modName {{ color: {S['text_primary']}; font-size: 19px; font-weight: 600; }}
            QLabel#versions, QLabel#source {{ color: {S['accent']}; }}
            QLabel#sourceHeading {{ font-size: 12px; }}
            QFrame#warning {{ background: #25141b; border: 1px solid #85404a; border-radius: 10px; }}
            QLabel#warningTitle {{ color: #ff9398; font-size: 17px; font-weight: 600; }}
            QPushButton {{ background: {S['surface']}; color: {S['text_primary']};
                border: 1px solid {S['border_bright']}; border-radius: 6px;
                padding: 12px 18px; font-family: 'Segoe UI'; font-size: 13px; }}
            QPushButton:hover {{ background: {S['surface_hover']}; }}
            QPushButton:focus {{ border: 2px solid {S['accent']}; }}
            QPushButton#confirm {{ background: #583222; border-color: {S['warning']}; color: #ffe0a6; }}
            QPushButton#confirm:hover {{ background: #75462d; }}
        ''')
        screen = self.screen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(700, available.width() - 40), min(610, available.height() - 60))
