"""Custom frameless-window title bar for EveJS Launcher V2.

A 36px bar with app logo, title text and min/max/close buttons rendered
with universally-available Unicode glyphs. Supports drag-to-move and double-click
to toggle maximize. Window controls invoke the top-level window directly while
launcher actions use explicit signals.
"""
from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
    QResizeEvent,
)
from PyQt6.QtWidgets import (
    QFrame,
    QWidget,
    QHBoxLayout,
    QLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
)

from src.constants import COLORS, APP_TITLE
from src.i18n import format_ui_phrase, translate_ui_phrase
from src.widgets.update_button import UpdateButton
from src.widgets.ui_translation import (
    register_translatable_widget_tree,
    set_translatable_accessible_description,
    set_translatable_accessible_name,
    set_translatable_text,
    set_translatable_tooltip,
)

# Simple universally-available glyphs for window controls
_GLYPH_MIN = "—"       # em dash for minimize
_GLYPH_MAX = "□"       # white square for maximize
_GLYPH_RESTORE = "❐"   # lower right drop-shadowed white square for restore
_GLYPH_CLOSE = "✕"     # multiplication X for close

_WIN_BTN_FONT = "Segoe UI"

_ASSETS_LOGO = Path(__file__).resolve().parent.parent.parent / "assets" / "logo.png"


class _TitleButton(QPushButton):
    """Small square glyph button used on the title bar."""

    def __init__(self, glyph: str, parent: QWidget | None = None, hover_bg: str | None = None):
        super().__init__(glyph, parent)
        self.setFixedSize(46, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        font = QFont(_WIN_BTN_FONT)
        font.setPixelSize(14)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(font)
        hover = hover_bg or COLORS["steel"]
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {COLORS["white"]};
            }}
            QPushButton:hover {{
                background: {hover};
            }}
            QPushButton:pressed {{
                background: {COLORS["carbon"]};
            }}
            """
        )


class _AudioTransportButton(QPushButton):
    """Compact keyboard-accessible previous/next soundtrack control."""

    def __init__(
        self,
        glyph: str,
        accessible_name: str,
        accessible_description: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(glyph, parent)
        self.setFixedSize(22, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoDefault(False)
        font = QFont(_WIN_BTN_FONT)
        font.setPixelSize(16)
        font.setBold(True)
        self.setFont(font)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                color: {COLORS["teal_dim"]};
                padding: 0;
            }}
            QPushButton:hover {{
                background: {COLORS["steel"]};
                border-color: {COLORS["teal_dim"]};
                color: {COLORS["white"]};
            }}
            QPushButton:pressed {{
                background: {COLORS["carbon"]};
                color: {COLORS["gold"]};
            }}
            QPushButton:focus {{
                border: 1px solid {COLORS["teal"]};
                color: {COLORS["teal"]};
            }}
            """
        )
        set_translatable_tooltip(self, accessible_name)
        set_translatable_accessible_name(self, accessible_name)
        set_translatable_accessible_description(
            self,
            accessible_description,
        )


