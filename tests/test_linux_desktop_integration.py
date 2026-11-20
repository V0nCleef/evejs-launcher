"""Linux desktop and local TLS regressions from Bazzite testing."""
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
from threading import Thread

import pytest

from src.core.linux_client import _ensure_x_authority, local_ca_context
from src.widgets import localized_dialogs

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux desktop and TLS')


def test_qt_file_picker_shows_mounts_and_preserves_save_filename(qapp, tmp_path, monkeypatch):
    mounts = tmp_path / 'mountinfo'
    mounts.write_bytes(
        b'31 24 0:4 / / rw - ext4 /dev/root rw\n'
        b'32 24 8:2 / /mnt rw - ext4 /dev/data rw\n'
        b'33 24 8:3 / /run/media/example/Game\\040Drive rw - ext4 /dev/game rw\n'
        b'34 24 8:3 / /run/media/example/Game\\040Drive rw - ext4 /dev/game rw\n'
        b'35 24 0:5 / /proc rw - proc proc rw\n'
    )
    monkeypatch.setattr(localized_dialogs, '_MOUNTINFO', mounts)
    qt = localized_dialogs.QtFileDialog
    captured = {}

    def cancel(dialog):
        captured['sidebar'] = [url.toLocalFile() for url in dialog.sidebarUrls()]
        captured['filename'] = dialog.selectedFiles()[0]
        captured['filter'] = dialog.selectedNameFilter()
        captured['mode'] = dialog.acceptMode()
        return 0

    monkeypatch.setattr(qt, 'exec', cancel)
    file = tmp_path / 'Mod Examples.zip'
    assert localized_dialogs.LocalizedFileDialog.getSaveFileName(
        directory=str(file), filter='ZIP (*.zip);;All files (*)',
        initialFilter='ZIP (*.zip)', options=qt.Option.DontUseNativeDialog,
    ) == ('', '')
    assert captured['sidebar'] == ['/', str(Path.home()), '/mnt', '/run/media/example/Game Drive']
    assert captured['filename'] == str(file)
    assert captured['filter'] == 'ZIP (*.zip)'
    assert captured['mode'] == qt.AcceptMode.AcceptSave


def test_xauthority_recovers_only_a_matching_local_display(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    host = os.fsencode(socket.gethostname())

    def record(display):
        parts = (host, display, b'MIT-MAGIC-COOKIE-1', b'\x00' * 16)
        return b'\x01\x00' + b''.join(len(part).to_bytes(2, 'big') + part for part in parts)

    active = tmp_path / '.mutter-Xwaylandauth.current'
    active.write_bytes(record(b'4'))
    wrong = tmp_path / 'xauth_wrong'
    wrong.write_bytes(record(b'2'))
    os.utime(active, (1, 1))
    (tmp_path / 'xauth_symlink').symlink_to(active)
    (tmp_path / '.Xauthority').write_bytes(record(b'3'))
    env = {'DISPLAY': ':4.0', 'XDG_RUNTIME_DIR': str(tmp_path)}
    _ensure_x_authority(env)
    assert env['XAUTHORITY'] == str(active)
    inherited = env | {'XAUTHORITY': 'explicit'}
    _ensure_x_authority(inherited)
    assert inherited['XAUTHORITY'] == 'explicit'
    remote = {'DISPLAY': 'localhost:4', 'XDG_RUNTIME_DIR': str(tmp_path)}
    _ensure_x_authority(remote)
    assert 'XAUTHORITY' not in remote
    unmatched = {'DISPLAY': ':9', 'XDG_RUNTIME_DIR': str(tmp_path)}
    _ensure_x_authority(unmatched)
    assert 'XAUTHORITY' not in unmatched
    active.write_bytes(record(b''))
    home_display = {'DISPLAY': ':3', 'XDG_RUNTIME_DIR': str(tmp_path)}
    _ensure_x_authority(home_display)
    assert home_display['XAUTHORITY'] == str(tmp_path / '.Xauthority')
    _ensure_x_authority(unmatched)
    assert unmatched['XAUTHORITY'] == str(active)


def test_local_ca_accepts_legacy_leaf_but_checks_chain_and_hostname(tmp_path):
    openssl = shutil.which('openssl')
    if not openssl:
        pytest.skip('openssl CLI needed for a legacy leaf fixture')

    def generate(*args):
        subprocess.run([openssl, *args], cwd=tmp_path, check=True, capture_output=True)

    for name in ('ca', 'other-ca'):
        generate('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                 '-subj', f'/CN={name}', '-addext', 'basicConstraints=critical,CA:TRUE',
                 '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
                 '-keyout', f'{name}.key', '-out', f'{name}.pem')
    generate('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localhost',
             '-keyout', 'leaf.key', '-out', 'leaf.csr')
    (tmp_path / 'leaf.ext').write_text(
        'basicConstraints=critical,CA:FALSE\nauthorityKeyIdentifier=none\n'
        'subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n'
    )
    generate('x509', '-req', '-in', 'leaf.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key',
             '-CAcreateserial', '-days', '1', '-extfile', 'leaf.ext', '-out', 'leaf.pem')
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(tmp_path / 'leaf.pem', tmp_path / 'leaf.key')

    def handshake(client, hostname):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            listener.settimeout(5)

            def serve():
                try:
                    peer, _ = listener.accept()
                    with peer, server.wrap_socket(peer, server_side=True):
                        pass
                except ssl.SSLError:
                    pass  # Expected when the client rejects a certificate.

            worker = Thread(target=serve)
            worker.start()
            try:
                with socket.create_connection(listener.getsockname(), timeout=5) as peer:
                    with client.wrap_socket(peer, server_hostname=hostname):
                        pass
            finally:
                worker.join(5)
                assert not worker.is_alive()

    strict = ssl.create_default_context(cafile=str(tmp_path / 'ca.pem'))
    strict.verify_flags |= ssl.VERIFY_X509_STRICT
    with pytest.raises(ssl.SSLCertVerificationError, match='Authority Key Identifier'):
        handshake(strict, 'localhost')
    local = local_ca_context(tmp_path / 'ca.pem')
    assert local.check_hostname and local.verify_mode == ssl.CERT_REQUIRED
    handshake(local, 'localhost')
    with pytest.raises(ssl.SSLCertVerificationError, match='Hostname mismatch'):
        handshake(local, 'other-host')
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(local_ca_context(tmp_path / 'other-ca.pem'), 'localhost')
