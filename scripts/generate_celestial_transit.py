"""Reproduce the approved, original Celestial Transit music asset.

This is the deterministic tonal synthesizer used for the review candidate that
was approved for the launcher. It uses pitched additive oscillators and fixed
tonal delay taps only: no samples, random/noise sources, or EVE audio.

The canonical output is ``assets/audio/music/celestial_transit.wav``. Run with
``--output`` to render elsewhere for an exact-hash verification.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


def _load_review_generator():
    """Load the retained deterministic implementation beside this script."""
    implementation = Path(__file__).with_name("celestial_transit_synthesis.py")
    spec = importlib.util.spec_from_file_location(
        "celestial_transit_synthesis", implementation
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load synthesis implementation: {implementation}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("assets/audio/music/celestial_transit.wav"),
    )
    args = parser.parse_args()
    synth = _load_review_generator()
    audio, _composition = synth.compose_celestial_transit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    synth.write_wav(args.output, audio)
    print(args.output)


if __name__ == "__main__":
    main()
