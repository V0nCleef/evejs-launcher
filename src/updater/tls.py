"""Verified updater HTTPS with native Windows certificate chain validation."""
from __future__ import annotations

import ssl
import sys


def update_ssl_context():
    """Return a fresh context; no global patching or verification fallback."""
    if sys.platform == "win32":
        try:
            import truststore
        except ImportError as exc:
            raise ssl.SSLError("Windows update HTTPS requires truststore 0.10.5.") from exc
        # The pinned release fixes concurrent-handshake verification races in
        # older versions. Do not silently use a different vendored copy.
        if truststore.__version__ != "0.10.5":
            raise ssl.SSLError("Windows update HTTPS requires truststore 0.10.5.")
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context
