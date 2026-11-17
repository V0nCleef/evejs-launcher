"""Exercise frozen Linux update success and failed-start rollback in disposable installs."""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.updater.linux_package import PACKAGE_NAME, EXECUTABLE, extract_linux_package


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='evejs-packaged-update-') as temporary:
        root = Path(temporary)
        damaged = root / 'failed-start.tar.gz'
        removed = []
        with tarfile.open(args.archive, 'r:gz') as original, tarfile.open(damaged, 'w:gz') as result:
            for entry in original:
                if entry.name.endswith('/platforms/libqoffscreen.so'):
                    removed.append(entry.name)
                    continue
                result.addfile(entry, original.extractfile(entry) if entry.isreg() else None)
        if len(removed) != 1:
            raise ValueError('Could not construct the exact missing-platform-plugin failure fixture.')
        assets = {'success': args.archive, 'rollback': damaged}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *unused): pass
            def do_GET(self):
                case, _, name = self.path.lstrip('/').partition('/')
                if case not in assets or name not in (PACKAGE_NAME, PACKAGE_NAME + '.sha256'):
                    self.send_error(404); return
                archive = assets[case]
                if name.endswith('.sha256'):
                    with archive.open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    content = (digest + '  ' + PACKAGE_NAME + '\n').encode()
                    self.send_response(200); self.send_header('Content-Length', str(len(content))); self.end_headers()
                    self.wfile.write(content)
                else:
                    self.send_response(200); self.send_header('Content-Length', str(archive.stat().st_size)); self.end_headers()
                    with archive.open('rb') as stream:
                        shutil.copyfileobj(stream, self.wfile)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for case in ('success', 'rollback'):
                installation = extract_linux_package(args.archive, root / (case + '-install')).directory
                state = root / (case + '-state')
                command = [str(installation / EXECUTABLE), '--linux-update-check', str(state),
                    '--linux-update-package', f'http://127.0.0.1:{server.server_port}/{case}/{PACKAGE_NAME}']
                if case == 'rollback': command.append('--linux-update-expect-rollback')
                with (root / (case + '.log')).open('wb') as stream:
                    process = subprocess.Popen(command, env=dict(os.environ, QT_QPA_PLATFORM='offscreen', PYINSTALLER_RESET_ENVIRONMENT='1'),
                        cwd=installation, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 130
                data = {}
                while time.monotonic() < deadline:
                    report = state / 'check.json'
                    if report.is_file():
                        data = json.loads(report.read_bytes())
                        if data.get('phase') in ('passed', 'failed'):
                            break
                    elif process.poll() is not None:
                        break
                    time.sleep(.1)
                process.wait(timeout=5)
                if data.get('phase') != 'passed':
                    data.update(phase='failed', originalExit=process.returncode)
                    data['consoleTail'] = (root / (case + '.log')).read_text(errors='replace')[-6000:]
                    for result in state.glob('cache/evejs-launcher/updates/transaction-*/result.json'):
                        data['transactionResult'] = json.loads(result.read_bytes())
                        log = result.parent / 'startup.log'
                        if log.exists(): data['candidateConsoleTail'] = log.read_text(errors='replace')[-3000:]
                target = args.output / ('linux-packaged-update-' + case + '.json')
                target.write_text(json.dumps(data, indent=2) + '\n')
                print(json.dumps({'case': case, 'phase': data.get('phase'), 'report': str(target)}), flush=True)
                if data.get('phase') != 'passed':
                    raise RuntimeError('Packaged Linux update ' + case + ' did not pass: ' + json.dumps(data))
                # The accepted build closes itself after checking exact stop control.
                time.sleep(.5)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
