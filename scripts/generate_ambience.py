"""Generate the original Deep Signal industrial station soundscape.

The mix is entirely synthesized: a dark low tonal bed, ventilation, structural
creaks, hydraulic gantries, distant ship traffic, docking clamps, and restrained
warp arrivals. It deliberately avoids bright chirps and high lead tones. It
samples no EVE Online or third-party audio. The fixed seed and standard-library-
only generator keep the asset reproducible.
"""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import random
import wave


SAMPLE_RATE = 22_050
DURATION_SECONDS = 96
CHANNELS = 2
SEED = 73_190
OUTPUT = Path("assets/audio/music/deep_signal_ambience.wav")
TAU = 2.0 * math.pi


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _smoothstep(value: float) -> float:
    value = _clamp(value)
    return value * value * (3.0 - 2.0 * value)


def _sine_window(value: float) -> float:
    return math.sin(math.pi * _clamp(value)) ** 2


def _loop_frequency(frequency: float) -> float:
    return round(frequency * DURATION_SECONDS) / DURATION_SECONDS


def _pan(position: float) -> tuple[float, float]:
    angle = (_clamp(position, -1.0, 1.0) + 1.0) * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def _add_flyby(
    left: array,
    right: array,
    *,
    start: float,
    duration: float,
    direction: int,
    size: float,
    phase: float,
) -> None:
    """Mix one distant thruster pass with motion encoded mainly by stereo."""
    first = round(start * SAMPLE_RATE)
    count = round(duration * SAMPLE_RATE)
    for index in range(count):
        progress = index / max(1, count - 1)
        envelope = _sine_window(progress) ** 1.35
        local_t = index / SAMPLE_RATE
        pan_position = direction * (-0.92 + 1.84 * _smoothstep(progress))
        gain_l, gain_r = _pan(pan_position)
        sweep = 31.0 + 22.0 * progress + 8.0 * math.sin(math.pi * progress)
        engine = (
            0.64 * math.sin(TAU * sweep * local_t + phase)
            + 0.22 * math.sin(TAU * (sweep * 2.03) * local_t + phase * 0.7)
            + 0.10 * math.sin(TAU * (147.0 + 29.0 * progress) * local_t)
        )
        turbine = 0.10 * math.sin(
            TAU * (184.0 * local_t + 32.0 * local_t * local_t)
        )
        sample = size * envelope * (engine + turbine)
        frame = first + index
        left[frame] += sample * gain_l
        right[frame] += sample * gain_r


def _add_docking_clamp(
    left: array,
    right: array,
    *,
    start: float,
    pan_position: float,
    strength: float,
) -> None:
    """Mix a muted hull thud followed by short metallic clamp resonances."""
    first = round(start * SAMPLE_RATE)
    count = round(1.55 * SAMPLE_RATE)
    gain_l, gain_r = _pan(pan_position)
    for index in range(count):
        local_t = index / SAMPLE_RATE
        thud = math.exp(-local_t * 9.5) * (
            0.78 * math.sin(TAU * 54.0 * local_t)
            + 0.31 * math.sin(TAU * 87.0 * local_t)
        )
        metal = math.exp(-local_t * 4.3) * (
            0.24 * math.sin(TAU * 173.0 * local_t)
            + 0.16 * math.sin(TAU * 281.0 * local_t + 0.6)
            + 0.09 * math.sin(TAU * 337.0 * local_t + 1.1)
        )
        latch_t = local_t - 0.42
        latch = 0.0
        if latch_t >= 0.0:
            latch = math.exp(-latch_t * 12.0) * (
                0.35 * math.sin(TAU * 72.0 * latch_t)
                + 0.13 * math.sin(TAU * 349.0 * latch_t)
            )
        sample = strength * (thud + metal + latch)
        frame = first + index
        left[frame] += sample * gain_l
        right[frame] += sample * gain_r


def _add_hydraulic_gantry(
    left: array,
    right: array,
    *,
    start: float,
    pan_position: float,
    variant: int,
) -> None:
    """Mix a low actuator cycle: motor, pressure release, and heavy stop."""
    first = round(start * SAMPLE_RATE)
    duration = 3.2 + 0.35 * (variant % 3)
    count = round(duration * SAMPLE_RATE)
    gain_l, gain_r = _pan(pan_position)
    for index in range(count):
        progress = index / max(1, count - 1)
        local_t = index / SAMPLE_RATE
        motor_env = _sine_window(progress) ** 0.65
        motor_frequency = 39.0 + variant * 4.0 + 15.0 * progress
        motor = motor_env * (
            0.62 * math.sin(TAU * motor_frequency * local_t)
            + 0.24 * math.sin(TAU * motor_frequency * 2.02 * local_t)
            + 0.10 * math.sin(TAU * 171.0 * local_t)
        )
        # Several low, inharmonic partials suggest air pressure without a hiss.
        pressure = 0.0
        release_t = local_t - duration * 0.62
        if release_t >= 0.0:
            pressure_env = math.exp(-release_t * 2.7)
            pressure = pressure_env * (
                0.22 * math.sin(TAU * 91.0 * release_t + 0.3)
                + 0.17 * math.sin(TAU * 137.0 * release_t + 1.2)
                + 0.12 * math.sin(TAU * 223.0 * release_t + 2.0)
            )
        stop_t = local_t - duration * 0.84
        stop = 0.0
        if stop_t >= 0.0:
            stop = math.exp(-stop_t * 12.0) * (
                0.58 * math.sin(TAU * 52.0 * stop_t)
                + 0.24 * math.sin(TAU * 109.0 * stop_t)
            )
        sample = 0.050 * (motor + pressure + stop)
        frame = first + index
        left[frame] += sample * gain_l
        right[frame] += sample * gain_r


