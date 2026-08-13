from __future__ import annotations

import hashlib
import json
from pathlib import Path
import wave

import pytest

from scripts import generate_lyra_voice_catalog as generator
from scripts import process_lyra_voice_catalog as processor
from src.audio.events import VOICE_LINE_TEXT, VoiceLine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = PROJECT_ROOT / "assets/audio/voice/lyra"
MANIFEST_PATH = CATALOG_ROOT / "manifest.json"
REFERENCE_PATH = (
    PROJECT_ROOT / "scripts/voice_reference/lyra_balanced_lift_approved.mp3"
)
RAW_ROOT = PROJECT_ROOT / "scripts/voice_reference/lyra_cori_high_raw"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_bundled_lyra_catalog_records_balanced_lift_provenance() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    processing = manifest["post_processing"]

    assert manifest["schema_version"] == 2
    assert processing["profile_id"] == processor.PROFILE_ID
    assert processing["display_name"] == "Balanced Lift"
    assert processing["approved_reference"] == {
        "path": "scripts/voice_reference/lyra_balanced_lift_approved.mp3",
        "sha256": processor.REFERENCE_SHA256,
        "format": "48 kHz stereo MP3 at 192 kbps",
        "measured_stereo_lufs": -15.36,
        "measured_stereo_true_peak_dbtp": -4.32,
    }
    assert _sha256(REFERENCE_PATH) == processor.REFERENCE_SHA256
    assert processing["raw_catalog"]["path"] == (
        "scripts/voice_reference/lyra_cori_high_raw"
    )
    assert processing["raw_catalog"]["manifest_sha256"] == _sha256(
        RAW_ROOT / "manifest.json"
    )
    assert processing["output"] == "mono PCM16 WAV at 22050 Hz"
    assert processing["matched_profile_filter_template"] == (
        processor.MATCHED_FILTER_TEMPLATE
    )
    assert processing["matched_profile_target_mono_lufs"] == (
        processor.TARGET_MONO_LUFS
    )
    assert processing["matched_profile_loudness_tolerance_lu"] == (
        processor.LOUDNESS_TOLERANCE_LU
    )
    assert processing["limiter_filter"] == processor.LIMITER_FILTER
    assert processing["reference_extracts"] == {
        line.value: {
            "start_seconds": start,
            "end_seconds": end,
            "tail_pad_seconds": processor.REFERENCE_PAD_SECONDS,
        }
        for line, (start, end) in processor.REFERENCE_SEGMENTS.items()
    }

    clips = manifest["clips"]
    reference_keys = {line.value for line in processor.REFERENCE_SEGMENTS}
    assert {
        key for key, entry in clips.items()
        if entry["processing_method"] == "approved_reference_extract"
    } == reference_keys
    assert {
        key for key, entry in clips.items()
        if entry["processing_method"] == "matched_profile"
    } == {line.value for line in VoiceLine} - reference_keys

    for line in VoiceLine:
        path = CATALOG_ROOT / line.filename
        raw_path = RAW_ROOT / line.filename
        entry = clips[line.value]
        with wave.open(str(path), "rb") as stream:
            assert entry["channels"] == stream.getnchannels() == 1
            assert entry["sample_width_bytes"] == stream.getsampwidth() == 2
            assert entry["sample_rate_hz"] == stream.getframerate() == 22_050
            assert entry["frames"] == stream.getnframes()
        assert entry["text"] == VOICE_LINE_TEXT[line]
        assert entry["filename"] == line.filename
        assert entry["bytes"] == path.stat().st_size
        assert entry["sha256"] == _sha256(path)
        assert entry["raw_source_sha256"] == _sha256(raw_path)
        assert len(entry["sha256"]) == len(entry["raw_source_sha256"]) == 64
        assert entry["sha256"] == entry["sha256"].casefold()
        assert entry["raw_source_sha256"] == (
            entry["raw_source_sha256"].casefold()
        )


def test_lyra_build_packages_only_release_wavs_and_manifest() -> None:
    build_text = (PROJECT_ROOT / "build.spec").read_text(encoding="utf-8")

    assert "assets/audio/voice/lyra/*.wav" in build_text
    assert "assets/audio/voice/lyra/manifest.json" in build_text
    assert "scripts/voice_reference" not in build_text
    assert "lyra_balanced_lift_approved.mp3" not in build_text


def test_processor_rejects_a_changed_approved_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changed_reference = tmp_path / "changed.mp3"
    changed_reference.write_bytes(b"not the approved review master")
    monkeypatch.setattr(
        "sys.argv",
        [
            "process_lyra_voice_catalog.py",
            "--input",
            str(RAW_ROOT),
            "--reference",
            str(changed_reference),
            "--output",
            str(tmp_path / "output"),
        ],
    )

    with pytest.raises(SystemExit, match="reference hash mismatch"):
        processor.main()


def test_generator_refuses_to_relabel_preserved_recordings(
    tmp_path: Path,
) -> None:
    model = tmp_path / "model.onnx"
    config = tmp_path / "model.onnx.json"
    model.write_bytes(b"current model")
    config.write_bytes(b"current config")
    prior_manifest = {
        "schema_version": 1,
        "catalog": "LYRA fixed operational voice lines",
        "voice": "Piper en_GB-cori-high",
        "voice_language": "en_GB",
        "generation_runtime": "piper-tts 1.3.0",
        "model_sha256": "0" * 64,
        "config_sha256": _sha256(config),
        "synthesis": generator.SYNTHESIS_SETTINGS,
        "clips": {},
    }

    with pytest.raises(SystemExit, match="different provenance: model_sha256"):
        generator._validate_preserved_provenance(
            prior_manifest,
            model=model,
            config=config,
            piper_version="1.3.0",
        )


def test_processor_rejects_a_processed_catalog_as_raw_input(
    tmp_path: Path,
) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    fake_raw = tmp_path / "not-raw"
    fake_raw.mkdir()
    (fake_raw / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="unexpected identity: schema_version"):
        processor._load_source_manifest(fake_raw)
