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
    monkeypatch.setattr(
        overview_patch,
        "is_eve_client_running",
        lambda: pytest.fail("CheckOnly must not inspect or block live clients"),
    )
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


def test_beta_nonstandard_bundle_location_uses_official_read_only_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client, bundle, ca_text = _certificate_fixture(
        tmp_path,
        bundles_match=True,
    )
    alternate_bundle = client / "res" / "alternate" / "cacert.pem"
    alternate_bundle.parent.mkdir(parents=True)
    alternate_bundle.write_text(f"SYSTEM CERTIFICATES\n{ca_text}\n", encoding="utf-8")
    bundle.unlink()
    original_bundle = alternate_bundle.read_bytes()
    monkeypatch.setattr(
        overview_patch,
        "is_eve_client_running",
        lambda: pytest.fail("A passing CheckOnly run must not block live clients"),
    )
    monkeypatch.setattr(
        platform_win,
        "_selected_ca_is_in_client_bundles",
        lambda *_args: pytest.fail("CheckOnly owns recursive bundle validation"),
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="check passed", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert platform_win.prepare_evejs_client_certificate_trust(root, client)

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[-1] == "-CheckOnly"
    assert "-RotateCa" not in argv
    assert "-SkipClientBundles" not in argv
    assert str(client) == argv[argv.index("-ClientPath") + 1]
    assert kwargs["cwd"] == str(root)
    assert alternate_bundle.read_bytes() == original_bundle


@pytest.mark.parametrize("ca_state", ["missing", "invalid"])
def test_beta_check_only_rejects_missing_or_invalid_ca_without_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ca_state: str,
) -> None:
    root, client, bundle, _ca_text = _certificate_fixture(
        tmp_path,
        bundles_match=False,
    )
    ca_path = root / "server" / "certs" / "xmpp-ca-cert.pem"
    if ca_state == "missing":
        ca_path.unlink()
    else:
        ca_path.write_text("not a PEM certificate\n", encoding="utf-8")
    original_bundle = bundle.read_bytes()
    monkeypatch.setattr(
        overview_patch,
        "is_eve_client_running",
        lambda: pytest.fail("CheckOnly failure must not attempt repair or block on clients"),
    )
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        return subprocess.CompletedProcess(
            argv,
            7,
            stdout="",
            stderr="selected CA could not be validated",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as error:
        platform_win.prepare_evejs_client_certificate_trust(root, client)

    message = str(error.value)
    assert "No automatic repair or CA rotation was attempted" in message
    assert f"EveJS root: {root}" in message
    assert f"EVE client: {client}" in message
    assert "SetupEveJS.bat -Only certs,client-offline" in message
    assert "selected CA could not be validated" in message
    assert len(calls) == 1
    assert calls[0][-1] == "-CheckOnly"
    assert "-RotateCa" not in calls[0]
    assert bundle.read_bytes() == original_bundle
    if ca_state == "missing":
        assert not ca_path.exists()
    else:
        assert ca_path.read_text(encoding="utf-8") == "not a PEM certificate\n"


def test_beta_check_only_timeout_reports_paths_without_repair(
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
        lambda: pytest.fail("CheckOnly timeout must not enter a mutation guard"),
    )
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        raise subprocess.TimeoutExpired(argv, timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as error:
        platform_win.prepare_evejs_client_certificate_trust(root, client, timeout_seconds=5)

    message = str(error.value)
    assert f"EveJS root: {root}" in message
    assert f"EVE client: {client}" in message
    assert "No automatic repair was attempted" in message
    assert "SetupEveJS.bat -Only certs,client-offline" in message
    assert calls[0][-1] == "-CheckOnly"
    assert bundle.read_bytes() == original_bundle
