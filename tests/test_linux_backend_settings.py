"""The Linux host must not silently change a selected Compose backend."""
from src import config
from src.core import host, linux_installation
from src.pages.settings_page import SettingsPage


def test_linux_docker_save_uses_preflight_without_native_setup_import(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config, 'CONFIG_FILE', tmp_path / 'config.json')
    monkeypatch.setattr(host, 'IS_LINUX', True)
    def forbidden(*args):
        raise AssertionError('Compose must not import Native Linux Setup settings')
    monkeypatch.setattr(linux_installation, 'imported_settings', forbidden)
    root = tmp_path / 'compose-world'
    root.mkdir()
    (root / 'compose.yaml').write_text('services: {}\n')
    page = SettingsPage()
    page.runtime_backend_combo.setCurrentIndex(page.runtime_backend_combo.findData('docker_compose'))
    page.evejs_root_edit.setText(str(root))
    requests = []
    page.docker_preflight_requested.connect(requests.append)
    page.save_settings()
    assert len(requests) == 1
    assert requests[0].draft.evejs_root == str(root)
    assert not config.CONFIG_FILE.exists()
    page.close()
