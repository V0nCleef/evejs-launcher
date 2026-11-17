"""Interoperability, isolation and fail-closed checks for Linux stage 1."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

import pytest

from src.core.linux_installation import (LinuxInstallation, LinuxSetupError, _assignment_value,
                                         read_setup_config, file_sha256)
from src.core.linux_client import prepare_account_settings, proton_environment, settings_base, wine_path


@pytest.fixture
def linux_installation(tmp_path):
    root = tmp_path / "prepared world"
    client = tmp_path / "copied client" / "tq"
    cache = client.parent
    prefix = tmp_path / "existing prefix"
    steam = tmp_path / "Steam"
    node = tmp_path / "node"
    proton = tmp_path / "Proton" / "proton"
    for path in (root / "config", root / "server", client / "bin64", cache / "ResFiles", prefix, steam, proton.parent):
        path.mkdir(parents=True, exist_ok=True)
    for path in (client / "bin64/exefile.exe", root / "server/index.js", cache / "index_tranquility.txt", node, proton):
        path.write_text("prepared")
        path.chmod(0o755)
    (client / "start.ini").write_text("[main]\nbuild=3396210\nserver=127.0.0.1\ncryptoPack=Placebo\n")
    (root / "config/version.json").write_text(json.dumps({"evejsVersion": "261108.1", "configSchemaVersion": 1}))
    (root / "config/server.json").write_text(json.dumps({"network": {"serverPort": 26000}}))
    config = {"EVEJS_REPO": root, "EVEJS_VERSION": "261108.1", "SUPPORTED_BUILD": "3396210", "TQ_PATH": client,
              "CACHE_ROOT": cache, "PROTON_BIN": proton, "PROTON_PREFIX": prefix, "STEAM_ROOT": steam,
              "NODE_BIN": node, "EVEJS_CDN_PORT": "443", "EVEJS_PLATFORM_MODE": "standard-linux"}
    (root / ".evejs-linux.conf").write_text("\n".join(f"{key}={shlex.quote(str(value))}" for key, value in config.items()))
    return LinuxInstallation.load(root)


@pytest.mark.parametrize("raw,expected", [
    (r"/home/example/copied\ client/tq", "/home/example/copied client/tq"),
    ("'/home/example/雪 client/tq'", "/home/example/雪 client/tq"),
    (r"$'/home/example/\u96ea'", "/home/example/雪"),
    (r"$'/home/example/\303\251'", "/home/example/é"),
    (r"a\$\(literal\)", "a$(literal)"),
    ("''", ""),
])
def test_setup_printf_quoting_is_decoded_as_data(raw, expected):
    assert _assignment_value(raw) == expected


@pytest.mark.parametrize("raw", ["$(touch /tmp/surprise)", "`id`", "x;id", "a b", '"$HOME"', r"$'bad\npath'", "'unterminated", "x|cat"])
def test_setup_shell_commands_and_control_characters_are_rejected(raw):
    with pytest.raises(LinuxSetupError):
        _assignment_value(raw)


def test_duplicate_setup_assignment_is_rejected(tmp_path):
    path = tmp_path / "config"
    path.write_text("TQ_PATH=/a\nTQ_PATH=/b\n")
    with pytest.raises(LinuxSetupError, match="duplicate"):
        read_setup_config(path)


def test_import_preserves_existing_installation(linux_installation):
    conf = linux_installation.root / ".evejs-linux.conf"
    before = conf.read_bytes()
    assert LinuxInstallation.load(linux_installation.root) == linux_installation
    assert conf.read_bytes() == before


def test_import_refuses_mismatched_root(linux_installation):
    conf = linux_installation.root / ".evejs-linux.conf"
    lines = conf.read_text().splitlines()
    conf.write_text("\n".join("EVEJS_REPO=/elsewhere" if line.startswith("EVEJS_REPO=") else line for line in lines))
    with pytest.raises((LinuxSetupError, OSError)):
        LinuxInstallation.load(linux_installation.root)


def test_steam_environment_has_prefix_and_wine_visible_paths(linux_installation):
    # Path spelling matters here; this test runs on the actual Linux host.
    if sys.platform != "linux":
        pytest.skip("Wine Z mapping is a Linux host contract")
    env = proton_environment(linux_installation, {"WINEPREFIX": "/wrong", "SSL_CERT_DIR": "/wrong"})
    assert env["STEAM_COMPAT_DATA_PATH"] == str(linux_installation.prefix)
    assert env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] == str(linux_installation.steam)
    assert env["SSL_CERT_FILE"].startswith("Z:\\")
    assert env["EO_REMOTEFILECACHEFOLDER"] == wine_path(linux_installation.cache / "ResFiles")
    assert "WINEPREFIX" not in env and "SSL_CERT_DIR" not in env


def test_accounts_get_separate_named_settings_without_copying_private_caches(linux_installation, monkeypatch):
    import src.core.shared_client_settings as shared
    monkeypatch.setattr(shared, "require_shared_settings_support", lambda _client: None)
    base = settings_base(linux_installation)
    default = base / "settings"
    default.mkdir(parents=True)
    (default / "prefs.ini").write_text("newbie=0\ncustom_graphics=preserved\n")
    (default / "core_user__.dat").write_bytes(b"private account cache")
    a, option_a = prepare_account_settings(linux_installation, "account-a")
    b, option_b = prepare_account_settings(linux_installation, "account-b")
    assert a != b and option_a != option_b
    assert a.parent == base and b.parent == base
    assert "custom_graphics=preserved" in (a / "prefs.ini").read_text()
    assert not (a / "core_user__.dat").exists()
    assert (default / "core_user__.dat").read_bytes() == b"private account cache"
    (a / "prefs.ini").write_text("my_own_setting=1\n")
    assert prepare_account_settings(linux_installation, "account-a")[0] == a
    assert "my_own_setting=1" in (a / "prefs.ini").read_text()


def test_market_source_and_binary_are_bound_to_canonical_install(linux_installation):
    installation = linux_installation
    market = installation.root / "externalservices/market-server"
    (market / "src").mkdir(parents=True)
    source = market / "src/main.rs"
    source.write_text("fn main() {}\n")
    records = f"src/main.rs {file_sha256(source).upper()}"
    digest = hashlib.sha256(records.encode()).hexdigest().upper()
    identity = hashlib.sha256(str(installation.root).encode()).hexdigest()[:16]
    cache = installation.root.parent / ".evejs-linux-runtime" / identity / "market-builds" / digest.lower()
    cache.mkdir(parents=True)
    binary = cache / "market-server"
    binary.write_text("verified binary")
    binary.chmod(0o755)
    metadata = {"schema": "evejs-linux-market-runtime/1", "repo": str(installation.root), "evejsVersion": installation.version,
                "mode": "stock", "marketProfile": f"stock-v{installation.version}-market", "sourceDigest": digest,
                "binary": str(binary), "binarySha256": file_sha256(binary)}
    (cache / "build.json").write_text(json.dumps(metadata))
    assert installation.verified_market_binary() == binary
    binary.write_text("changed binary")
    with pytest.raises(LinuxSetupError, match="verified"):
        installation.verified_market_binary()
    source.write_text("fn main() { panic!(); }\n")
    with pytest.raises(LinuxSetupError):
        installation.verified_market_binary()


def test_windows_platform_api_keeps_original_functions():
    if sys.platform != "win32":
        pytest.skip("Windows preservation")
    from src.core import platform, platform_win
    for name in platform.__all__:
        assert getattr(platform, name) is getattr(platform_win, name)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux profile attribution')
def test_linux_profile_binding_resolves_after_context_is_released(linux_installation, tmp_path):
    from src.core.linux_profiles import bind_installation, enroll_profile, account_settings_path
    from src.core.profiles import create_profile
    with bind_installation(linux_installation):
        profile = create_profile('account-a', str(linux_installation.client), tmp_path / 'Profiles', bootstrap_settings=False)
        enroll_profile(profile, linux_installation, 'account-a')
        settings = account_settings_path(profile / 'tq')
    assert account_settings_path(profile / 'tq') == settings
    assert settings.parent == settings_base(linux_installation)
    assert settings.name.startswith('settings_EveJSLauncher_')


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux shared profile alias')
def test_linux_named_shared_profile_uses_real_client_key_and_safe_link(linux_installation, tmp_path):
    from src.core.linux_profiles import bind_installation
    from src.core.shared_client_settings import _create_junction, _remove_junction, shared_settings_alias
    target = tmp_path / 'shared-data'
    target.mkdir()
    (target / 'keep.dat').write_bytes(b'keep')
    with bind_installation(linux_installation):
        alias, host = shared_settings_alias(tmp_path / 'Profiles/account-a/tq')
        assert alias.parent == settings_base(linux_installation)
        alias.parent.mkdir(parents=True)
        _create_junction(target, alias)
        assert alias.is_symlink() and (alias / 'keep.dat').read_bytes() == b'keep'
        _remove_junction(alias)
    assert (target / 'keep.dat').read_bytes() == b'keep'
