# -*- mode: python ; coding: utf-8 -*-
"""Linux x86_64 onedir build; preserve Qt's native desktop plugins."""
import json
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules
from src.core.mod_guide import catalog_paths

catalog = json.loads(Path('docs/how-to-make-a-mod/navigation.json').read_text(encoding='utf-8'))
guide = [Path(path) for path in catalog_paths(catalog)]
for path in guide:
    if path.is_absolute() or '..' in path.parts or path.parts[0] not in {'docs', 'examples'} or not path.is_file():
        raise ValueError(f'Invalid public guide path: {path}')

datas = [(str(path), str(path.parent)) for path in guide]
datas += [
    ('assets/data/character_names.json', 'assets/data'),
    ('assets/hero/hero_nebula.png', 'assets/hero'),
    ('assets/deep_signal/operations_orbital.png', 'assets/deep_signal'),
    ('assets/deep_signal/operations_scene.json', 'assets/deep_signal'),
    ('assets/character_creation/*.png', 'assets/character_creation'),
    ('assets/audio/music/celestial_transit.wav', 'assets/audio/music'),
    ('assets/audio/voice/lyra/*.wav', 'assets/audio/voice/lyra'),
    ('assets/audio/voice/lyra/manifest.json', 'assets/audio/voice/lyra'),
    ('assets/*.png', 'assets'), ('assets/*.ico', 'assets'),
    ('src/core/template_settings/*', 'src/core/template_settings'),
    ('src/core/helpers/*.py', 'src/core/helpers'),
    ('src/core/helpers/*.js', 'src/core/helpers'),
    ('src/core/client/overview_bridge_py27.json', 'src/core/client'),
    ('src/core/client/shared_menu.py', 'src/core/client'),
    ('src/core/client/shared_menu_loader.cjs', 'src/core/client'),
    ('src/core/client/login_delivery.cjs', 'src/core/client'),
    ('VERSION', '.'), ('CHANGELOG.md', '.'), ('LICENSE', '.'),
    ('THIRD_PARTY_NOTICES.md', '.'), ('licenses/*', 'licenses'),
]
excluded = ['src.core.platform_win', 'src.core.process_identity_win', 'winreg',
            'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtQuick', 'PyQt6.QtQml',
            'pytest', 'tkinter', 'unittest']
hidden = [name for name in collect_submodules('src') if name not in excluded]
a = Analysis(['main.py'], pathex=[], binaries=[], datas=datas, hiddenimports=hidden,
             excludes=excluded, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='evejs-launcher', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='EveJS-Launcher-Linux-x86_64')
