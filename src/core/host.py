"""Host capabilities and user directories, independent of the server backend."""
from __future__ import annotations

import os
from pathlib import Path
import sys

IS_LINUX = sys.platform.startswith("linux")
IS_WINDOWS = sys.platform == "win32"
LINUX_PREVIEW_NOTICE = (
    'Linux uses the Node, native Market, Proton and prefix paths recorded by Linux Setup. '
    'Portable loader mods and Node/native helper packages are supported; Windows installers '
    'and PowerShell helpers require a compatible package from the mod author.'
)


def config_directory() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA", "")) / "EveJS-Launcher"
    value = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(value) if value and Path(value).is_absolute() else Path.home() / ".config"
    return base / "evejs-launcher"


def state_directory() -> Path:
    if IS_WINDOWS:
        return config_directory()
    value = os.environ.get("XDG_STATE_HOME", "")
    base = Path(value) if value and Path(value).is_absolute() else Path.home() / ".local" / "state"
    return base / "evejs-launcher"


def cache_directory() -> Path:
    if IS_WINDOWS:
        return config_directory() / 'cache'
    value = os.environ.get('XDG_CACHE_HOME', '')
    base = Path(value) if value and Path(value).is_absolute() else Path.home() / '.cache'
    return base / 'evejs-launcher'


def external_process_environment(environment: dict[str, str] | None = None) -> dict[str, str]:
    """Keep bundled Qt/libstdc++ out of Linux system programs and Proton."""
    result = dict(os.environ if environment is None else environment)
    if IS_LINUX and getattr(sys, 'frozen', False):
        bundle = getattr(sys, '_MEIPASS', '')
        if 'LD_LIBRARY_PATH_ORIG' in result:
            original = result.pop('LD_LIBRARY_PATH_ORIG')
            if original:
                result['LD_LIBRARY_PATH'] = original
            else:
                result.pop('LD_LIBRARY_PATH', None)
        elif bundle and 'LD_LIBRARY_PATH' in result:
            # Repeated preparation must preserve an already restored user
            # library path. Remove only paths belonging to this frozen bundle.
            paths = [value for value in result['LD_LIBRARY_PATH'].split(os.pathsep)
                     if not Path(value).is_relative_to(Path(bundle))]
            if paths:
                result['LD_LIBRARY_PATH'] = os.pathsep.join(paths)
            else:
                result.pop('LD_LIBRARY_PATH', None)
        if bundle:
            for key in ('PATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
                if key in result:
                    paths = [value for value in result[key].split(os.pathsep)
                             if not Path(value).is_relative_to(Path(bundle))]
                    if paths:
                        result[key] = os.pathsep.join(paths)
                    else:
                        result.pop(key, None)
    return result