def _add_hull_creak(
    left: array,
    right: array,
    *,
    start: float,
    duration: float,
    pan_position: float,
    strength: float,
) -> None:
    """Mix a slow stressed-metal groan through the station structure."""
    first = round(start * SAMPLE_RATE)
    count = round(duration * SAMPLE_RATE)
    gain_l, gain_r = _pan(pan_position)
    for index in range(count):
        progress = index / max(1, count - 1)
        local_t = index / SAMPLE_RATE
        envelope = _sine_window(progress) ** 1.6
        sweep = 47.0 + 42.0 * math.sin(math.pi * progress) ** 1.4
        wobble = 3.5 * math.sin(TAU * progress * 2.0)
        groan = (
            0.62 * math.sin(TAU * (sweep + wobble) * local_t)
            + 0.27 * math.sin(TAU * (sweep * 1.73) * local_t + 0.8)
            + 0.13 * math.sin(TAU * (sweep * 2.36) * local_t + 1.6)
        )
        sample = strength * envelope * groan
        frame = first + index
        left[frame] += sample * gain_l
        right[frame] += sample * gain_r


def _add_warp_arrival(
    left: array,
    right: array,
    *,
    start: float,
    pan_position: float,
) -> None:
    """Mix a rare broad swell and compact arrival transient."""
    first = round(start * SAMPLE_RATE)
    count = round(4.8 * SAMPLE_RATE)
    for index in range(count):
        progress = index / max(1, count - 1)
        local_t = index / SAMPLE_RATE
        envelope = (
            _smoothstep(progress / 0.73) ** 2
            if progress < 0.73
            else math.exp(-(progress - 0.73) * 14.0)
        )
        gain_l, gain_r = _pan(
            pan_position + 0.20 * math.sin(TAU * progress)
        )
        sweep = math.sin(TAU * (74.0 * local_t + 112.0 * local_t * local_t))
        sub = math.sin(TAU * (29.0 + 17.0 * progress) * local_t)
        pressure = math.sin(TAU * (218.0 * local_t - 18.0 * local_t * local_t))
        sample = 0.055 * envelope * (
            0.52 * sweep + 0.70 * sub + 0.10 * pressure
        )
        frame = first + index
        left[frame] += sample * gain_l
        right[frame] += sample * gain_r


