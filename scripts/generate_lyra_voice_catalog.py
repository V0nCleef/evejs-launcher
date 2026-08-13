"""Generate the raw fixed LYRA source catalog from a local Piper voice.

This script never downloads a model. Pass paths to an already reviewed Piper
runtime and `en_GB-cori-high` model/config. Only the finite lines declared in
`src.audio.events` can be generated. The neural runtime and model remain
build-time inputs and are not bundled with the launcher.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import wave

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.audio.events import VOICE_LINE_TEXT, VoiceLine


DEFAULT_OUTPUT = REPOSITORY_ROOT / "scripts/voice_reference/lyra_cori_high_raw"
MANIFEST_NAME = "manifest.json"
SYNTHESIS_SETTINGS = {
    "provider": "CPUExecutionProvider",
    "noise_scale": 0.667,
    "length_scale": 1.0,
    "noise_w_scale": 0.8,
    "normalize_audio": True,
    "volume": 1.0,
    "output": "mono PCM16 WAV at model-native 22050 Hz",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the raw LYRA Cori High source catalog locally."
    )
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--preserve-existing",
        action="store_true",
        help=(
            "Keep already approved WAV files in the output directory and "
            "synthesize only missing catalog lines. Piper models include "
            "stochastic inference operators, so this is required when "
            "extending a reviewed catalog without changing its recordings."
        ),
    )
    return parser


def _clip_record(path: Path, line: VoiceLine) -> dict[str, object]:
    """Describe one generated or preserved mono PCM16 catalog clip."""
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.getnframes()
    if channels != 1 or sample_width != 2 or sample_rate != 22_050 or frames <= 0:
        raise SystemExit(
            f"Invalid existing catalog clip {path}: expected non-empty mono "
            "PCM16 WAV at 22050 Hz"
        )
    return {
        "text": VOICE_LINE_TEXT[line],
        "filename": line.filename,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "sample_rate_hz": sample_rate,
        "frames": frames,
    }


def _approved_existing_manifest(output: Path) -> dict[str, object]:
    """Load the prior manifest used to authorize preserved recordings."""
    manifest_path = output / MANIFEST_NAME
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot preserve clips from invalid {manifest_path}: {exc}")
    if not isinstance(payload, dict):
        raise SystemExit(f"Cannot preserve clips: invalid object in {manifest_path}")
    clips = payload.get("clips")
    if not isinstance(clips, dict):
        raise SystemExit(f"Cannot preserve clips: missing clip map in {manifest_path}")
    return payload


def _validate_preserved_provenance(
    prior_manifest: dict[str, object],
    *,
    model: Path,
    config: Path,
    piper_version: str,
) -> dict[str, object]:
    """Reject relabeling recordings made with a different synthesis input."""
    if not prior_manifest:
        return {}
    expected = {
        "schema_version": 1,
        "catalog": "LYRA fixed operational voice lines",
        "voice": "Piper en_GB-cori-high",
        "voice_language": "en_GB",
        "generation_runtime": f"piper-tts {piper_version}",
        "model_sha256": _sha256(model),
        "config_sha256": _sha256(config),
        "synthesis": SYNTHESIS_SETTINGS,
    }
    mismatched = [
        key for key, value in expected.items()
        if prior_manifest.get(key) != value
    ]
    if mismatched:
        raise SystemExit(
            "Refusing to preserve recordings with different provenance: "
            + ", ".join(mismatched)
        )
    clips = prior_manifest.get("clips")
    assert isinstance(clips, dict)
    return clips


def _validate_preserved_clip(
    destination: Path,
    line: VoiceLine,
    prior_clips: dict[str, object],
) -> bool:
    """Return true only for a file still matching its reviewed manifest entry."""
    entry = prior_clips.get(line.value)
    if not destination.is_file() or not isinstance(entry, dict):
        return False
    expected = {
        "text": VOICE_LINE_TEXT[line],
        "filename": line.filename,
        "sha256": _sha256(destination),
        "bytes": destination.stat().st_size,
    }
    if any(entry.get(key) != value for key, value in expected.items()):
        raise SystemExit(
            f"Refusing to preserve changed or stale catalog clip: {destination}"
        )
    _clip_record(destination, line)
    return True


def main() -> None:
    args = _parser().parse_args()
    runtime = args.runtime.resolve()
    model = args.model.resolve()
    config = args.config.resolve()
    output = args.output.resolve()
    for path in (runtime, model, config):
        if not path.exists():
            raise SystemExit(f"Missing required local input: {path}")

    # Isolate the build-only dependency from the launcher's runtime imports.
    sys.path.insert(0, str(runtime))
    from piper import PiperVoice  # type: ignore[import-not-found]  # noqa: PLC0415

    try:
        piper_version = importlib.metadata.version("piper-tts")
    except importlib.metadata.PackageNotFoundError:
        piper_version = "unknown"

    prior_manifest = (
        _approved_existing_manifest(output) if args.preserve_existing else {}
    )
    prior_clips = _validate_preserved_provenance(
        prior_manifest,
        model=model,
        config=config,
        piper_version=piper_version,
    )

    voice = PiperVoice.load(model, config_path=config, use_cuda=False)
    output.mkdir(parents=True, exist_ok=True)
    clips: dict[str, dict[str, object]] = {}
    generated_count = 0
    preserved_count = 0

    for line in VoiceLine:
        destination = output / line.filename
        if args.preserve_existing and _validate_preserved_clip(
            destination,
            line,
            prior_clips,
        ):
            preserved_count += 1
        else:
            with wave.open(str(destination), "wb") as wav_file:
                # Defaults come from the reviewed model config: noise_scale
                # 0.667, length_scale 1.0, noise_w_scale 0.8, normalized mono
                # PCM16. The model contains stochastic operators; catalog
                # recordings become immutable once approved and manifested.
                voice.synthesize_wav(VOICE_LINE_TEXT[line], wav_file)
            generated_count += 1
        clips[line.value] = _clip_record(destination, line)

    manifest = {
        "schema_version": 1,
        "catalog": "LYRA fixed operational voice lines",
        "voice": "Piper en_GB-cori-high",
        "voice_language": "en_GB",
        "generation_runtime": f"piper-tts {piper_version}",
        "model_source": (
            "https://huggingface.co/rhasspy/piper-voices/tree/"
            "b15880f5cbc33fcfc97938b1f72411dc770e5bc4/en/en_GB/cori/high"
        ),
        "model_sha256": _sha256(model),
        "config_sha256": _sha256(config),
        "synthesis": SYNTHESIS_SETTINGS,
        "licensing": {
            "generator": "Piper GPL-3.0-or-later (build-time only)",
            "voice_dataset": "Public-domain LibriVox recordings",
            "model_card": (
                "https://huggingface.co/rhasspy/piper-voices/raw/main/"
                "en/en_GB/cori/high/MODEL_CARD"
            ),
        },
        "clips": clips,
    }
    manifest_path = output / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {len(clips)} raw fixed LYRA clips in {output}")
    print(
        f"Catalog update: {generated_count} synthesized, "
        f"{preserved_count} preserved"
    )
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
