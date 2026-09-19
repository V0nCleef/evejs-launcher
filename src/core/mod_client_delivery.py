"""Advisory, last-reported client script delivery. Never controls activation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from .. import config

FEATURE_ENV = "EVEJS_LAUNCHER_HELPER_FEATURES"
FEATURE = "client-script-delivery-v1"
METHODS = frozenset({"login-handshake", "client-script-patch", "none"})
LEGACY_GUIDE_URL = ("https://github.com/V0nCleef/evejs-launcher/blob/main/"
                    "docs/how-to-make-a-mod/12-client-files.md#legacy-client-script-patches")


def _scope(descriptor, client_root, backend):
    if client_root is None or backend not in {"native", "docker"}:
        return None
    canonical = lambda p: os.path.normcase(str(Path(p).resolve()))
    return {"root": canonical(descriptor.root), "package": canonical(descriptor.folder),
            "client": canonical(client_root), "backend": backend}


def _path(scope):
    key = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    return config.CONFIG_DIR / "mod-client-delivery" / (key + ".json")


def _identity(descriptor):
    helper = descriptor.launcher_api.helper if descriptor.launcher_api else None
    if helper is None:
        return None
    # Old observations cannot label a replaced manifest or helper as current.
    info = helper.path.stat()
    return {"manifest": descriptor.identity,
            "helper": [info.st_size, info.st_mtime_ns]}


def record_delivery(result, backend):
    """Called after a successful lifecycle/launch preparation commit.

    Missing metadata clears an older observation. Cache failure never fails an
    install or launch; this is presentation state, not an ownership receipt.
    """
    temporary = None
    try:
        if not result.success or result.state != "ready":
            return
        scope = _scope(result.descriptor, result.context.client_root, backend)
        if scope is None:
            return
        path = _path(scope)
        method = getattr(result, "client_script_delivery", None)
        if result.action in {"prepare_disable", "prepare_remove"} or method is None:
            path.unlink(missing_ok=True)
            return
        identity = _identity(result.descriptor)
        if identity is None:
            return
        document = {"schemaVersion": 1, "scope": scope, "identity": identity, "method": method}
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="delivery-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream)
        os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def reported_delivery(mod, client_root, backend):
    """Return only a scoped observation; absence does not imply legacy use."""
    try:
        if not mod.valid or not mod.active or mod.api_descriptor is None:
            return None
        descriptor = mod.api_descriptor
        scope = _scope(descriptor, client_root, backend)
        if scope is None:
            return None
        path = _path(scope)
        if path.stat().st_size > 8192:
            return None
        document = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(document, dict) or document.get("schemaVersion") != 1
                or document.get("scope") != scope or document.get("identity") != _identity(descriptor)):
            return None
        method = document.get("method")
        return method if isinstance(method, str) and method in METHODS else None
    except (OSError, ValueError, TypeError):
        return None