class _MusicSpectrum(QWidget):
    """Render the controller's real deterministic 16-band music spectrum."""

    BAND_COUNT = 16
    FRAME_INTERVAL_MS = 33
    ATTACK = 0.48
    FALLOFF = 0.18
    PEAK_HOLD_FRAMES = 6
    PEAK_DECAY = 0.035
    SETTLE_EPSILON = 0.002

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._active = False
        self._target_levels = (0.0,) * self.BAND_COUNT
        self._display_levels = [0.0] * self.BAND_COUNT
        self._peak_levels = [0.0] * self.BAND_COUNT
        self._peak_holds = [0] * self.BAND_COUNT
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(self.FRAME_INTERVAL_MS)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.timeout.connect(self._advance_animation)

        self.setObjectName("musicSpectrum")
        self.setMinimumWidth(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        set_translatable_accessible_name(self, "Live music spectrum")
        self._sync_accessibility()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(86, 18)

    @staticmethod
    def _normalized_levels(levels: object) -> tuple[float, ...]:
        """Clamp, truncate, and zero-pad one untrusted spectrum payload."""

        try:
            supplied = tuple(levels)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            supplied = ()
        normalized: list[float] = []
        for value in supplied[: _MusicSpectrum.BAND_COUNT]:
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError):
                numeric = 0.0
            if not math.isfinite(numeric):
                numeric = 0.0
            normalized.append(min(1.0, max(0.0, numeric)))
        normalized.extend(
            0.0 for _ in range(_MusicSpectrum.BAND_COUNT - len(normalized))
        )
        return tuple(normalized)

    def set_levels(self, levels: object) -> None:
        """Target one real controller frame; inactive displays stay at zero."""

        normalized = self._normalized_levels(levels)
        self._target_levels = (
            normalized if self._active else (0.0,) * self.BAND_COUNT
        )
        if self._needs_animation() and not self._animation_timer.isActive():
            self._animation_timer.start()

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if self._active == active:
            return
        self._active = active
        if not active:
            self._target_levels = (0.0,) * self.BAND_COUNT
        self._sync_accessibility()
        if self._needs_animation() and not self._animation_timer.isActive():
            self._animation_timer.start()
        self.update()

    def is_active(self) -> bool:
        return self._active

    def levels(self) -> tuple[float, ...]:
        """Return the currently painted smoothed levels for verification."""

        return tuple(self._display_levels)

    def target_levels(self) -> tuple[float, ...]:
        """Return the normalized target frame for verification."""

        return self._target_levels

    def peak_levels(self) -> tuple[float, ...]:
        """Return the currently painted peak markers for verification."""

        return tuple(self._peak_levels)

    def is_animating(self) -> bool:
        return self._animation_timer.isActive()

    def _sync_accessibility(self) -> None:
        set_translatable_accessible_description(
            self,
            (
                "Live 16-band music spectrum visualization."
                if self._active
                else "Music spectrum is inactive."
            ),
        )

    def _needs_animation(self) -> bool:
        if any(
            abs(target - current) > self.SETTLE_EPSILON
            for target, current in zip(
                self._target_levels,
                self._display_levels,
                strict=True,
            )
        ):
            return True
        return any(
            peak - current > self.SETTLE_EPSILON
            for peak, current in zip(
                self._peak_levels,
                self._display_levels,
                strict=True,
            )
        )

    def _advance_animation(self) -> None:
        """Advance one deterministic attack/falloff and peak-decay frame."""

        zero_target = not any(self._target_levels)
        for index, target in enumerate(self._target_levels):
            current = self._display_levels[index]
            smoothing = self.ATTACK if target > current else self.FALLOFF
            current += (target - current) * smoothing
            if abs(target - current) <= self.SETTLE_EPSILON:
                current = target
            self._display_levels[index] = min(1.0, max(0.0, current))

            if current >= self._peak_levels[index]:
                self._peak_levels[index] = current
                self._peak_holds[index] = self.PEAK_HOLD_FRAMES
            elif self._peak_holds[index] > 0:
                self._peak_holds[index] -= 1
            else:
                self._peak_levels[index] = max(
                    current,
                    self._peak_levels[index] - self.PEAK_DECAY,
                )

        if not self._needs_animation():
            if zero_target:
                self._display_levels = [0.0] * self.BAND_COUNT
                self._peak_levels = [0.0] * self.BAND_COUNT
                self._peak_holds = [0] * self.BAND_COUNT
            self._animation_timer.stop()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        baseline = QColor(COLORS["teal_dim"])
        baseline.setAlpha(35 if self._active else 20)
        painter.setPen(QPen(baseline, 1.0))
        bottom = float(self.height() - 2)
        painter.drawLine(QPointF(1.0, bottom), QPointF(self.width() - 1.0, bottom))

        count = self.BAND_COUNT
        usable_width = max(1.0, float(self.width() - 2))
        gap = max(0.4, min(1.8, usable_width / count * 0.28))
        bar_width = max(
            0.7,
            (usable_width - gap * (count - 1)) / count,
        )
        total_width = bar_width * count + gap * (count - 1)
        start_x = max(1.0, (self.width() - total_width) / 2.0)
        available_height = max(4.0, bottom - 1.0)

        painter.setPen(Qt.PenStyle.NoPen)
        for index, level in enumerate(self._display_levels):
            if level > 0.0:
                height = max(1.0, available_height * level)
                x = start_x + index * (bar_width + gap)
                rect = QRectF(x, bottom - height, bar_width, height)

                glow = QColor(COLORS["teal"])
                glow.setAlpha(int(20 + 42 * level))
                painter.setBrush(glow)
                painter.drawRoundedRect(rect.adjusted(-1.0, -1.0, 1.0, 1.0), 1.4, 1.4)

                gradient = QLinearGradient(0.0, rect.bottom(), 0.0, rect.top())
                low = QColor(COLORS["teal_dim"])
                high = QColor(COLORS["teal"])
                low.setAlpha(170)
                high.setAlpha(245)
                gradient.setColorAt(0.0, low)
                gradient.setColorAt(1.0, high)
                painter.setBrush(gradient)
                painter.drawRoundedRect(rect, 0.9, 0.9)

            peak = self._peak_levels[index]
            if peak <= 0.0:
                continue
            x = start_x + index * (bar_width + gap)
            peak_y = bottom - available_height * peak
            gold = QColor(COLORS["gold"])
            gold.setAlpha(int(85 + 80 * peak))
            peak_pen = QPen(gold, 1.0)
            peak_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(peak_pen)
            painter.drawLine(
                QPointF(x + 0.2, peak_y),
                QPointF(x + bar_width - 0.2, peak_y),
            )
            painter.setPen(Qt.PenStyle.NoPen)


