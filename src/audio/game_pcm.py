"""One PCM stream with concurrent cue mixing entirely off the GUI thread."""
from array import array
from dataclasses import dataclass
from PyQt6.QtCore import QCoreApplication, QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices
from .game_cues import SAMPLE_RATE

@dataclass
class _Voice:
    frames: memoryview
    offset: int = 0


class _PcmMixer:
    """Mix short PCM blocks and advance each voice by the frames actually written."""

    max_voices = 32
    headroom = 30000

    def __init__(self):
        self._voices = []

    def play(self, data):
        if data and len(self._voices) < self.max_voices:
            self._voices.append(_Voice(memoryview(data).cast("h")))

    def clear(self):
        self._voices.clear()

    def render(self, size):
        count = size // 2
        if not self._voices:
            return bytes(size)
        if len(self._voices) == 1:
            voice = self._voices[0]
            block = voice.frames[voice.offset:voice.offset + count].tobytes()
            return block.ljust(size, b"\0")

        mix = [0] * count
        for voice in self._voices:
            for index, sample in enumerate(voice.frames[voice.offset:voice.offset + count]):
                mix[index] += sample
        peak = max(map(abs, mix))
        if peak > self.headroom:
            gain = self.headroom / peak
            mix = [round(sample * gain) for sample in mix]
        return array("h", mix).tobytes()

    def advance(self, size):
        count = size // 2
        for voice in self._voices:
            voice.offset += count
        self._voices = [voice for voice in self._voices if voice.offset < len(voice.frames)]


class _AudioStream(QObject):
    opened = pyqtSignal()
    failed = pyqtSignal(str)

    # Aim for 40 ms queued so stop/mute takes effect promptly.
    buffer_bytes = SAMPLE_RATE * 2 * 40 // 1000

    def __init__(self, volume):
        super().__init__()
        self._volume = volume
        self._mixer = _PcmMixer()
        self._sink = None
        self._device = None
        self._pending = b''

    @pyqtSlot()
    def open(self):
        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull() or not device.isFormatSupported(fmt):
            self.failed.emit("The default audio device cannot play sound effects.")
            return
        self._sink = QAudioSink(device, fmt, self)
        self._sink.setVolume(self._volume)
        self._sink.setBufferSize(self.buffer_bytes)
        self._device = self._sink.start()
        if self._device is None or self._sink.error() != QAudio.Error.NoError:
            self.failed.emit("Sound effects could not open the audio device.")
            return
        self._timer = QTimer(self, timerType=Qt.TimerType.PreciseTimer, interval=10)
        self._timer.timeout.connect(self._fill)
        self._fill()
        self._timer.start()
        self.opened.emit()

    @pyqtSlot(object)
    def play(self, data):
        self._mixer.play(data)

    @pyqtSlot()
    def clear(self):
        self._mixer.clear()
        self._pending = b''

    @pyqtSlot(float)
    def set_volume(self, volume):
        self._volume = max(0., min(1., volume))
        if self._sink is not None:
            self._sink.setVolume(self._volume)

    @pyqtSlot()
    def close(self):
        self.clear()
        if hasattr(self, '_timer'):
            self._timer.stop()
        if self._sink is not None:
            self._sink.stop()
        self.thread().quit()

    def _fill(self):
        if self._sink.state() == QAudio.State.StoppedState:
            self._timer.stop()
            self.failed.emit(f"Sound effects stopped ({self._sink.error().name}).")
            return
        free = self._sink.bytesFree()
        queued = max(0, self._sink.bufferSize() - free)
        size = max(0, min(free, self.buffer_bytes - queued)) & ~1
        if not size:
            return
        # Own the rendered block until every byte is accepted. A short or odd
        # QIODevice write must not restart samples or skip a newly queued voice.
        if not self._pending:
            self._pending = self._mixer.render(size)
            self._mixer.advance(size)
        written = self._device.write(self._pending[:size])
        if written > 0:
            self._pending = self._pending[written:]


class GameAudio(QObject):
    """Play overlapping cues on the audio thread without reopening the device."""
    _played = pyqtSignal(object)
    _stopped = pyqtSignal()
    _closed = pyqtSignal()
    _volume_changed = pyqtSignal(float)

    def __init__(self, parent, *, volume=1.0):
        super().__init__(parent)
        self.ready = False
        self.error = ""
        self._thread = QThread(QCoreApplication.instance())
        self._stream = _AudioStream(volume)
        self._stream.moveToThread(self._thread)
        self._thread.started.connect(self._stream.open)
        self._thread.finished.connect(self._stream.deleteLater)
        self._stream.opened.connect(self._mark_ready)
        self._stream.failed.connect(self._mark_failed)
        self._played.connect(self._stream.play)
        self._stopped.connect(self._stream.clear)
        self._closed.connect(self._stream.close)
        self._volume_changed.connect(self._stream.set_volume)
        QCoreApplication.instance().aboutToQuit.connect(self._join_at_exit)
        self._thread.start()

    def play(self, data):
        self._played.emit(data)

    def stop(self):
        self._stopped.emit()

    def set_volume(self, volume):
        self._volume_changed.emit(volume)

    def shutdown(self):
        # Normal window shutdown stays asynchronous. The application owns the
        # thread until it has closed its own sink on its own event loop.
        if self._thread.isRunning():
            self._closed.emit()

    def _join_at_exit(self):
        self.shutdown()
        self._thread.wait(1000)

    @pyqtSlot()
    def _mark_ready(self):
        self.ready = True

    @pyqtSlot(str)
    def _mark_failed(self, error):
        self.ready = False
        self.error = error
