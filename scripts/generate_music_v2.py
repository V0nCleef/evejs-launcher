"""Generate three deterministic, pitched space-music review loops.

These prototypes deliberately avoid noise beds, chirps, and copied melodic material.
Only synthesized tonal instruments and algorithmic reverb are used.
"""

from __future__ import annotations

import argparse
import json
import math
import wave
from pathlib import Path

import numpy as np


SR = 44_100
DURATION = 48.0
N = int(SR * DURATION)
TAU = 2.0 * math.pi


def midi(note: float) -> float:
    return 440.0 * 2.0 ** ((note - 69.0) / 12.0)


def cyclic_distance(t: np.ndarray, center: float, period: float = DURATION) -> np.ndarray:
    return (t - center + period / 2.0) % period - period / 2.0


def periodic_envelope(t: np.ndarray, start: float, length: float, attack: float, release: float) -> np.ndarray:
    age = (t - start) % DURATION
    env = np.zeros_like(t, dtype=np.float32)
    on = age < length
    env[on] = 1.0
    if attack > 0:
        env[on] *= np.minimum(age[on] / attack, 1.0)
    if release > 0:
        env[on] *= np.minimum((length - age[on]) / release, 1.0)
    return np.maximum(env, 0.0)


def osc(freq: float, t: np.ndarray, kind: str = "sine", phase: float = 0.0) -> np.ndarray:
    p = TAU * freq * t + phase
    if kind == "sine":
        return np.sin(p)
    if kind == "soft":
        return np.sin(p) + 0.22 * np.sin(2.0 * p + 0.2) + 0.08 * np.sin(3.0 * p + 1.0)
    if kind == "string":
        return np.sin(p) + 0.28 * np.sin(2.0 * p + 0.4) + 0.12 * np.sin(3.0 * p + 0.8) + 0.04 * np.sin(4.0 * p)
    if kind == "organ":
        return np.sin(p) + 0.34 * np.sin(2.0 * p) + 0.18 * np.sin(3.0 * p + 0.1)
    raise ValueError(kind)


def tone(note: float, t: np.ndarray, kind: str, detune: float = 0.0) -> np.ndarray:
    f = midi(note)
    if detune <= 0:
        return osc(f, t, kind)
    cents = 2.0 ** (detune / 1200.0)
    return 0.56 * osc(f / cents, t, kind, 0.1) + 0.44 * osc(f * cents, t, kind, 1.7)


def add_note(bus: np.ndarray, t: np.ndarray, note: float, start: float, length: float, amp: float,
             attack: float, release: float, kind: str = "soft", pan: float = 0.0,
             detune: float = 0.0) -> None:
    env = periodic_envelope(t, start, length, attack, release)
    sig = tone(note, t, kind, detune) * env * amp
    left = math.sqrt((1.0 - pan) * 0.5)
    right = math.sqrt((1.0 + pan) * 0.5)
    bus[:, 0] += sig * left
    bus[:, 1] += sig * right


def add_chord(bus: np.ndarray, t: np.ndarray, notes: tuple[int, ...], start: float, length: float,
              amp: float, kind: str, attack: float, release: float, detune: float = 0.0) -> None:
    pans = np.linspace(-0.48, 0.48, len(notes))
    for note, pan in zip(notes, pans):
        add_note(bus, t, note, start, length, amp / math.sqrt(len(notes)), attack, release,
                 kind, float(pan), detune)


def tonal_reverb(x: np.ndarray, amount: float = 0.18) -> np.ndarray:
    y = x.copy()
    # Prime-ish delays, feedback-free; repeats wrap to retain exact loop continuity.
    for seconds, gain, cross in ((0.163, 0.18, False), (0.271, 0.12, True), (0.421, 0.075, False), (0.683, 0.045, True)):
        shifted = np.roll(x, int(seconds * SR), axis=0)
        if cross:
            shifted = shifted[:, ::-1]
        y += amount * gain / 0.18 * shifted
    return y


