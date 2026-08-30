from pathlib import Path

import pytest

from build_support import (
    is_forbidden_build_path,
    reject_contaminated_binaries,
    sanitize_build_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_sanitize_build_path_removes_foreign_dll_toolchains() -> None:
    clean, removed = sanitize_build_path(
        ";".join(
            (
                r"C:\Python311",
                r"C:\Users\Test\.cache\codex-runtimes\runtime\native\poppler\Library\bin",
                r"C:/Users/Test/.cache/codex-runtimes/runtime/native/libheif/bin",
                r"C:\Program Files\Amazon Corretto\jdk25\bin",
                r"C:\Windows\System32",
            )
        ),
        separator=";",
    )

    assert clean == r"C:\Python311;C:\Windows\System32"
    assert len(removed) == 3
    assert all(is_forbidden_build_path(entry) for entry in removed)


def test_reject_contaminated_binaries_fails_closed() -> None:
    binaries = (
        ("PyQt6/Qt6/bin/Qt6Gui.dll", r"C:\Python311\Qt6Gui.dll", "BINARY"),
        (
            "icuuc.dll",
            r"C:\Users\Test\.cache\codex-runtimes\runtime\native\poppler\Library\bin\icuuc.dll",
            "BINARY",
        ),
    )

    with pytest.raises(RuntimeError, match=r"icuuc\.dll"):
        reject_contaminated_binaries(binaries)


def test_reject_contaminated_binaries_accepts_application_dependencies() -> None:
    reject_contaminated_binaries(
        (
            ("Qt6Gui.dll", r"C:\Python311\site-packages\PyQt6\Qt6\bin\Qt6Gui.dll"),
            ("python311.dll", r"C:\Python311\python311.dll"),
        )
    )


def test_build_spec_applies_guard_before_and_after_analysis() -> None:
    spec = (PROJECT_ROOT / "build.spec").read_text(encoding="utf-8")

    sanitize_at = spec.index("sanitize_build_path(")
    analysis_at = spec.index("a = Analysis(")
    reject_at = spec.index("reject_contaminated_binaries(a.binaries)")

    assert sanitize_at < analysis_at < reject_at