class _SpeakerGlyph(QWidget):
    """Small custom-painted speaker whose state remains legible when muted."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._muted = False
        self._active = False
        self.setObjectName("audioSpeakerGlyph")
        self.setFixedSize(22, 22)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName("Launcher music state")
        self._sync_accessibility()

    def set_state(self, *, muted: bool, active: bool) -> None:
        muted = bool(muted)
        active = bool(active)
        if self._muted == muted and self._active == active:
            return
        self._muted = muted
        self._active = active
        self._sync_accessibility()
        self.update()

    def is_muted(self) -> bool:
        return self._muted

    def _sync_accessibility(self) -> None:
        if self._muted:
            description = "Launcher music is muted. This control does not affect LYRA voice."
        elif self._active:
            description = "Launcher music soundscape is active."
        else:
            description = "Launcher music soundscape is off."
        set_translatable_accessible_description(self, description)
        set_translatable_tooltip(self, description)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._muted:
            color = QColor(COLORS["gold"])
        elif self._active:
            color = QColor(COLORS["teal"])
        else:
            color = QColor(COLORS["grey"])
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(color)

        speaker = QPolygonF(
            (
                QPointF(3.5, 8.0),
                QPointF(7.0, 8.0),
                QPointF(11.0, 4.5),
                QPointF(11.0, 17.5),
                QPointF(7.0, 14.0),
                QPointF(3.5, 14.0),
            )
        )
        painter.drawPolygon(speaker)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._muted:
            painter.drawLine(QPointF(14.0, 8.0), QPointF(19.0, 14.0))
            painter.drawLine(QPointF(19.0, 8.0), QPointF(14.0, 14.0))
        else:
            painter.drawArc(QRectF(10.5, 7.0, 6.0, 8.0), -55 * 16, 110 * 16)
            painter.drawArc(QRectF(10.0, 4.5, 10.0, 13.0), -55 * 16, 110 * 16)


class TitleBar(QWidget):
    """Frameless window title bar (36px).

    Calls ``window().showMinimized()``, ``window().showMaximized()`` /
    ``window().showNormal()`` and ``window().close()`` directly. Launcher-level
    update and audio actions are emitted as signals.
    """

    HEIGHT = 36

    update_clicked = pyqtSignal()
    music_mute_changed = pyqtSignal(bool)
    previous_music_requested = pyqtSignal()
    next_music_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, title: str = APP_TITLE):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName("TitleBar")
        self.setStyleSheet(
            f"""
            #TitleBar {{
                background: {COLORS["deep_space"]};
                border-bottom: 1px solid {COLORS["steel"]};
            }}
            """
        )

        self._drag_pos: QPoint | None = None
        self._ambience_active = False
        self._ambience_track_name = "STATION SOUNDSCAPE"
        self._audio_track_full_text = "SOUNDSCAPE OFF"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(6)
        # The shell owns its responsive collapse rules; do not let the current
        # child size hints pin a stale minimum width during a narrower resize.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        # ── Logo ─────────────────────────────────────────────────────────
        self._logo = QLabel(self)
        self._logo.setFixedSize(20, 20)
        if _ASSETS_LOGO.exists():
            pix = QPixmap(str(_ASSETS_LOGO)).scaled(
                20, 20,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._logo.setPixmap(pix)
        layout.addWidget(self._logo)

        # ── Title ────────────────────────────────────────────────────────
        self._title = QLabel(title, self)
        self._title.setStyleSheet(
            f"color: {COLORS['grey']}; font-size: 11px; font-weight: 600;"
            " letter-spacing: 2px; background: transparent;"
        )
        layout.addWidget(self._title)

        # Spacer
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(spacer)

        # ── Update pill ──────────────────────────────────────────────────
        self.update_btn = UpdateButton(self)
        self.update_btn.clicked.connect(self.update_clicked.emit)
        layout.addWidget(self.update_btn)

        # Persistent soundscape capsule reports an honest inactive state until
        # a playback/controller seam explicitly marks it active.
        self.audio_capsule = QFrame(self)
        self.audio_capsule.setObjectName("audioCapsule")
        self.audio_capsule.setFixedHeight(30)
        self.audio_capsule.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.audio_capsule.setAccessibleName("Launcher music controls")
        self.audio_capsule.setStyleSheet(
            f"""
            QFrame#audioCapsule {{
                background: {COLORS["carbon"]};
                border: 1px solid {COLORS["teal_dim"]};
                border-radius: 14px;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
            """
        )
        self._audio_layout = QHBoxLayout(self.audio_capsule)
        self._audio_layout.setContentsMargins(6, 1, 4, 1)
        self._audio_layout.setSpacing(5)

        self.audio_note_label = QLabel("\u266a", self.audio_capsule)
        self.audio_note_label.setObjectName("audioNoteLabel")
        self.audio_note_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audio_note_label.setFixedWidth(16)
        self.audio_note_label.setStyleSheet(
            f"color: {COLORS['teal']}; font-size: 15px; font-weight: 700;"
        )
        self.audio_note_label.setAccessibleName("Music")
        self._audio_layout.addWidget(self.audio_note_label)

        self.audio_track_label = QLabel("SOUNDSCAPE OFF", self.audio_capsule)
        self.audio_track_label.setObjectName("audioTrackLabel")
        self.audio_track_label.setMinimumWidth(0)
        self.audio_track_label.setMaximumWidth(148)
        self.audio_track_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.audio_track_label.setStyleSheet(
            f"color: {COLORS['grey']}; font-size: 9px; font-weight: 700;"
            " letter-spacing: 1px;"
        )
        self.audio_track_label.setToolTip(self._audio_track_full_text)
        self.audio_track_label.setAccessibleName(
            f"Current launcher music: {self._audio_track_full_text}"
        )
        self._audio_layout.addWidget(self.audio_track_label)

        self.audio_previous_btn = _AudioTransportButton(
            "‹",
            "Previous music track",
            "Play the previous launcher music track.",
            self.audio_capsule,
        )
        self.audio_previous_btn.setObjectName("previousMusicButton")
        self.audio_previous_btn.clicked.connect(
            self.previous_music_requested.emit
        )
        self._audio_layout.addWidget(self.audio_previous_btn)

        self.music_spectrum = _MusicSpectrum(self.audio_capsule)
        # Keep the established attribute as a compatibility alias for tests
        # and callers written before the display represented real FFT data.
        self.audio_waveform = self.music_spectrum
        self._audio_layout.addWidget(self.music_spectrum, 1)

        self.audio_next_btn = _AudioTransportButton(
            "›",
            "Next music track",
            "Play the next launcher music track.",
            self.audio_capsule,
        )
        self.audio_next_btn.setObjectName("nextMusicButton")
        self.audio_next_btn.clicked.connect(self.next_music_requested.emit)
        self._audio_layout.addWidget(self.audio_next_btn)

        self.audio_speaker_glyph = _SpeakerGlyph(self.audio_capsule)
        self._audio_layout.addWidget(self.audio_speaker_glyph)

        # Text and accessible descriptions keep the action understandable
        # without relying on the custom speaker glyph alone.
        self.audio_mute_btn = QPushButton("MUTE", self.audio_capsule)
        self.audio_mute_btn.setObjectName("musicMuteButton")
        self.audio_mute_btn.setCheckable(True)
        self.audio_mute_btn.setFixedSize(78, 26)
        self.audio_mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.audio_mute_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {COLORS["carbon"]};
                border: 1px solid {COLORS["steel"]};
                border-radius: 4px;
                color: {COLORS["grey"]};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 0 8px;
            }}
            QPushButton:hover {{
                border-color: {COLORS["teal_dim"]};
                color: {COLORS["white"]};
            }}
            QPushButton:checked {{
                background: {COLORS["steel"]};
                border-color: {COLORS["gold"]};
                color: {COLORS["gold"]};
            }}
            QPushButton:focus {{
                border: 1px solid {COLORS["teal"]};
            }}
            """
        )
        self.audio_mute_btn.toggled.connect(self._on_music_mute_toggled)
        self.set_music_muted(False)
        self._audio_layout.addWidget(self.audio_mute_btn)
        layout.addWidget(self.audio_capsule)

        # ── Window controls ──────────────────────────────────────────────
        self.btn_min = _TitleButton(_GLYPH_MIN, self)
        self.btn_min.setToolTip("Minimize")
        self.btn_min.clicked.connect(self._on_minimize)
        layout.addWidget(self.btn_min)

        self.btn_max = _TitleButton(_GLYPH_MAX, self)
        self.btn_max.setToolTip("Maximize")
        self.btn_max.clicked.connect(self._on_maximize_restore)
        layout.addWidget(self.btn_max)

        self.btn_close = _TitleButton(_GLYPH_CLOSE, self, hover_bg=COLORS["red"])
        self.btn_close.setToolTip("Close")
        self.btn_close.clicked.connect(self._on_close)
        layout.addWidget(self.btn_close)

        register_translatable_widget_tree(self)
        self._sync_audio_capsule_layout(self.width())

    # ── Public API ───────────────────────────────────────────────────────
    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def show_update_available(self, version: str) -> None:
        """Display the gold update pill with *version* and pulse animation."""
        self.update_btn.set_update_available(version)

    def set_update_checking(self) -> None:
        """Show the checking-for-updates spinner."""
        self.update_btn.set_checking()

    def set_update_up_to_date(self) -> None:
        """Hide the update button (no update available)."""
        self.update_btn.set_up_to_date()

    def set_music_muted(self, muted: bool) -> None:
        """Reflect the persisted music-only mute without re-emitting it."""
        muted = bool(muted)
        previous = self.audio_mute_btn.blockSignals(True)
        self.audio_mute_btn.setChecked(muted)
        self.audio_mute_btn.blockSignals(previous)
        set_translatable_text(
            self.audio_mute_btn,
            "MUTED" if muted else "MUTE",
        )
        action = "Unmute" if muted else "Mute"
        state = "muted" if muted else "on"
        set_translatable_tooltip(
            self.audio_mute_btn,
            f"{action} launcher music",
        )
        self.audio_mute_btn.setAccessibleName(
            translate_ui_phrase(f"{action} launcher music")
        )
        self.audio_mute_btn.setAccessibleDescription(
            format_ui_phrase(
                "Launcher music is {state}. Activate to {action} music; "
                "This control does not affect LYRA voice.",
                state=translate_ui_phrase(state),
                action=translate_ui_phrase(action.casefold()),
            )
        )
        self.audio_speaker_glyph.set_state(
            muted=muted,
            active=self._ambience_active,
        )
        self._sync_audio_capsule_accessibility()

    def set_audio_status(
        self,
        active: bool,
        track_name: str = "STATION SOUNDSCAPE",
    ) -> None:
        """Reflect the truthful looping soundscape status supplied by audio.

        The title bar deliberately defaults to ``SOUNDSCAPE OFF``. This method does
        not start playback and the waveform never represents playback progress.
        """
        self._ambience_active = bool(active)
        normalized_name = " ".join(str(track_name).split()).upper()
        self._ambience_track_name = normalized_name or "STATION SOUNDSCAPE"
        self._set_audio_track_text(
            self._ambience_track_name if self._ambience_active else "SOUNDSCAPE OFF"
        )
        self.audio_waveform.set_active(self._ambience_active)
        self.audio_speaker_glyph.set_state(
            muted=self.is_music_muted(),
            active=self._ambience_active,
        )
        self._sync_audio_capsule_accessibility()

    def set_music_spectrum(self, levels: object) -> None:
        """Consume one normalized controller spectrum frame without synthesis."""

        self.music_spectrum.set_levels(levels)

    def is_audio_active(self) -> bool:
        """Return the soundscape state displayed by the capsule."""
        return self._ambience_active

    def is_music_muted(self) -> bool:
        """Return the music-only state displayed by the title-bar control."""
        return self.audio_mute_btn.isChecked()

    def _on_music_mute_toggled(self, muted: bool) -> None:
        self.set_music_muted(muted)
        self.music_mute_changed.emit(bool(muted))

    def _sync_audio_capsule_accessibility(self) -> None:
        soundscape = (
            format_ui_phrase(
                "playing {track}",
                track=self._ambience_track_name,
            )
            if self._ambience_active
            else translate_ui_phrase("off")
        )
        music = translate_ui_phrase(
            "muted" if self.is_music_muted() else "unmuted"
        )
        self.audio_capsule.setAccessibleDescription(
            format_ui_phrase(
                "Launcher soundscape is {soundscape}; music is {music}. "
                "This control does not affect LYRA voice.",
                soundscape=soundscape,
                music=music,
            )
        )

    def _set_audio_track_text(self, text: str) -> None:
        """Show a compact elided title while retaining the complete identity."""
        self._audio_track_full_text = str(text)
        rendered_text = translate_ui_phrase(self._audio_track_full_text)
        self.audio_track_label.setToolTip(rendered_text)
        self.audio_track_label.setAccessibleName(
            format_ui_phrase(
                "Current launcher music: {track}",
                track=rendered_text,
            )
        )
        # A deliberately collapsed label has no useful contents geometry.
        # Keep its complete state text intact; the responsive layout elides it
        # again after making the label visible at a wider breakpoint.
        if self.audio_track_label.isHidden():
            self.audio_track_label.setText(rendered_text)
            return
        available = max(1, self.audio_track_label.contentsRect().width())
        self.audio_track_label.setText(
            self.audio_track_label.fontMetrics().elidedText(
                rendered_text,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )

    def retranslate_ui(self) -> None:
        """Refresh retained audio state without emitting user actions."""
        self.set_music_muted(self.is_music_muted())
        self.set_audio_status(
            self._ambience_active,
            self._ambience_track_name,
        )

    def _sync_audio_capsule_layout(self, width: int) -> None:
        """Collapse decoration before controls as the title bar narrows."""
        if width >= 1200:
            capsule_width = 410
            track_width = 180
            show_note, show_track, show_waveform, show_speaker = True, True, True, True
            show_transport = True
        elif width >= 920:
            capsule_width = 340
            track_width = 112
            show_note, show_track, show_waveform, show_speaker = True, True, True, True
            show_transport = True
        elif width >= 760:
            capsule_width = 282
            track_width = 82
            show_note, show_track, show_waveform, show_speaker = False, True, True, True
            show_transport = True
        elif width >= 620:
            capsule_width = 206
            track_width = 0
            show_note, show_track, show_waveform, show_speaker = False, False, True, True
            show_transport = True
        elif width >= 520:
            capsule_width = 124
            track_width = 0
            show_note, show_track, show_waveform, show_speaker = False, False, False, True
            show_transport = False
        else:
            capsule_width = 88
            track_width = 0
            show_note, show_track, show_waveform, show_speaker = False, False, False, False
            show_transport = False

        compact = capsule_width == 88
        self._audio_layout.setContentsMargins(4 if compact else 6, 1, 4, 1)
        self._audio_layout.setSpacing(3 if width < 920 else 4)
        self.audio_capsule.setFixedWidth(capsule_width)
        self.audio_note_label.setVisible(show_note)
        if show_track:
            self.audio_track_label.setFixedWidth(track_width)
        self.audio_track_label.setVisible(show_track)
        self.audio_previous_btn.setVisible(show_transport)
        self.audio_waveform.setVisible(show_waveform)
        self.audio_next_btn.setVisible(show_transport)
        self.audio_speaker_glyph.setVisible(show_speaker)
        if show_track:
            self._set_audio_track_text(self._audio_track_full_text)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        self._sync_audio_capsule_layout(event.size().width())
        super().resizeEvent(event)

    # ── Window control slots (call parent window directly) ───────────────
    def _on_minimize(self) -> None:
        win = self.window()
        if win is not None:
            win.showMinimized()

    def _on_maximize_restore(self) -> None:
        self._toggle_max_restore()

    def _on_close(self) -> None:
        win = self.window()
        if win is not None:
            win.close()

    def _toggle_max_restore(self) -> None:
        win = self.window()
        if win is None:
            return
        if win.isMaximized():
            win.showNormal()
            self.btn_max.setText(_GLYPH_MAX)
            set_translatable_tooltip(self.btn_max, "Maximize")
        else:
            win.showMaximized()
            self.btn_max.setText(_GLYPH_RESTORE)
            set_translatable_tooltip(self.btn_max, "Restore")

    # ── Drag-to-move / double-click maximize ─────────────────────────────
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            if win is not None:
                self._drag_pos = (
                    event.globalPosition().toPoint() - win.frameGeometry().topLeft()
                )
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            win = self.window()
            if win is not None:
                if win.isMaximized():
                    # Dragging from a maximized state restores first so the
                    # window follows the cursor naturally.
                    win.showNormal()
                    self.btn_max.setText(_GLYPH_MAX)
                win.move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_max_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(super().sizeHint().width(), self.HEIGHT)
