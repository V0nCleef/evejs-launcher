"""Create the deterministic, purely tonal Celestial Transit track.

The synthesis path intentionally contains no random generator, sampled ambience,
filtered noise, or noise oscillator. Every audible source is a pitched additive
oscillator. Space comes from fixed musical delay taps rather than a noise-like
reverb tail.
"""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE = 44_100


@dataclass(frozen=True)
class Timbre:
    partials: tuple[tuple[float, float], ...]
    detunes: tuple[tuple[float, float], ...] = ((0.0, 1.0),)
    tremolo_hz: float = 0.0
    tremolo_depth: float = 0.0


PAD = Timbre(
    partials=((1.0, 1.0), (2.0, 0.22), (3.0, 0.08)),
    detunes=((-6.0, 0.29), (0.0, 0.42), (6.0, 0.29)),
    tremolo_hz=0.075,
    tremolo_depth=0.035,
)
HORN = Timbre(
    partials=((1.0, 1.0), (2.0, 0.31), (3.0, 0.15), (4.0, 0.045)),
    detunes=((-3.5, 0.45), (3.5, 0.45), (0.0, 0.10)),
    tremolo_hz=0.12,
    tremolo_depth=0.025,
)
LOW_STRINGS = Timbre(
    partials=((1.0, 1.0), (2.0, 0.35), (3.0, 0.13), (4.0, 0.05)),
    detunes=((-2.0, 0.46), (2.0, 0.46), (0.0, 0.08)),
    tremolo_hz=0.09,
    tremolo_depth=0.025,
)
PURE_BASS = Timbre(partials=((1.0, 1.0), (2.0, 0.18), (3.0, 0.045)))
SOFT_PULSE = Timbre(partials=((1.0, 1.0), (2.0, 0.13)))
GLASS_LOW = Timbre(
    partials=((1.0, 1.0), (2.0, 0.12), (3.0, 0.055), (5.0, 0.018)),
    detunes=((-2.5, 0.50), (2.5, 0.50)),
)


def midi_hz(note: int | float) -> float:
    return 440.0 * 2.0 ** ((float(note) - 69.0) / 12.0)


def equal_power_pan(pan: float) -> tuple[float, float]:
    angle = (max(-1.0, min(1.0, pan)) + 1.0) * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def shaped_envelope(
    length: int,
    hold_samples: int,
    attack_samples: int,
    release_samples: int,
) -> np.ndarray:
    env = np.ones(length, dtype=np.float64)
    attack_samples = max(1, min(attack_samples, length))
    env[:attack_samples] = np.sin(
        np.linspace(0.0, math.pi / 2.0, attack_samples, endpoint=True)
    ) ** 2

    release_start = max(attack_samples, min(hold_samples, length - 1))
    release_end = min(length, release_start + max(1, release_samples))
    release_length = release_end - release_start
    if release_length:
        env[release_start:release_end] = np.cos(
            np.linspace(0.0, math.pi / 2.0, release_length, endpoint=True)
        ) ** 2
    if release_end < length:
        env[release_end:] = 0.0
    return env


def add_note(
    bus: np.ndarray,
    *,
    start: float,
    hold: float,
    midi: int | float,
    amplitude: float,
    timbre: Timbre,
    pan: float = 0.0,
    attack: float = 0.08,
    release: float = 0.50,
    phase_seed: float = 0.0,
) -> None:
    """Mix one additive note into a stereo bus."""

    start_sample = max(0, int(round(start * SAMPLE_RATE)))
    length = int(round((hold + release) * SAMPLE_RATE))
    end_sample = min(len(bus), start_sample + length)
    length = end_sample - start_sample
    if length <= 1:
        return

    time = np.arange(length, dtype=np.float64) / SAMPLE_RATE
    env = shaped_envelope(
        length,
        int(round(hold * SAMPLE_RATE)),
        int(round(attack * SAMPLE_RATE)),
        int(round(release * SAMPLE_RATE)),
    )
    if timbre.tremolo_hz:
        tremolo = 1.0 + timbre.tremolo_depth * np.sin(
            2.0 * math.pi * timbre.tremolo_hz * time + 0.41 + phase_seed
        )
        env *= tremolo

    partial_norm = sum(weight for _, weight in timbre.partials)
    base_frequency = midi_hz(midi)
    for detune_index, (detune_cents, detune_weight) in enumerate(timbre.detunes):
        detune_ratio = 2.0 ** (detune_cents / 1200.0)
        tone = np.zeros(length, dtype=np.float64)
        for partial_index, (ratio, partial_weight) in enumerate(timbre.partials):
            phase = phase_seed + 0.37 * partial_index + 0.19 * detune_index
            tone += (partial_weight / partial_norm) * np.sin(
                2.0 * math.pi * base_frequency * ratio * detune_ratio * time + phase
            )

        local_pan = pan + (detune_index - (len(timbre.detunes) - 1) / 2.0) * 0.10
        left_gain, right_gain = equal_power_pan(local_pan)
        signal = amplitude * detune_weight * env * tone
        bus[start_sample:end_sample, 0] += signal * left_gain
        bus[start_sample:end_sample, 1] += signal * right_gain


