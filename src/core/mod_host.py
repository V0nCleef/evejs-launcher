"""Host-side execution capabilities, separate from a mod's server backend."""
from __future__ import annotations

import os
from pathlib import Path

from .host import IS_LINUX


def helper_unavailable_reason(helper) -> str:
    if not IS_LINUX or helper is None or helper.runtime == 'node':
        return ''
    if helper.runtime == 'powershell':
        return 'This mod uses a Windows PowerShell helper. Its author must provide a Linux or Node helper.'
    path = Path(helper.path)
    try:
        with path.open('rb') as stream:
            signature = stream.read(256)
        if not os.access(path, os.X_OK):
            return 'This mod’s Linux helper is not executable.'
        if signature.startswith(b'\x7fELF') or signature.startswith(b'#!'):
            return ''
    except OSError:
        return 'This mod’s helper is unavailable.'
    return 'This mod does not provide a native Linux executable or script helper.'


def mod_unavailable_reason(mod) -> str:
    if not IS_LINUX:
        return ''
    if mod.api_descriptor is not None and mod.api_descriptor.launcher_api is not None:
        return helper_unavailable_reason(mod.api_descriptor.launcher_api.helper)
    from .mod_manifest import ActivationKind
    if mod.activation_kind is ActivationKind.CLIENT_PACKAGE:
        return 'This client integration requires its Windows installer. Use a package with a supported Linux helper.'
    return ''
