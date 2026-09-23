from pathlib import Path
import subprocess

import pytest

from src.core import overview_patch, platform_win


_BETA_INSTALLER = """param(
  [string]$ClientPath,
  [switch]$CheckOnly,
  [switch]$RotateCa,
  [switch]$ServerFilesOnly
)
"""


def _certificate_fixture(
    tmp_path: Path,
    *,
    bundles_match: bool,
) -> tuple[Path, Path, Path, str]:
    root = tmp_path / "EveJS"
    installer = (
        root
        / "tools"
        / "ClientSETUP"
        / "scripts"
        / "Install-EvEJSCerts.ps1"
    )
    installer.parent.mkdir(parents=True)
    installer.write_text(_BETA_INSTALLER, encoding="utf-8")
    (root / "package.json").write_text(
        '{"name":"eve.js","version":"0.12.9"}\n',
        encoding="utf-8",
    )
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
    bundle.write_text(
        "SYSTEM CERTIFICATES\n" + (ca_text + "\n" if bundles_match else ""),
        encoding="utf-8",
    )
    return root, client, bundle, ca_text


def test_beta_current_bundles_use_read_only_check_and_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client, bundle, _ca_text = _certificate_fixture(
        tmp_path,
        bundles_match=True,
    )
    original_bundle = bundle.read_bytes()
    monkeypatch.setattr(
        overview_patch,
        "is_eve_client_running",
        lambda: pytest.fail("A passing CheckOnly run must not block live clients"),
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="check passed", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert platform_win.prepare_evejs_client_certificate_trust(root, client)

    assert len(calls) == 1
    argv = calls[0][0]
    assert argv[-1] == "-CheckOnly"
    assert "-SkipClientBundles" not in argv
    assert "-ServerFilesOnly" not in argv
    assert "-RotateCa" not in argv
    assert bundle.read_bytes() == original_bundle


def test_beta_check_failure_does_not_retry_with_mutating_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client, bundle, _ca_text = _certificate_fixture(
        tmp_path,
        bundles_match=True,
    )
    original_bundle = bundle.read_bytes()
    monkeypatch.setattr(overview_patch, "is_eve_client_running", lambda: True)
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv,
            7,
            stdout="",
            stderr="trust check failed",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="No automatic repair or CA rotation"):
        platform_win.prepare_evejs_client_certificate_trust(root, client)

    assert len(calls) == 1
    assert calls[0][-1] == "-CheckOnly"
    assert "-RotateCa" not in calls[0]
    assert bundle.read_bytes() == original_bundle


def test_beta_unmatched_bundles_block_installer_while_eve_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client, bundle, _ca_text = _certificate_fixture(
        tmp_path,
        bundles_match=False,
    )
    original_bundle = bundle.read_bytes()
    monkeypatch.setattr(overview_patch, "is_eve_client_running", lambda: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "A live EVE client must block every certificate installer mutation"
        ),
    )

    with pytest.raises(RuntimeError, match="Close every EVE client"):
        platform_win.prepare_evejs_client_certificate_trust(root, client)

    assert bundle.read_bytes() == original_bundle


def test_beta_unmatched_bundles_use_normal_installer_when_client_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client, bundle, ca_text = _certificate_fixture(
        tmp_path,
        bundles_match=False,
    )
    monkeypatch.setattr(overview_patch, "is_eve_client_running", lambda: False)
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        bundle.write_text(
            bundle.read_text(encoding="utf-8") + ca_text + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout="prepared", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert platform_win.prepare_evejs_client_certificate_trust(root, client)

    assert len(calls) == 1
    argv = calls[0]
    assert "-CheckOnly" not in argv
    assert "-SkipClientBundles" not in argv
    assert "-ServerFilesOnly" not in argv
    assert "-RotateCa" not in argv
    assert ca_text in bundle.read_text(encoding="utf-8")
