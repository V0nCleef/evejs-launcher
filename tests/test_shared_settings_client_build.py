"""Exact 24.02 settings-module gate regressions."""

import hashlib
import zipfile

import pytest

from src.core import shared_client_settings as shared


def test_3724492_settings_requires_each_reviewed_dependency(tmp_path, monkeypatch):
    modules = {key: f"fixture {key}".encode() for key in shared._STOCK_SETTINGS_ENTRIES}
    expected = {key: hashlib.sha256(data).hexdigest() for key, data in modules.items()}
    monkeypatch.setattr(shared, "_STOCK_SETTINGS_ENTRIES", expected)
    (tmp_path / "start.ini").write_text("build=3724492\n", encoding="utf-8")
    archive_path = tmp_path / "code.ccp"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for key, data in modules.items():
            archive.writestr(key, data)
    shared.require_shared_settings_support(tmp_path)

    changed = {**modules, "carbonui/util/settings.pyj": b"unexpected settings loader"}
    with zipfile.ZipFile(archive_path, "w") as archive:
        for key, data in changed.items():
            archive.writestr(key, data)
    with pytest.raises(shared.SharedSettingsError, match="not supported"):
        shared.require_shared_settings_support(tmp_path)


def test_old_settings_fingerprint_does_not_allow_new_build(tmp_path, monkeypatch):
    original_prefs = b"reviewed legacy settings"
    monkeypatch.setattr(shared, "_EVE_PREFS_SHA256", hashlib.sha256(original_prefs).hexdigest())
    with zipfile.ZipFile(tmp_path / "code.ccp", "w") as archive:
        archive.writestr("eveprefs/__init__.pyj", original_prefs)
    ini = tmp_path / "start.ini"
    ini.write_text("build=3396210\n", encoding="utf-8")
    shared.require_shared_settings_support(tmp_path)
    ini.write_text("build=3724492\n", encoding="utf-8")
    with pytest.raises(shared.SharedSettingsError, match="not supported"):
        shared.require_shared_settings_support(tmp_path)
