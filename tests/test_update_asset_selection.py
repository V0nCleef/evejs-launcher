"""Release source archives must never be offered as launcher updates."""

import pytest

from src.updater import checker


SOURCE = {
    "name": "EveJS-Launcher-Source-1.0.62.zip",
    "browser_download_url": "https://example.invalid/source.zip",
}
BINARY = {
    "name": "EveJS-Launcher-V1.zip",
    "browser_download_url": "https://example.invalid/EveJS-Launcher-V1.zip",
}


@pytest.mark.parametrize("assets", [[SOURCE, BINARY], [BINARY, SOURCE], [BINARY]])
def test_checker_selects_launcher_package_regardless_of_asset_order(qapp, monkeypatch, assets):
    monkeypatch.setattr(checker, "get_current_version", lambda: "1.0.61")
    monkeypatch.setattr(checker, "_load_skipped_versions", set)
    monkeypatch.setattr(checker, "get_latest_release", lambda: {
        "tag_name": "v1.0.62", "assets": assets,
    })
    worker = checker.UpdateChecker()
    updates, failures = [], []
    worker.update_available.connect(lambda *args: updates.append(args))
    worker.check_failed.connect(failures.append)

    worker.run()

    assert updates == [("v1.0.62", "", BINARY["browser_download_url"], "")]
    assert failures == []


@pytest.mark.parametrize("assets", [[SOURCE], [], [{**BINARY, "browser_download_url": ""}]])
def test_checker_reports_missing_launcher_package_without_offering_update(qapp, monkeypatch, assets):
    monkeypatch.setattr(checker, "get_current_version", lambda: "1.0.61")
    monkeypatch.setattr(checker, "_load_skipped_versions", set)
    monkeypatch.setattr(checker, "get_latest_release", lambda: {
        "tag_name": "v1.0.62", "assets": assets,
    })
    worker = checker.UpdateChecker()
    updates, failures = [], []
    worker.update_available.connect(lambda *args: updates.append(args))
    worker.check_failed.connect(failures.append)

    worker.run()

    assert updates == []
    assert len(failures) == 1
    assert "EveJS-Launcher-V1.zip" in failures[0]
