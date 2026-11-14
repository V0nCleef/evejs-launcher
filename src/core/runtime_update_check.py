"""Explicit disposable packaged-update check; never used by normal launches.

The caller supplies a new absolute evidence directory and loopback package URL.
All accounts, servers, logs and configuration are synthetic. The inherited
diagnostic marker lets the installed build verify the *normal* restart path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit


_ENV = "EVEJS_PRIVATE_UPDATE_CHECK"
_NODE = r"""
const fs = require('fs'), http = require('http');
const [role, ready, ports] = process.argv.slice(2);
const servers = ports.split(',').map(p => http.createServer((req,res) => {
  res.end(JSON.stringify({pid: process.pid, role}));
}));
let pending = servers.length;
servers.forEach((s,i) => s.listen(Number(ports.split(',')[i]), process.platform === 'win32' ? '127.0.0.1' : '0.0.0.0', () => {
  if (--pending === 0) fs.writeFileSync(ready, JSON.stringify({pid:process.pid,ports:servers.map(s=>s.address().port)}));
}));
const timer = setInterval(() => console.log('heartbeat ' + role + ' ' + process.pid), 100);
process.on('SIGBREAK', () => { console.log('graceful-game-stop'); clearInterval(timer); servers.forEach(s=>s.close()); });
setTimeout(() => process.exit(124), 150000).unref();
"""


class RuntimeUpdateCheck:
    @classmethod
    def from_argv(cls, argv):
        if "--apply-update" in argv:
            return None  # the independent updater follows its ordinary route
        if "--runtime-update-check" in argv:
            parser = argparse.ArgumentParser(add_help=False)
            parser.add_argument("--runtime-update-check", type=Path, required=True)
            parser.add_argument("--runtime-update-url", required=True)
            parser.add_argument("--runtime-update-case", choices=("native", "external", "docker_managed", "docker_connect"), default="native")
            parser.add_argument("--runtime-update-visible", action="store_true")
            args = parser.parse_args(argv)
            return cls(args.runtime_update_check, args)
        return cls(Path(os.environ[_ENV]), None) if _ENV in os.environ else None

    def __init__(self, root, args):
        self.root, self.args = root, args
        self.started = time.monotonic()
        self.children = []

    def prepare(self):
        if not getattr(sys, "frozen", False) or not self.root.is_absolute():
            raise ValueError("Packaged check requires an absolute private directory")
        for path in (self.root, *self.root.parents):
            if path.exists() and (path.is_symlink() or path.is_junction()):
                raise ValueError("Private check directory cannot be redirected")
        if self.args is not None:
            url = urlsplit(self.args.runtime_update_url)
            if url.scheme != "http" or url.hostname != "127.0.0.1":
                raise ValueError("Private check packages must use loopback HTTP")
            self.root.mkdir()  # never replace an existing state or config
            for name in ("appdata", "localappdata", "temp", "world/server"):
                (self.root / name).mkdir(parents=True)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            self.data = {"protocol": "evejs_private_runtime_update_v1", "phase": "prepared",
                         "case": self.args.runtime_update_case, "visible": self.args.runtime_update_visible,
                         "executable": str(Path(sys.executable).resolve()), "url": self.args.runtime_update_url,
                         "game_port": port, "desktopControlUsed": False}
            self._write()
            (self.root / "world/server/index.js").write_text("// disposable update fixture\n", encoding="utf-8")
            (self.root / "server.js").write_text(_NODE, encoding="utf-8")
            cfg = {"evejs_root": str(self.root / "world"), "client_path": "", "runtime_backend": "native",
                   "game_port": port, "auto_start_server": False, "auto_start_market": False,
                   "update_auto_check": False, "update_check_interval_hours": 0,
                   "audio_master_muted": True, "audio_music_enabled": False, "audio_voice_enabled": False,
                   "animations_enabled": False, "language": "en"}
            if self.data["case"].startswith("docker_"):
                self._compose(cfg)
            folder = self.root / "appdata/EveJS-Launcher"
            folder.mkdir()
            (folder / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        else:
            self.data = json.loads((self.root / "check.json").read_text(encoding="utf-8"))
            if (self.data.get("protocol") != "evejs_private_runtime_update_v1"
                    or self.data.get("phase") != "awaiting_restart"
                    or self.data["executable"] != str(Path(sys.executable).resolve())
                    or os.environ.get("APPDATA") != str(self.root / "appdata")):
                raise ValueError("Private update check restart identity changed")
        os.environ.update(APPDATA=str(self.root / "appdata"), LOCALAPPDATA=str(self.root / "localappdata"),
                          TEMP=str(self.root / "temp"), TMP=str(self.root / "temp"),
                          QT_QPA_PLATFORM="windows" if self.data["visible"] else "offscreen")
        os.environ[_ENV] = str(self.root)
        self.watchdog = threading.Timer(110, lambda: self.fail("check_deadline_exceeded"))
        self.watchdog.daemon = True
        self.watchdog.start()
        sys.excepthook = lambda *_: self.fail("unhandled_check_exception")

    def _compose(self, cfg):
        project = "evejs-update-check-" + hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        script = (self.root / "server.js").resolve().as_posix()
        services = {}
        ports = iter(range(self.data["game_port"], self.data["game_port"] + 6))
        for service, role, internal in (("server", "game", (26000,26001,26002,26003,5222)), ("market", "market", (40110,))):
            services[service] = {"image": "node:22-alpine", "command": ["node", "/fixture.js", role, "/tmp/ready.json", ",".join(map(str,internal))],
                "volumes": [{"type":"bind","source":script,"target":"/fixture.js","read_only":True}],
                "ports": [f"127.0.0.1:{next(ports)}:{inside}" for inside in internal],
                "healthcheck": {"test":["CMD","node","-e",f"require('http').get('http://127.0.0.1:{internal[-1]}/health',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"],"interval":"1s","timeout":"1s","retries":15}}
        compose = self.root / "world/compose.yaml"
        compose.write_text(json.dumps({"services":services}, indent=2), encoding="utf-8")
        cfg.update(runtime_backend="docker_compose", docker_compose_file=str(compose),
                   docker_project_name=project, docker_control_policy="managed" if self.data["case"] == "docker_managed" else "connect_only",
                   docker_keep_running_on_exit=True)
        self.data.update(compose_file=str(compose), project=project)
        self._write()

    def _write(self):
        temporary = self.root / "check.pending"
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(temporary, self.root / "check.json")

    def fail(self, reason):
        self.data.update(phase="failed", error=reason)
        self._write()
        # Only children spawned and retained by this explicit private check.
        for process in self.children:
            if process.poll() is None:
                process.terminate()
        os._exit(1)

    def attach(self, window, app, recovered):
        from PyQt6.QtCore import QTimer
        from .runtime_handoff import _context
        self.window, self.app, self.recovered = window, app, recovered
        self.data["launcher_pid"] = os.getpid()
        self.data["outgoing_context" if self.args is not None else "incoming_context"] = _context(window._cfg)
        self._write()
        QTimer.singleShot(100, self._origin if self.args is not None else self._resumed)

    def _spawn(self, role, port):
        from .server_launcher import _popen_with_console_log, SERVER_CONSOLE_LOG, MARKET_CONSOLE_LOG
        from .platform import get_graceful_server_process_flags, get_hidden_process_flags
        node = shutil.which("node")
        if not node:
            self.fail("node_not_available")
        console = SERVER_CONSOLE_LOG if role == "game" else MARKET_CONSOLE_LOG if role == "market" else self.root / "client-console.log"
        process = _popen_with_console_log([node, str(self.root / "server.js"), role,
                         str(self.root / (role + "-ready.json")), str(port)], console,
                         stdin=subprocess.DEVNULL, **(get_graceful_server_process_flags() if role == "game" else get_hidden_process_flags()))
        self.children.append(process)
        return process

    def _origin(self):
        from PyQt6.QtCore import QTimer
        if not self.children:
            if self.data["case"] in ("native", "external"):
                game = self._spawn("game", self.data["game_port"])
                market = self._spawn("market", 40111)
                if self.data["case"] == "native":
                    self.window._server_proc, self.window._market_proc = game, market
            client = self._spawn("client", 0)
            self.window._tracker.add("update-fixture", "Update Fixture", client)
        from .service_status import ServiceState
        snapshot = self.window._runtime_snapshot
        if snapshot.game is not ServiceState.ONLINE or snapshot.market is not ServiceState.ONLINE:
            QTimer.singleShot(250, self._origin)
            return
        expected = {}
        for role in ("game", "market", "client"):
            ready = self.root / (role + "-ready.json")
            if ready.exists():
                expected[role] = json.loads(ready.read_text())
        self.data.update(phase="awaiting_restart", original_launcher_pid=os.getpid(), expected=expected)
        self._write()
        self.window._latest_download_url = self.data["url"]
        self.window._latest_version = "Private update check"
        self.window._begin_update_install()

    def _resumed(self):
        from PyQt6.QtCore import QTimer
        native = self.data["case"] == "native"
        if (self.recovered.clients != 1 or self.recovered.game != native or self.recovered.market != native):
            self.fail("runtime_ownership_was_not_restored")
        tracker = self.window._tracker.running
        if len(tracker) != 1 or tracker[0].pid != self.data["expected"]["client"]["pid"]:
            self.fail("client_identity_changed")
        if native and (self.window._server_proc.pid != self.data["expected"]["game"]["pid"]
                       or self.window._market_proc.pid != self.data["expected"]["market"]["pid"]):
            self.fail("native_process_identity_changed")
        from .service_status import ServiceState
        if self.window._runtime_snapshot.game is not ServiceState.ONLINE or self.window._runtime_snapshot.market is not ServiceState.ONLINE:
            QTimer.singleShot(250, self._resumed)
            return
        self.data.update(phase="resumed", ownership_restored=True, same_processes=True,
                         resumed_launcher_pid=os.getpid(), native_owned=native,
                         client_count=self.window._tracker.running_count)
        self._write()
        QTimer.singleShot(500, self._finish)

    def _finish(self):
        from PyQt6.QtCore import QTimer
        # The outside evidence collector can inspect Native UI without a race.
        if not (self.root / "finish-check").exists():
            QTimer.singleShot(250, self._finish)
            return
        for client in self.window._tracker.running:
            self.children.append(client.process)
        self.window._tracker.kill_all()
        self.window._tracker.prune_dead()
        if self.window._tracker.running_count:
            QTimer.singleShot(100, self._finish)
            return
        if self.data["case"] == "native":
            self.window._run_stop_sequence(stop_game=True, stop_market=True, on_complete=self._done)
        else:
            self._done()

    def _done(self):
        if self.data["case"] == "native" and (self.window._server_process_alive() or self.window._market_proc is not None):
            self.fail("resumed_native_stop_control_failed")
        self.data.update(phase="passed", stop_control_verified=self.data["case"] == "native")
        self._write()
        self.watchdog.cancel()
        self.window.close()
