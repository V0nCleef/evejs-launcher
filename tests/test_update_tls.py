"""Updater HTTPS keeps native verification scoped to each request."""
import io
import json
import ssl
import sys
from types import SimpleNamespace

import pytest


def test_native_context_keeps_verification_hostname_and_default_policy(monkeypatch):
    from src.updater import tls
    original = ssl.SSLContext
    monkeypatch.setattr(tls.sys, "platform", "win32")
    first, second = tls.update_ssl_context(), tls.update_ssl_context()
    assert first is not second
    for context in (first, second):
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        assert not context.verify_flags & ssl.VERIFY_CRL_CHECK_CHAIN
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert ssl.SSLContext is original


def test_other_platform_retains_standard_verified_context(monkeypatch):
    from src.updater import tls
    monkeypatch.setattr(tls.sys, "platform", "linux")
    context = tls.update_ssl_context()
    assert type(context) is ssl.SSLContext
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def test_unsafe_native_library_fails_closed_without_openssl_fallback(monkeypatch):
    from src.updater import tls
    monkeypatch.setattr(tls.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(__version__="0.10.4"))
    monkeypatch.setattr(ssl, "create_default_context", lambda: pytest.fail("No verification fallback"))
    with pytest.raises(ssl.SSLError, match="truststore 0.10.5"):
        tls.update_ssl_context()


def test_launcher_api_passes_the_scoped_context(monkeypatch):
    from src.updater import github
    context = object()
    monkeypatch.setattr(github, "update_ssl_context", lambda: context, raising=False)
    def open_request(request, *, timeout, **kwargs):
        assert kwargs.get("context") is context
        return io.BytesIO(json.dumps({"tag_name": "v1.0.79", "assets": []}).encode())
    monkeypatch.setattr(github.urllib.request, "urlopen", open_request)
    assert github.get_latest_release()["tag_name"] == "v1.0.79"


def test_launcher_asset_download_passes_the_scoped_context(monkeypatch, tmp_path):
    from src.updater import github
    context = object()
    monkeypatch.setattr(github, "update_ssl_context", lambda: context, raising=False)
    class Response(io.BytesIO):
        headers = {"Content-Length": "7"}
    def open_request(request, *, timeout, **kwargs):
        assert kwargs.get("context") is context
        return Response(b"archive")
    monkeypatch.setattr(github.urllib.request, "urlopen", open_request)
    target = tmp_path / "asset.zip"
    assert github.download_asset("https://github.com/Author/Example/releases/download/v1/asset.zip", target)
    assert target.read_bytes() == b"archive"


def test_mod_api_sidecar_and_asset_opener_retains_tls_and_redirect_guard(monkeypatch):
    from src.core import mod_update_source as updates
    context = object()
    monkeypatch.setattr(updates, "update_ssl_context", lambda: context, raising=False)
    def build(*handlers):
        secure = [h for h in handlers if isinstance(h, updates.urllib.request.HTTPSHandler)]
        assert len(secure) == 1 and secure[0]._context is context
        redirect = next(h for h in handlers if isinstance(h, updates._GitHubRedirects))
        for url in ("http://github.com/asset", "https://example.invalid/asset"):
            with pytest.raises(updates.ModUpdateError):
                redirect.redirect_request(None, None, 302, "", {}, url)
        return SimpleNamespace(open=lambda request, timeout: "opened")
    monkeypatch.setattr(updates.urllib.request, "build_opener", build)
    assert updates.github_open("https://api.github.com/repos/Author/Example/releases") == "opened"


def test_certificate_failures_never_retry_with_weaker_validation(monkeypatch, tmp_path):
    from src.updater import github
    from src.core import mod_update_source as updates
    calls = []
    def refused():
        calls.append(True)
        raise ssl.SSLCertVerificationError("Rejected certificate")
    monkeypatch.setattr(github, "update_ssl_context", refused, raising=False)
    monkeypatch.setattr(updates, "update_ssl_context", refused, raising=False)
    monkeypatch.setattr(github.urllib.request, "urlopen", lambda *a, **k: pytest.fail("No retry"))
    assert github.get_latest_release() is None
    assert not github.download_asset("https://github.com/Author/Example/archive.zip", tmp_path / "asset.zip")
    with pytest.raises(ssl.SSLCertVerificationError):
        updates.github_open("https://api.github.com/repos/Author/Example/releases")
    assert len(calls) == 3
    assert not (tmp_path / "asset.zip").exists()
