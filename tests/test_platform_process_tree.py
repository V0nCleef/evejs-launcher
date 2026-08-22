"""Contracts for exact-PID Windows process-tree termination."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest

from src.core import platform_win


def _dword_value(value: object) -> int:
    return int(getattr(value, "value", value))


class _FakeConsoleKernel:
    def __init__(self, attach_results: tuple[bool, ...] = ()) -> None:
        self.events: list[tuple[object, ...]] = []
        self._attach_results = iter(attach_results)

    def FreeConsole(self) -> bool:
        self.events.append(("free",))
        return True

    def AttachConsole(self, pid: object) -> bool:
        self.events.append(("attach", _dword_value(pid)))
        return next(self._attach_results, True)

    def SetConsoleCtrlHandler(self, _handler: object, ignore: bool) -> bool:
        self.events.append(("ignore", bool(ignore)))
        return True

    def GenerateConsoleCtrlEvent(self, event: object, group: object) -> bool:
        self.events.append(
            ("generate", _dword_value(event), _dword_value(group))
        )
        return True


class _FakeMutexKernel:
    def __init__(self, wait_result: int = 0) -> None:
        self.events: list[tuple[object, ...]] = []
        self.wait_result = wait_result

    def CreateMutexW(self, _security, owned: bool, name: str) -> int:  # noqa: N802
        self.events.append(("create", bool(owned), name))
        return 5150

    def WaitForSingleObject(self, handle: object, timeout: object) -> int:  # noqa: N802
        self.events.append(("wait", _dword_value(handle), _dword_value(timeout)))
        return self.wait_result

    def ReleaseMutex(self, handle: object) -> bool:  # noqa: N802
        self.events.append(("release", _dword_value(handle)))
        return True

    def CloseHandle(self, handle: object) -> bool:  # noqa: N802
        self.events.append(("close", _dword_value(handle)))
        return True

def test_graceful_server_flags_create_one_hidden_private_console() -> None:
    kwargs = platform_win.get_graceful_server_process_flags()

    assert kwargs["creationflags"] == subprocess.CREATE_NEW_CONSOLE
    startup_info = kwargs["startupinfo"]
    assert isinstance(startup_info, subprocess.STARTUPINFO)
    assert startup_info.dwFlags & subprocess.STARTF_USESHOWWINDOW
    assert startup_info.wShowWindow == subprocess.SW_HIDE


def test_client_trust_and_spawn_mutex_is_held_through_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel = _FakeMutexKernel()
    monkeypatch.setattr(platform_win, "kernel32", fake_kernel)

    with platform_win.serialize_evejs_client_trust_and_spawn(timeout_seconds=12):
        fake_kernel.events.append(("inside",))

    assert fake_kernel.events == [
        ("create", False, "Local\\EveJSLauncherClientTrustSpawnV1"),
        ("wait", 5150, 12_000),
        ("inside",),
        ("release", 5150),
        ("close", 5150),
    ]


def test_client_trust_and_spawn_mutex_timeout_closes_without_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel = _FakeMutexKernel(wait_result=0x00000102)
    monkeypatch.setattr(platform_win, "kernel32", fake_kernel)

    with pytest.raises(RuntimeError, match="Timed out waiting"):
        with platform_win.serialize_evejs_client_trust_and_spawn(
            timeout_seconds=2,
        ):
            pytest.fail("Timed-out mutex must not enter the launch context")

    assert fake_kernel.events == [
        ("create", False, "Local\\EveJSLauncherClientTrustSpawnV1"),
        ("wait", 5150, 2_000),
        ("close", 5150),
    ]


def test_graceful_shutdown_targets_only_owned_private_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel = _FakeConsoleKernel()
    monkeypatch.setattr(platform_win, "kernel32", fake_kernel)
    process_lists = iter(((), (42_424, os.getpid())))
    monkeypatch.setattr(
        platform_win,
        "_console_process_ids",
        lambda: next(process_lists),
    )
    monkeypatch.setattr(platform_win.time, "sleep", lambda _seconds: None)

    assert platform_win.request_graceful_server_shutdown(42_424)
    assert fake_kernel.events == [
        ("attach", 42_424),
        ("ignore", True),
        ("generate", 1, 0),
        ("free",),
    ]


def test_graceful_shutdown_restores_source_console_through_exact_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel = _FakeConsoleKernel()
    monkeypatch.setattr(platform_win, "kernel32", fake_kernel)
    process_lists = iter(((os.getpid(), 777), (42_424, os.getpid())))
    monkeypatch.setattr(
        platform_win,
        "_console_process_ids",
        lambda: next(process_lists),
    )
    monkeypatch.setattr(platform_win.time, "sleep", lambda _seconds: None)

    assert platform_win.request_graceful_server_shutdown(42_424)
    assert fake_kernel.events == [
        ("free",),
        ("attach", 42_424),
        ("ignore", True),
        ("generate", 1, 0),
        ("free",),
        ("attach", 777),
    ]


def test_graceful_shutdown_does_not_destroy_a_lone_source_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel = _FakeConsoleKernel()
    monkeypatch.setattr(platform_win, "kernel32", fake_kernel)
    monkeypatch.setattr(
        platform_win,
        "_console_process_ids",
        lambda: (os.getpid(),),
    )

    assert not platform_win.request_graceful_server_shutdown(42_424)
    assert fake_kernel.events == []


def test_graceful_shutdown_tries_each_retained_source_console_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel = _FakeConsoleKernel((True, False, True))
    monkeypatch.setattr(platform_win, "kernel32", fake_kernel)
    process_lists = iter(
        ((os.getpid(), 777, 888), (42_424, os.getpid()))
    )
    monkeypatch.setattr(
        platform_win,
        "_console_process_ids",
        lambda: next(process_lists),
    )
    monkeypatch.setattr(platform_win.time, "sleep", lambda _seconds: None)

    assert platform_win.request_graceful_server_shutdown(42_424)
    assert fake_kernel.events[-2:] == [
        ("attach", 777),
        ("attach", 888),
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console contract")
def test_consoleless_launcher_can_deliver_sigbreak_to_hidden_node_console() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the Windows signal integration test")
    target_code = (
        "process.once('SIGBREAK', () => { "
        "console.log('STOPPED'); process.exit(0); }); "
        "console.log('READY'); setInterval(() => {}, 1000);"
    )
    coordinator_code = textwrap.dedent(
        f"""
        import subprocess
        import sys

        from src.core.platform_win import (
            get_graceful_server_process_flags,
            request_graceful_server_shutdown,
        )

        target = subprocess.Popen(
            [{node!r}, "-e", {target_code!r}],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            **get_graceful_server_process_flags(),
        )
        try:
            ready = target.stdout.readline().strip()
            requested = request_graceful_server_shutdown(target.pid)
            return_code = target.wait(timeout=5)
            stopped = target.stdout.readline().strip()
            probe = subprocess.run(
                [sys.executable, "-c", "print('SPAWNED')"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if (
                ready != "READY"
                or not requested
                or return_code != 0
                or stopped != "STOPPED"
                or probe.returncode != 0
                or probe.stdout.strip() != "SPAWNED"
            ):
                raise SystemExit(1)
        finally:
            if target.poll() is None:
                target.kill()
                target.wait(timeout=5)
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", coordinator_code],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_terminate_process_tree_uses_exact_pid_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setenv("SystemRoot", r"C:\Windows Test")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert platform_win.terminate_process_tree(42_424)
    assert captured["argv"] == [
        r"C:\Windows Test\System32\taskkill.exe",
        "/F",
        "/T",
        "/PID",
        "42424",
    ]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["check"] is False
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["timeout"] == 5
    assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize("pid", [0, -1, True, 1.5, "42"])
def test_terminate_process_tree_rejects_non_positive_integer_pids(pid) -> None:
    with pytest.raises(ValueError):
        platform_win.terminate_process_tree(pid)  # type: ignore[arg-type]


def _certificate_fixture(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    root = tmp_path / "EveJS"
    installer = (
        root
        / "tools"
        / "ClientSETUP"
        / "scripts"
        / "Install-EvEJSCerts.ps1"
    )
    installer.parent.mkdir(parents=True)
    installer.write_text("# fixture", encoding="utf-8")
    ca_text = (
        "-----BEGIN CERTIFICATE-----\n"
        "RklYVFVSRV9FVkVKU19DQQ==\n"
        "-----END CERTIFICATE-----"
    )
    ca_path = root / "server" / "certs" / "xmpp-ca-cert.pem"
    ca_path.parent.mkdir(parents=True)
    ca_path.write_text(ca_text, encoding="utf-8")
    client = tmp_path / "client" / "tq"
    bundle = client / "bin64" / "packages" / "certifi" / "cacert.pem"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("SYSTEM CERTIFICATES\n", encoding="utf-8")
    return root, client, bundle, ca_text


def test_certificate_trust_skips_older_root_without_official_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("PowerShell must not run"),
    )

    assert not platform_win.prepare_evejs_client_certificate_trust(
        tmp_path / "older-evejs",
        tmp_path / "missing-client",
    )


def test_certificate_trust_current_ca_still_runs_official_health_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client, bundle, ca_text = _certificate_fixture(tmp_path)
    bundle.write_text(f"SYSTEM CERTIFICATES\n{ca_text}\n", encoding="utf-8")
    from src.core import overview_patch

    monkeypatch.setattr(
        overview_patch,
        "is_eve_client_running",
        lambda: pytest.fail("A current CA must not probe or block live clients"),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout="already healthy",
            stderr="",
        ),
    )

    assert platform_win.prepare_evejs_client_certificate_trust(root, client)


def test_certificate_trust_blocks_ca_rotation_while_eve_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client, _bundle, _ca_text = _certificate_fixture(tmp_path)
    from src.core import overview_patch

    monkeypatch.setattr(overview_patch, "is_eve_client_running", lambda: True)

    with pytest.raises(RuntimeError, match="Close every EVE client"):
        platform_win.prepare_evejs_client_certificate_trust(root, client)


def test_certificate_trust_runs_official_installer_hidden_and_reverifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client, bundle, ca_text = _certificate_fixture(tmp_path)
    from src.core import overview_patch

    monkeypatch.setattr(overview_patch, "is_eve_client_running", lambda: False)
    system_root = tmp_path / "Windows"
    powershell = (
        system_root
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    powershell.parent.mkdir(parents=True)
    powershell.write_bytes(b"")
    monkeypatch.setenv("SystemRoot", str(system_root))
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        bundle.write_text(
            f"SYSTEM CERTIFICATES\r\n\r\n{ca_text}\r\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="[eve.js] Chat and public-gateway certificates are ready.\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert platform_win.prepare_evejs_client_certificate_trust(
        root,
        client,
        timeout_seconds=123,
    )
    assert captured["argv"] == [
        str(powershell),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(
            root
            / "tools"
            / "ClientSETUP"
            / "scripts"
            / "Install-EvEJSCerts.ps1"
        ),
        "-ClientPath",
        str(client),
    ]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == str(root)
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == 123.0
    assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_certificate_trust_failure_blocks_launch_with_installer_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client, _bundle, _ca_text = _certificate_fixture(tmp_path)
    from src.core import overview_patch

    monkeypatch.setattr(overview_patch, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            7,
            stdout="",
            stderr="certificate fixture failed",
        ),
    )

    with pytest.raises(RuntimeError, match="certificate fixture failed"):
        platform_win.prepare_evejs_client_certificate_trust(root, client)


def test_certificate_trust_rejects_false_success_without_bundle_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client, _bundle, _ca_text = _certificate_fixture(tmp_path)
    from src.core import overview_patch

    monkeypatch.setattr(overview_patch, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout="success",
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="still missing"):
        platform_win.prepare_evejs_client_certificate_trust(root, client)