def add_chord(
    bus: np.ndarray,
    *,
    start: float,
    hold: float,
    notes: tuple[int, ...],
    amplitude: float,
    timbre: Timbre,
    attack: float,
    release: float,
) -> None:
    pans = np.linspace(-0.58, 0.58, len(notes))
    for index, (note, pan) in enumerate(zip(notes, pans, strict=True)):
        add_note(
            bus,
            start=start,
            hold=hold,
            midi=note,
            amplitude=amplitude,
            timbre=timbre,
            pan=float(pan),
            attack=attack,
            release=release,
            phase_seed=0.23 * index,
        )


def fixed_tonal_space(bus: np.ndarray, wet: float) -> np.ndarray:
    """A deterministic multi-tap delay; it adds echoes, never a noise tail."""

    result = bus.copy()
    taps = ((0.23, 0.34), (0.41, 0.23), (0.67, 0.16), (0.91, 0.10))
    for tap_index, (seconds, gain) in enumerate(taps):
        delay = int(round(seconds * SAMPLE_RATE))
        if delay >= len(bus):
            continue
        if tap_index % 2:
            result[delay:, 0] += bus[:-delay, 1] * gain * wet
            result[delay:, 1] += bus[:-delay, 0] * gain * wet
        else:
            result[delay:] += bus[:-delay] * gain * wet
    return result


def apply_master_fades(audio: np.ndarray, fade_in: float, fade_out: float) -> None:
    in_samples = min(len(audio), int(round(fade_in * SAMPLE_RATE)))
    out_samples = min(len(audio), int(round(fade_out * SAMPLE_RATE)))
    audio[:in_samples] *= np.sin(
        np.linspace(0.0, math.pi / 2.0, in_samples, endpoint=True)
    )[:, None] ** 2
    audio[-out_samples:] *= np.cos(
        np.linspace(0.0, math.pi / 2.0, out_samples, endpoint=True)
    )[:, None] ** 2


def finalize(audio: np.ndarray) -> np.ndarray:
    # Gentle deterministic saturation adds musical overtones and tames peaks.
    audio = np.tanh(audio * 1.08) / math.tanh(1.08)
    dc = np.mean(audio, axis=0)
    audio -= dc

    rms = float(np.sqrt(np.mean(np.square(audio))))
    peak = float(np.max(np.abs(audio)))
    target_rms = 10.0 ** (-19.5 / 20.0)
    scale = min(target_rms / max(rms, 1e-12), 0.78 / max(peak, 1e-12))
    return np.clip(audio * scale, -0.999, 0.999)


