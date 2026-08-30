"""Opt-in translation for reviewed static Qt widget phrases.

Text shown by the launcher is not automatically UI text.  Character names,
mod names, ship names, paths, and server messages can all legitimately match
an English button label.  Widgets therefore have to be registered explicitly
with :func:`mark_translatable` before this module will touch them.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QWidget,
)

from src.i18n import current_language, is_reviewed_ui_phrase, translate_ui_phrase


_SOURCE_TEXT = "evejsI18nSourceText"
_RENDERED_TEXT = "evejsI18nRenderedText"
_SOURCE_PLACEHOLDER = "evejsI18nSourcePlaceholder"
_RENDERED_PLACEHOLDER = "evejsI18nRenderedPlaceholder"
_SOURCE_TOOLTIP = "evejsI18nSourceTooltip"
_RENDERED_TOOLTIP = "evejsI18nRenderedTooltip"
_SOURCE_WINDOW_TITLE = "evejsI18nSourceWindowTitle"
_RENDERED_WINDOW_TITLE = "evejsI18nRenderedWindowTitle"
_SOURCE_ACCESSIBLE_NAME = "evejsI18nSourceAccessibleName"
_RENDERED_ACCESSIBLE_NAME = "evejsI18nRenderedAccessibleName"
_SOURCE_ACCESSIBLE_DESCRIPTION = "evejsI18nSourceAccessibleDescription"
_RENDERED_ACCESSIBLE_DESCRIPTION = "evejsI18nRenderedAccessibleDescription"
_TEXT_ALLOW_TEMPLATES = "evejsI18nTextAllowTemplates"
_TEXT_TEMPLATE_MIN_LITERAL = "evejsI18nTextTemplateMinLiteral"
_PLACEHOLDER_ALLOW_TEMPLATES = "evejsI18nPlaceholderAllowTemplates"
_TOOLTIP_ALLOW_TEMPLATES = "evejsI18nTooltipAllowTemplates"
_WINDOW_TITLE_ALLOW_TEMPLATES = "evejsI18nWindowTitleAllowTemplates"
_ACCESSIBLE_NAME_ALLOW_TEMPLATES = "evejsI18nAccessibleNameAllowTemplates"
_ACCESSIBLE_DESCRIPTION_ALLOW_TEMPLATES = (
    "evejsI18nAccessibleDescriptionAllowTemplates"
)
_TRANSLATABLE = "evejsI18nTranslatable"
_COMBO_SOURCE_ROLE = int(Qt.ItemDataRole.UserRole) + 487


_WidgetT = TypeVar("_WidgetT", bound=QObject)


def _retranslate_property(
    widget: QObject,
    getter: Callable[[], str],
    setter: Callable[[str], None],
    language: str,
    source_property: str,
    rendered_property: str,
    *,
    register_missing: bool = False,
    allow_templates: bool = False,
    template_min_literal: int = 0,
) -> None:
    current = getter()
    source = widget.property(source_property)
    last_rendered = widget.property(rendered_property)
    if not isinstance(source, str):
        if not register_missing:
            return
        source = current
    elif current != last_rendered:
        source = current
    rendered = translate_ui_phrase(
        source,
        language,
        allow_templates=allow_templates,
        template_min_literal=template_min_literal,
    )
    if current != rendered:
        setter(rendered)
    widget.setProperty(source_property, source)
    widget.setProperty(rendered_property, rendered)


def mark_translatable(
    widget: _WidgetT,
    *,
    allow_templates: bool = False,
    template_min_literal: int = 0,
) -> _WidgetT:
    """Register one static-text widget and render the active language now.

    Returning *widget* keeps construction concise::

        title = mark_translatable(QLabel("Settings"))

    The source phrase is captured before the first translation, so switching
    languages remains lossless even when two phrases share one translation.
    """
    widget.setProperty(_TRANSLATABLE, True)
    language = current_language()
    if isinstance(widget, QComboBox):
        # Combo items may be semantic values in existing launcher code.  They
        # require a dedicated data-key migration and are deliberately excluded.
        return widget
    if isinstance(widget, QLineEdit):
        widget.setProperty(_PLACEHOLDER_ALLOW_TEMPLATES, allow_templates)
        _retranslate_property(
            widget,
            widget.placeholderText,
            widget.setPlaceholderText,
            language,
            _SOURCE_PLACEHOLDER,
            _RENDERED_PLACEHOLDER,
            register_missing=True,
            allow_templates=allow_templates,
        )
    elif isinstance(widget, QGroupBox):
        widget.setProperty(_TEXT_ALLOW_TEMPLATES, allow_templates)
        widget.setProperty(_TEXT_TEMPLATE_MIN_LITERAL, template_min_literal)
        _retranslate_property(
            widget,
            widget.title,
            widget.setTitle,
            language,
            _SOURCE_TEXT,
            _RENDERED_TEXT,
            register_missing=True,
            allow_templates=allow_templates,
            template_min_literal=template_min_literal,
        )
    elif isinstance(widget, (QLabel, QAbstractButton)):
        widget.setProperty(_TEXT_ALLOW_TEMPLATES, allow_templates)
        widget.setProperty(_TEXT_TEMPLATE_MIN_LITERAL, template_min_literal)
        _retranslate_property(
            widget,
            widget.text,
            widget.setText,
            language,
            _SOURCE_TEXT,
            _RENDERED_TEXT,
            register_missing=True,
            allow_templates=allow_templates,
            template_min_literal=template_min_literal,
        )
    return widget


def _set_translatable_text(
    widget: QWidget,
    source: str,
    *,
    allow_templates: bool,
    template_min_literal: int = 0,
) -> None:
    """Set and immediately translate one explicitly launcher-owned text value."""
    if isinstance(widget, QGroupBox):
        widget.setTitle(source)
    elif isinstance(widget, (QLabel, QAbstractButton)):
        widget.setText(source)
    else:
        raise TypeError(f"Unsupported translatable text widget: {type(widget)!r}")
    mark_translatable(
        widget,
        allow_templates=allow_templates,
        template_min_literal=template_min_literal,
    )
    retranslate_widget_tree(widget, current_language())


def set_translatable_text(widget: QWidget, source: str) -> None:
    """Set launcher-owned exact text without reverse-matching templates."""
    _set_translatable_text(widget, source, allow_templates=False)


def set_translatable_text_template(
    widget: QWidget,
    source: str,
    *,
    template_min_literal: int = 0,
) -> None:
    """Set trusted launcher framing rendered from a reviewed template."""
    _set_translatable_text(
        widget,
        source,
        allow_templates=True,
        template_min_literal=template_min_literal,
    )


def _set_translatable_tooltip(
    widget: QWidget,
    source: str,
    *,
    allow_templates: bool,
) -> None:
    """Set and immediately translate one reviewed launcher tooltip."""
    widget.setToolTip(source)
    widget.setProperty(_TRANSLATABLE, True)
    widget.setProperty(_SOURCE_TOOLTIP, source)
    widget.setProperty(_RENDERED_TOOLTIP, source)
    widget.setProperty(_TOOLTIP_ALLOW_TEMPLATES, allow_templates)
    _retranslate_property(
        widget,
        widget.toolTip,
        widget.setToolTip,
        current_language(),
        _SOURCE_TOOLTIP,
        _RENDERED_TOOLTIP,
        allow_templates=allow_templates,
    )


def set_translatable_tooltip(widget: QWidget, source: str) -> None:
    """Set an exact reviewed tooltip; unknown diagnostics pass through."""
    _set_translatable_tooltip(widget, source, allow_templates=False)


def set_translatable_tooltip_template(widget: QWidget, source: str) -> None:
    """Set trusted launcher tooltip framing rendered from a template."""
    _set_translatable_tooltip(widget, source, allow_templates=True)


def set_translatable_accessible_name(
    widget: QWidget,
    source: str,
    *,
    allow_templates: bool = False,
) -> None:
    """Set and immediately translate launcher-owned accessible-name text."""
    widget.setAccessibleName(source)
    widget.setProperty(_TRANSLATABLE, True)
    widget.setProperty(_SOURCE_ACCESSIBLE_NAME, source)
    widget.setProperty(_RENDERED_ACCESSIBLE_NAME, source)
    widget.setProperty(_ACCESSIBLE_NAME_ALLOW_TEMPLATES, allow_templates)
    _retranslate_property(
        widget,
        widget.accessibleName,
        widget.setAccessibleName,
        current_language(),
        _SOURCE_ACCESSIBLE_NAME,
        _RENDERED_ACCESSIBLE_NAME,
        allow_templates=allow_templates,
    )


def set_translatable_accessible_description(
    widget: QWidget,
    source: str,
    *,
    allow_templates: bool = False,
) -> None:
    """Set and translate launcher-owned accessible-description text."""
    widget.setAccessibleDescription(source)
    widget.setProperty(_TRANSLATABLE, True)
    widget.setProperty(_SOURCE_ACCESSIBLE_DESCRIPTION, source)
    widget.setProperty(_RENDERED_ACCESSIBLE_DESCRIPTION, source)
    widget.setProperty(
        _ACCESSIBLE_DESCRIPTION_ALLOW_TEMPLATES,
        allow_templates,
    )
    _retranslate_property(
        widget,
        widget.accessibleDescription,
        widget.setAccessibleDescription,
        current_language(),
        _SOURCE_ACCESSIBLE_DESCRIPTION,
        _RENDERED_ACCESSIBLE_DESCRIPTION,
        allow_templates=allow_templates,
    )


def register_translatable_combo_item(
    combo: QComboBox,
    index: int,
    source: str | None = None,
) -> None:
    """Opt one launcher-owned combo item into translation without touching data."""
    if index < 0 or index >= combo.count():
        return
    source_text = combo.itemText(index) if source is None else source
    if not is_reviewed_ui_phrase(source_text):
        return
    combo.setItemData(index, source_text, _COMBO_SOURCE_ROLE)
    combo.setProperty(_TRANSLATABLE, True)
    rendered = translate_ui_phrase(source_text, current_language())
    if combo.itemText(index) != rendered:
        combo.setItemText(index, rendered)


def _register_reviewed_combo_items(combo: QComboBox) -> None:
    for index in range(combo.count()):
        register_translatable_combo_item(combo, index)


def _retranslate_combo_items(combo: QComboBox, language: str) -> None:
    for index in range(combo.count()):
        source = combo.itemData(index, _COMBO_SOURCE_ROLE)
        if isinstance(source, str):
            combo.setItemText(index, translate_ui_phrase(source, language))


def _candidate_source(widget: QObject) -> str | None:
    if isinstance(widget, QComboBox):
        return None
    if isinstance(widget, QLineEdit):
        return widget.placeholderText()
    if isinstance(widget, QGroupBox):
        return widget.title()
    if isinstance(widget, (QLabel, QAbstractButton)):
        return widget.text()
    return None


def register_translatable_widget_tree(root: QObject) -> None:
    """Register reviewed phrases already present in a static widget tree.

    Callers must invoke this once, before adding any user/server-provided rows.
    Ordinary retranslation never performs registration, so widgets created
    later remain data by default even when their value happens to be
    ``"Settings"`` or another catalog phrase.
    """
    for widget in (root, *root.findChildren(QObject)):
        if widget.property("i18nIgnore"):
            continue
        if isinstance(widget, QComboBox):
            _register_reviewed_combo_items(widget)
        source = _candidate_source(widget)
        if source and is_reviewed_ui_phrase(source):
            mark_translatable(widget)
        tooltip = widget.toolTip() if isinstance(widget, QWidget) else ""
        if tooltip and is_reviewed_ui_phrase(tooltip):
            widget.setProperty(_TRANSLATABLE, True)
            widget.setProperty(_TOOLTIP_ALLOW_TEMPLATES, False)
            _retranslate_property(
                widget,
                widget.toolTip,
                widget.setToolTip,
                current_language(),
                _SOURCE_TOOLTIP,
                _RENDERED_TOOLTIP,
                register_missing=True,
            )
        window_title = widget.windowTitle() if isinstance(widget, QWidget) else ""
        if window_title and is_reviewed_ui_phrase(window_title):
            widget.setProperty(_TRANSLATABLE, True)
            widget.setProperty(_WINDOW_TITLE_ALLOW_TEMPLATES, False)
            _retranslate_property(
                widget,
                widget.windowTitle,
                widget.setWindowTitle,
                current_language(),
                _SOURCE_WINDOW_TITLE,
                _RENDERED_WINDOW_TITLE,
                register_missing=True,
            )
        if isinstance(widget, QWidget):
            accessible_name = widget.accessibleName()
            if accessible_name and is_reviewed_ui_phrase(accessible_name):
                widget.setProperty(_TRANSLATABLE, True)
                widget.setProperty(_ACCESSIBLE_NAME_ALLOW_TEMPLATES, False)
                _retranslate_property(
                    widget,
                    widget.accessibleName,
                    widget.setAccessibleName,
                    current_language(),
                    _SOURCE_ACCESSIBLE_NAME,
                    _RENDERED_ACCESSIBLE_NAME,
                    register_missing=True,
                )
            accessible_description = widget.accessibleDescription()
            if accessible_description and is_reviewed_ui_phrase(
                accessible_description
            ):
                widget.setProperty(_TRANSLATABLE, True)
                widget.setProperty(
                    _ACCESSIBLE_DESCRIPTION_ALLOW_TEMPLATES,
                    False,
                )
                _retranslate_property(
                    widget,
                    widget.accessibleDescription,
                    widget.setAccessibleDescription,
                    current_language(),
                    _SOURCE_ACCESSIBLE_DESCRIPTION,
                    _RENDERED_ACCESSIBLE_DESCRIPTION,
                    register_missing=True,
                )


def retranslate_widget_tree(root: QObject, language: str) -> None:
    """Retranslate explicitly registered static phrases below *root*.

    Unregistered labels are never inspected as phrases.  This is the important
    safety boundary that keeps user and server data intact.
    """
    objects = (root, *root.findChildren(QObject))
    for widget in objects:
        if widget.property("i18nIgnore") or not widget.property(_TRANSLATABLE):
            continue
        if isinstance(widget, QComboBox):
            _retranslate_combo_items(widget, language)
        elif isinstance(widget, QLineEdit):
            _retranslate_property(
                widget,
                widget.placeholderText,
                widget.setPlaceholderText,
                language,
                _SOURCE_PLACEHOLDER,
                _RENDERED_PLACEHOLDER,
                allow_templates=bool(
                    widget.property(_PLACEHOLDER_ALLOW_TEMPLATES)
                ),
            )
        elif isinstance(widget, QGroupBox):
            _retranslate_property(
                widget,
                widget.title,
                widget.setTitle,
                language,
                _SOURCE_TEXT,
                _RENDERED_TEXT,
                allow_templates=bool(widget.property(_TEXT_ALLOW_TEMPLATES)),
                template_min_literal=int(
                    widget.property(_TEXT_TEMPLATE_MIN_LITERAL) or 0
                ),
            )
        elif isinstance(widget, (QLabel, QAbstractButton)):
            _retranslate_property(
                widget,
                widget.text,
                widget.setText,
                language,
                _SOURCE_TEXT,
                _RENDERED_TEXT,
                allow_templates=bool(widget.property(_TEXT_ALLOW_TEMPLATES)),
                template_min_literal=int(
                    widget.property(_TEXT_TEMPLATE_MIN_LITERAL) or 0
                ),
            )
        if isinstance(widget, QWidget):
            _retranslate_property(
                widget,
                widget.toolTip,
                widget.setToolTip,
                language,
                _SOURCE_TOOLTIP,
                _RENDERED_TOOLTIP,
                allow_templates=bool(
                    widget.property(_TOOLTIP_ALLOW_TEMPLATES)
                ),
            )
            _retranslate_property(
                widget,
                widget.windowTitle,
                widget.setWindowTitle,
                language,
                _SOURCE_WINDOW_TITLE,
                _RENDERED_WINDOW_TITLE,
                allow_templates=bool(
                    widget.property(_WINDOW_TITLE_ALLOW_TEMPLATES)
                ),
            )
            _retranslate_property(
                widget,
                widget.accessibleName,
                widget.setAccessibleName,
                language,
                _SOURCE_ACCESSIBLE_NAME,
                _RENDERED_ACCESSIBLE_NAME,
                allow_templates=bool(
                    widget.property(_ACCESSIBLE_NAME_ALLOW_TEMPLATES)
                ),
            )
            _retranslate_property(
                widget,
                widget.accessibleDescription,
                widget.setAccessibleDescription,
                language,
                _SOURCE_ACCESSIBLE_DESCRIPTION,
                _RENDERED_ACCESSIBLE_DESCRIPTION,
                allow_templates=bool(
                    widget.property(
                        _ACCESSIBLE_DESCRIPTION_ALLOW_TEMPLATES
                    )
                ),
            )