def synthesize() -> array:
    """Return one long, seamless, low-fatigue stereo station soundscape."""
    rng = random.Random(SEED)
    frame_count = SAMPLE_RATE * DURATION_SECONDS
    left = array("f", [0.0]) * frame_count
    right = array("f", [0.0]) * frame_count

    # Eight related suspended chords form a slow low-frequency tonal arc. Every
    # oscillator is loop-quantized; only its amplitude changes as chords fade.
    chord_frequencies = (
        (36.71, 55.00, 73.42, 87.31),
        (29.14, 43.65, 65.41, 73.42),
        (43.65, 65.41, 87.31, 110.00),
        (32.70, 49.00, 73.42, 82.41),
        (36.71, 55.00, 65.41, 87.31),
        (24.50, 36.71, 58.27, 73.42),
        (29.14, 43.65, 65.41, 82.41),
        (36.71, 55.00, 73.42, 87.31),
    )
    chords = tuple(
        tuple(_loop_frequency(frequency) for frequency in chord)
        for chord in chord_frequencies
    )
    bed_frequencies = tuple(
        _loop_frequency(value) for value in (32.70, 49.00, 65.41, 97.99)
    )
    texture_cycles = (1_411, 2_119, 3_187, 4_729, 6_827, 9_871)
    texture_gains = (0.22, 0.17, 0.13, 0.10, 0.07, 0.045)
    left_phases = tuple(rng.uniform(0.0, TAU) for _ in texture_cycles)
    right_phases = tuple(rng.uniform(0.0, TAU) for _ in texture_cycles)

    for frame in range(frame_count):
        t = frame / SAMPLE_RATE
        phase = frame / frame_count
        position = phase * len(chords)
        segment = min(len(chords) - 1, int(position))
        local = position - segment
        blend = _smoothstep((local - 0.58) / 0.42)
        current = chords[segment]
        following = chords[(segment + 1) % len(chords)]

        slow_breath = 0.83 + 0.17 * math.sin(TAU * phase * 2.0 - 0.4)
        pad_a = sum(
            math.sin(TAU * frequency * t + 0.09 * math.sin(TAU * phase))
            for frequency in current
        ) / len(current)
        pad_b = sum(
            math.sin(TAU * frequency * t - 0.07 * math.sin(TAU * phase * 3.0))
            for frequency in following
        ) / len(following)
        score = slow_breath * ((1.0 - blend) * pad_a + blend * pad_b)

        machinery = (
            0.45 * math.sin(TAU * bed_frequencies[0] * t + 0.13 * math.sin(TAU * phase))
            + 0.23 * math.sin(TAU * bed_frequencies[1] * t - 0.11 * math.sin(TAU * phase * 2.0))
            + 0.12 * math.sin(TAU * bed_frequencies[2] * t)
            + 0.07 * math.sin(TAU * bed_frequencies[3] * t + 0.8)
        )
        texture_left = sum(
            gain * math.sin(TAU * cycles * phase + offset)
            for cycles, gain, offset in zip(
                texture_cycles, texture_gains, left_phases, strict=True
            )
        )
        texture_right = sum(
            gain * math.sin(TAU * cycles * phase + offset)
            for cycles, gain, offset in zip(
                texture_cycles, texture_gains, right_phases, strict=True
            )
        )

        left[frame] = (
            0.054 * score
            + 0.058 * machinery
            + 0.012 * texture_left
        )
        right[frame] = (
            0.054 * score
            + 0.058 * machinery
            + 0.012 * texture_right
        )

    # Sparse activity keeps the environment alive while an empty seam region
    # and deterministic event schedule keep the asset loop-safe/reproducible.
    for start, duration, direction, size in (
        (8.0, 5.8, 1, 0.035),
        (21.5, 7.2, -1, 0.048),
        (39.0, 4.8, 1, 0.030),
        (57.0, 8.4, -1, 0.056),
        (77.0, 5.6, 1, 0.040),
    ):
        _add_flyby(
            left,
            right,
            start=start,
            duration=duration,
            direction=direction,
            size=size,
            phase=rng.uniform(0.0, TAU),
        )
    for start, pan_position, strength in (
        (17.2, 0.66, 0.050),
        (34.7, -0.42, 0.042),
        (52.4, 0.28, 0.054),
        (73.8, -0.68, 0.045),
        (86.1, 0.52, 0.038),
    ):
        _add_docking_clamp(
            left,
            right,
            start=start,
            pan_position=pan_position,
            strength=strength,
        )
    for variant, (start, pan_position) in enumerate(
        (
            (12.4, -0.72),
            (29.0, 0.52),
            (46.8, -0.18),
            (68.5, 0.76),
            (83.0, -0.56),
        )
    ):
        _add_hydraulic_gantry(
            left,
            right,
            start=start,
            pan_position=pan_position,
            variant=variant,
        )
    for start, duration, pan_position, strength in (
        (5.2, 5.8, -0.35, 0.025),
        (31.2, 6.5, 0.58, 0.030),
        (54.4, 7.0, -0.62, 0.032),
        (79.0, 5.2, 0.24, 0.026),
    ):
        _add_hull_creak(
            left,
            right,
            start=start,
            duration=duration,
            pan_position=pan_position,
            strength=strength,
        )
    _add_warp_arrival(left, right, start=25.4, pan_position=0.48)
    _add_warp_arrival(left, right, start=63.0, pan_position=-0.38)

    # Gently join the last 20 ms to the exact first stereo frame.
    fade_frames = max(2, round(SAMPLE_RATE * 0.020))
    first_left, first_right = left[0], right[0]
    for index in range(fade_frames):
        weight = _smoothstep((index + 1) / fade_frames)
        frame = frame_count - fade_frames + index
        left[frame] = left[frame] * (1.0 - weight) + first_left * weight
        right[frame] = right[frame] * (1.0 - weight) + first_right * weight

    output = array("h")
    for sample_left, sample_right in zip(left, right, strict=True):
        output.append(round(math.tanh(sample_left * 1.55) * 32_767))
        output.append(round(math.tanh(sample_right * 1.55) * 32_767))
    return output


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUTPUT), "wb") as stream:
        stream.setnchannels(CHANNELS)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(synthesize().tobytes())
    print(
        f"Generated {OUTPUT} ({DURATION_SECONDS}s, "
        f"{OUTPUT.stat().st_size:,} bytes)"
    )


if __name__ == "__main__":
    main()