def slow_filter(x: np.ndarray, cutoff: float = 4200.0) -> np.ndarray:
    # One-pole low pass tames synthesis harmonics without introducing any noise.
    a = 1.0 - math.exp(-TAU * cutoff / SR)
    y = np.empty_like(x)
    y[0] = x[0]
    for i in range(1, len(x)):
        y[i] = y[i - 1] + a * (x[i] - y[i - 1])
    return y


def solemn_orchestral(t: np.ndarray) -> np.ndarray:
    b = np.zeros((N, 2), np.float32)
    # D minor: Dm - Bbmaj7 - F - Cadd9, one complete 48-second circular phrase.
    chords = [(38, 45, 50, 53, 57), (34, 41, 46, 50, 57), (29, 36, 41, 45, 48), (36, 43, 48, 50, 55)]
    roots = [26, 22, 29, 24]
    for bar, (chord, root) in enumerate(zip(chords, roots)):
        s = bar * 12.0
        add_chord(b, t, chord, s - 1.0, 14.0, 0.22, "string", 3.2, 4.0, 4.0)
        add_note(b, t, root, s, 11.5, 0.22, 1.6, 3.0, "soft", -0.05)
        add_note(b, t, root + 12, s, 10.0, 0.08, 2.0, 2.5, "sine", 0.10)
    # Restrained four-note identity, restated with a gentle answering descent.
    melody = [(0.5, 62, 4.5), (5.0, 65, 3.0), (8.0, 64, 3.6),
              (12.5, 57, 4.5), (17.0, 62, 3.0), (20.0, 60, 3.6),
              (24.5, 65, 4.5), (29.0, 69, 3.0), (32.0, 67, 3.6),
              (36.5, 64, 4.5), (41.0, 62, 3.0), (44.0, 60, 3.6)]
    for i, (s, n, length) in enumerate(melody):
        add_note(b, t, n, s, length, 0.075, 0.65, 1.7, "soft", -0.28 if i % 2 else 0.28)
    return tonal_reverb(slow_filter(b, 3500.0), 0.20)


def deep_synth_voyage(t: np.ndarray) -> np.ndarray:
    b = np.zeros((N, 2), np.float32)
    # C minor: Cm9 - Abmaj7 - Eb - Bbsus2. Dark but harmonically legible.
    chords = [(36, 43, 48, 51, 55), (32, 39, 44, 48, 55), (39, 46, 51, 55, 58), (34, 41, 46, 48, 53)]
    roots = [24, 20, 27, 22]
    for bar, (chord, root) in enumerate(zip(chords, roots)):
        s = bar * 12.0
        add_chord(b, t, chord, s - 0.75, 13.5, 0.18, "soft", 2.4, 3.3, 8.0)
        add_note(b, t, root, s, 11.7, 0.31, 0.5, 1.5, "sine", 0.0)
        # Pitched pulse, deliberately round and slow rather than percussive noise.
        for q in (0.0, 3.0, 6.0, 9.0):
            add_note(b, t, root + 12, s + q, 1.6, 0.085, 0.10, 0.65, "soft", -0.18 if q % 6 else 0.18)
    motif = [(1.5, 60), (4.5, 63), (7.5, 67), (10.0, 65),
             (13.5, 60), (16.5, 63), (19.5, 68), (22.0, 67),
             (25.5, 63), (28.5, 67), (31.5, 70), (34.0, 67),
             (37.5, 58), (40.5, 60), (43.5, 65), (46.0, 63)]
    for i, (s, n) in enumerate(motif):
        add_note(b, t, n, s, 2.2, 0.060, 0.20, 0.90, "sine", 0.34 if i % 2 else -0.34)
        add_note(b, t, n + 12, s, 1.2, 0.020, 0.15, 0.55, "sine", -0.15 if i % 2 else 0.15)
    return tonal_reverb(slow_filter(b, 3000.0), 0.14)


