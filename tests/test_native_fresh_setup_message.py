"""Fresh 0.12.9 roots keep Native gates strict and explain first setup."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.discovery import validate_evejs_root


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_root(root: Path, *, version: str) -> None:
    _write(root / "package.json", json.dumps({"name": "fixture", "version": version}))
    _write(
        root / "server/package.json",
        json.dumps({"name": "eve.js", "version": version}),
    )
    _write(root / "server/index.js", "// disposable fixture\n")
    _write(root / "SetupEveJS.bat", "@echo off\n")
    _write(root / "server/src/config/schema/index.js", "module.exports = [];\n")
    manager_version = version
    _write(
        root / "server/src/config/manager.js",
        f'const EVEJS_VERSION = "{manager_version}";\n'
        "module.exports = { EVEJS_VERSION };\n",
    )


def _assert_setup_hint(diagnostic: str, root: Path) -> None:
    setup_command = f'"{root / "SetupEveJS.bat"}" -Mode native'
    assert setup_command in diagnostic
    assert "selected EveJS root" in diagnostic
    assert "select this root again" in diagnostic


def test_fresh_0129_root_stays_blocked_until_all_native_setup_outputs_exist(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fresh-beta"
    _write_root(root, version="0.12.9")

    valid, diagnostic = validate_evejs_root(str(root))

    assert not valid
    assert "Missing SSL cert" in diagnostic
    _assert_setup_hint(diagnostic, root)

    _write(root / "server/certs/xmpp-ca-cert.pem", "fixture certificate\n")
    valid, diagnostic = validate_evejs_root(str(root))

    assert not valid
    assert "Missing Client config script" in diagnostic
    _assert_setup_hint(diagnostic, root)

    _write(
        root / "tools/ClientSETUP/scripts/EvEJSConfig.bat",
        "@echo off\n",
    )
    valid, diagnostic = validate_evejs_root(str(root))

    assert not valid
    assert "Missing game store" in diagnostic
    _assert_setup_hint(diagnostic, root)

    game_store = root / "_local/gameStore"
    _write(game_store / "manifest.json", '{"version":1}\n')
    (game_store / "data").mkdir(parents=True)
    valid, diagnostic = validate_evejs_root(str(root))

    assert not valid
    assert "Missing game store" in diagnostic
    _assert_setup_hint(diagnostic, root)

    _write(game_store / "data/static.json", "{}\n")
    assert validate_evejs_root(str(root)) == (True, "")


def test_older_root_keeps_existing_diagnostic_without_0129_setup_hint(
    tmp_path: Path,
) -> None:
    root = tmp_path / "older-root"
    _write_root(root, version="0.12.8")

    valid, diagnostic = validate_evejs_root(str(root))

    assert not valid
    assert diagnostic == (
        "Missing SSL cert (server may not be configured): "
        "server/certs/xmpp-ca-cert.pem"
    )
    assert "SetupEveJS.bat" not in diagnostic