def compose_celestial_transit() -> tuple[np.ndarray, dict[str, object]]:
    """A melodic 16-bar D-minor journey at 70 BPM."""

    bpm = 70.0
    beat = 60.0 / bpm
    bars = 16
    duration = bars * 4.0 * beat
    frames = int(round(duration * SAMPLE_RATE))
    pad = np.zeros((frames, 2), dtype=np.float64)
    bass = np.zeros_like(pad)
    lead = np.zeros_like(pad)
    pulse = np.zeros_like(pad)

    progression = (
        ((50, 53, 57, 64), 38),  # Dm(add9), D2 bass
        ((46, 50, 53, 57), 34),  # Bbmaj7
        ((41, 45, 48, 55), 29),  # F(add9)
        ((48, 52, 55, 62), 36),  # C(add9)
    )
    for bar in range(bars):
        chord, root = progression[bar % len(progression)]
        start = bar * 4.0 * beat
        add_chord(
            pad,
            start=start,
            hold=4.0 * beat,
            notes=chord,
            amplitude=0.19 if bar < 12 else 0.215,
            timbre=PAD,
            attack=0.65,
            release=1.25,
        )
        add_note(
            bass,
            start=start,
            hold=3.65 * beat,
            midi=root,
            amplitude=0.26,
            timbre=PURE_BASS,
            attack=0.18,
            release=0.85,
        )
        add_note(
            bass,
            start=start + 2.0 * beat,
            hold=1.55 * beat,
            midi=root + 7,
            amplitude=0.105,
            timbre=LOW_STRINGS,
            attack=0.22,
            release=0.65,
            pan=0.05,
        )

        # A slow, pitched heartbeat. Fixed 45 Hz-ish tones; no sweeps or clicks.
        for offset, strength in ((0.0, 0.12), (2.0, 0.075)):
            add_note(
                pulse,
                start=start + offset * beat,
                hold=0.40,
                midi=29,
                amplitude=strength,
                timbre=SOFT_PULSE,
                attack=0.045,
                release=0.38,
            )

    # Beat-relative melody. It stays below D5 and leaves broad gaps for speech.
    melody: tuple[tuple[float, float, int], ...] = (
        (16.0, 1.50, 69), (17.5, 0.50, 72), (18.0, 1.75, 74),
        (20.0, 1.00, 65), (21.0, 1.00, 69), (22.0, 1.00, 72), (23.0, 0.80, 69),
        (24.0, 1.75, 67), (26.0, 0.75, 69), (27.0, 0.80, 72),
        (28.0, 1.00, 64), (29.0, 1.00, 67), (30.0, 1.65, 69),
        (32.0, 0.90, 69), (33.0, 0.90, 72), (34.0, 1.35, 74), (35.5, 0.40, 72),
        (36.0, 0.90, 65), (37.0, 0.90, 69), (38.0, 1.70, 74),
        (40.0, 1.40, 72), (41.5, 0.45, 69), (42.0, 1.65, 67),
        (44.0, 0.90, 64), (45.0, 0.90, 62), (46.0, 0.85, 67), (47.0, 0.75, 69),
        (48.0, 1.25, 69), (49.5, 0.45, 72), (50.0, 1.75, 74),
        (52.0, 0.90, 77), (53.0, 0.90, 76), (54.0, 1.70, 74),
        (56.0, 0.90, 72), (57.0, 0.90, 69), (58.0, 1.60, 67),
        (60.0, 0.90, 64), (61.0, 0.90, 67), (62.0, 1.60, 69),
    )
    for index, (beat_pos, hold_beats, note) in enumerate(melody):
        add_note(
            lead,
            start=beat_pos * beat,
            hold=hold_beats * beat,
            midi=note,
            amplitude=0.17 if beat_pos < 48 else 0.19,
            timbre=HORN,
            pan=-0.14 if index % 2 == 0 else 0.14,
            attack=0.16,
            release=0.72,
            phase_seed=index * 0.071,
        )

    # A restrained response phrase only in the final four bars.
    counterline = ((50.0, 2.0, 57), (54.0, 2.0, 60), (58.0, 2.0, 55), (62.0, 1.5, 52))
    for index, (beat_pos, hold_beats, note) in enumerate(counterline):
        add_note(
            lead,
            start=beat_pos * beat,
            hold=hold_beats * beat,
            midi=note,
            amplitude=0.095,
            timbre=GLASS_LOW,
            pan=0.42 if index % 2 == 0 else -0.42,
            attack=0.30,
            release=1.1,
        )

    audio = (
        fixed_tonal_space(pad, 0.28)
        + bass
        + fixed_tonal_space(lead, 0.58)
        + pulse
    )
    apply_master_fades(audio, 1.5, 3.2)
    return finalize(audio), {
        "title": "Celestial Transit",
        "variant": "melodic",
        "bpm": bpm,
        "key": "D minor",
        "bars": bars,
        "duration_seconds": duration,
        "form": "4-bar tonal introduction; 8-bar main motif; 4-bar lift and resolution",
        "sources": "additive pitched oscillators and fixed tonal delay taps only",
    }


def write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = np.round(np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm.tobytes())
