"""Unicode and legacy-code-page client discovery regressions."""
from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from src.core import discovery


def _client_fixture(root: Path) -> tuple[Path, Path]:
    tq = root / "SharedCache" / "tq"
    executable = tq / "bin64" / "exefile.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    (tq / "start.ini").write_text("build=3396210\n", encoding="utf-8")
    return tq, executable


@pytest.mark.parametrize(
    ("folder_name", "encoding", "legacy_encoding"),
    [
        ("用户", "utf-8", "ascii"),
        ("用户", "utf-8-sig", "ascii"),
        ("用户", "utf-16", "ascii"),
        ("ユーザー", "cp932", "cp932"),
        ("用户", "gbk", "gbk"),
    ],
)
def test_find_client_path_decodes_windows_batch_encodings_without_losing_unicode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    folder_name: str,
    encoding: str,
    legacy_encoding: str,
) -> None:
    tq, executable = _client_fixture(tmp_path / folder_name)
    evejs_root = tmp_path / f"EveJS-{folder_name}"
    config_path = (
        evejs_root / "tools" / "ClientSETUP" / "scripts" / "EvEJSConfig.bat"
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(
        f'set "EVEJS_CLIENT_PATH={executable}"\r\n'.encode(encoding)
    )
    monkeypatch.setattr(
        discovery,
        "_legacy_batch_encoding",
        lambda: legacy_encoding,
    )

    assert discovery.find_client_path(str(evejs_root)) == str(tq)


def test_find_client_path_rejects_undecodable_batch_instead_of_dropping_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evejs_root = tmp_path / "EveJS"
    config_path = (
        evejs_root / "tools" / "ClientSETUP" / "scripts" / "EvEJSConfig.bat"
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(b'\xffset "EVEJS_CLIENT_PATH=C:\\near-match"\r\n')
    monkeypatch.setattr(discovery, "_legacy_batch_encoding", lambda: "ascii")

    assert discovery.find_client_path(str(evejs_root)) is None


@pytest.mark.parametrize(
    "malformed",
    [
        codecs.BOM_UTF16_LE + b"\xff",
        codecs.BOM_UTF16_BE + b"\xff",
        codecs.BOM_UTF8 + b"\xff",
    ],
)
def test_find_client_path_never_reinterprets_a_malformed_bom_as_ansi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed: bytes,
) -> None:
    evejs_root = tmp_path / "EveJS"
    config_path = (
        evejs_root / "tools" / "ClientSETUP" / "scripts" / "EvEJSConfig.bat"
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(
        malformed + b'set "EVEJS_CLIENT_PATH=C:\\false-match"\r\n'
    )
    monkeypatch.setattr(discovery, "_legacy_batch_encoding", lambda: "latin-1")

    assert discovery.find_client_path(str(evejs_root)) is None