def mysterious_station_hymn(t: np.ndarray) -> np.ndarray:
    b = np.zeros((N, 2), np.float32)
    # E phrygian-leaning hymn: Em(add9) - Fmaj7 - Cmaj7 - Bsus4/B.
    chords = [(40, 47, 52, 55, 66), (41, 48, 53, 57, 64), (36, 43, 48, 52, 59), (35, 42, 47, 52, 59)]
    pedal = [28, 29, 24, 23]
    for bar, (chord, root) in enumerate(zip(chords, pedal)):
        s = bar * 12.0
        add_chord(b, t, chord, s - 1.2, 14.2, 0.19, "organ", 3.5, 4.2, 3.0)
        add_note(b, t, root, s, 11.5, 0.18, 1.2, 2.6, "sine", 0.0)
    # Bell-like but dark: fundamental plus only low harmonics, no chirp/transient noise.
    hymn = [(0.8, 64, 5.0), (6.2, 65, 4.2), (12.8, 64, 5.0), (18.2, 60, 4.2),
            (24.8, 59, 5.0), (30.2, 60, 4.2), (36.8, 57, 5.0), (42.2, 59, 4.2)]
    for i, (s, n, length) in enumerate(hymn):
        add_note(b, t, n, s, length, 0.085, 0.035, 2.8, "soft", -0.38 if i % 2 else 0.38)
        add_note(b, t, n + 12, s, min(length, 2.8), 0.024, 0.02, 1.6, "sine", 0.20 if i % 2 else -0.20)
    return tonal_reverb(slow_filter(b, 3200.0), 0.24)


def normalize_loop(x: np.ndarray) -> np.ndarray:
    # Remove numerical DC, then conservative peak normalize. Signals are mathematically periodic.
    x = x - np.mean(x, axis=0, keepdims=True)
    peak = float(np.max(np.abs(x)))
    return np.asarray(x * (0.78 / max(peak, 1e-9)), dtype=np.float32)


def write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = np.clip(samples * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(pcm.tobytes())


def metrics(x: np.ndarray) -> dict[str, float]:
    mono = x.mean(axis=1)
    # Windowed spectral summary from deterministic segments.
    size = 131_072
    window = np.hanning(size)
    powers = []
    for offset in (0, N // 4, N // 2, 3 * N // 4):
        seg = np.take(mono, np.arange(offset, offset + size) % N) * window
        powers.append(np.abs(np.fft.rfft(seg)) ** 2)
    p = np.mean(powers, axis=0)
    f = np.fft.rfftfreq(size, 1.0 / SR)
    total = float(p.sum()) + 1e-20
    centroid = float((f * p).sum() / total)
    cumulative = np.cumsum(p) / total
    roll95 = float(f[min(int(np.searchsorted(cumulative, 0.95)), len(f) - 1)])
    high_6k = float(p[f >= 6000].sum() / total * 100.0)
    high_10k = float(p[f >= 10000].sum() / total * 100.0)
    # Spectral flatness is a useful noise-likeness proxy (lower = more tonal).
    audible = p[(f >= 40) & (f <= 12000)] + 1e-20
    flatness = float(np.exp(np.mean(np.log(audible))) / np.mean(audible))
    seam = float(np.max(np.abs(x[0] - x[-1])))
    return {
        "duration_seconds": DURATION,
        "sample_rate_hz": SR,
        "peak_dbfs": round(20 * math.log10(float(np.max(np.abs(x))) + 1e-12), 2),
        "rms_dbfs": round(20 * math.log10(float(np.sqrt(np.mean(x * x))) + 1e-12), 2),
        "spectral_centroid_hz": round(centroid, 1),
        "spectral_rolloff_95_hz": round(roll95, 1),
        "energy_above_6khz_percent": round(high_6k, 5),
        "energy_above_10khz_percent": round(high_10k, 6),
        "spectral_flatness_noise_proxy": round(flatness, 7),
        "loop_endpoint_delta": round(seam, 7),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    t = np.arange(N, dtype=np.float64) / SR
    pieces = {
        "01_solemn_orchestral": solemn_orchestral,
        "02_deep_synth_voyage": deep_synth_voyage,
        "03_mysterious_station_hymn": mysterious_station_hymn,
    }
    report = {}
    for name, generator in pieces.items():
        samples = normalize_loop(generator(t))
        write_wav(args.output / f"{name}.wav", samples)
        report[name] = metrics(samples)
        print(name, json.dumps(report[name]))
    (args.output / "analysis.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
