# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for EveJS Launcher V2 — onedir mode."""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Modules to exclude — aggressive pruning
EXCLUDES = [
    # Qt6 modules we don't use
    'PyQt6.QtQuick', 'PyQt6.QtQml', 'PyQt6.QtQmlModels', 'PyQt6.QtQmlWorkerScript',
    'PyQt6.QtDesigner', 'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets',
    'PyQt6.QtMultimediaWidgets',
    'PyQt6.Qt3DAnimation', 'PyQt6.Qt3DCore', 'PyQt6.Qt3DExtras',
    'PyQt6.Qt3DInput', 'PyQt6.Qt3DLogic', 'PyQt6.Qt3DQuick',
    'PyQt6.Qt3DQuickAnimation', 'PyQt6.Qt3DQuickExtras', 'PyQt6.Qt3DQuickInput',
    'PyQt6.Qt3DQuickRender', 'PyQt6.Qt3DQuickScene2D', 'PyQt6.Qt3DRender',
    'PyQt6.QtShaderTools', 'PyQt6.QtOpenGL', 'PyQt6.QtOpenGLWidgets',
    'PyQt6.QtSql', 'PyQt6.QtTest', 'PyQt6.QtWebChannel', 'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebEngineQuick', 'PyQt6.QtWebEngineWidgets', 'PyQt6.QtWebSockets',
    'PyQt6.QtXml', 'PyQt6.QtXmlPatterns', 'PyQt6.QtCharts', 'PyQt6.QtDataVisualization',
    'PyQt6.QtNetworkAuth', 'PyQt6.QtNfc', 'PyQt6.QtPositioning', 'PyQt6.QtPrintSupport',
    'PyQt6.QtPurchasing', 'PyQt6.QtQuick3D', 'PyQt6.QtQuickControls2',
    'PyQt6.QtQuickDialogs2', 'PyQt6.QtQuickTemplates2', 'PyQt6.QtQuickWidgets',
    'PyQt6.QtRemoteObjects', 'PyQt6.QtScxml', 'PyQt6.QtSensors', 'PyQt6.QtSerialPort',
    'PyQt6.QtStateMachine', 'PyQt6.QtVirtualKeyboard',
    'PyQt6.QtWaylandClient', 'PyQt6.QtWebView', 'PyQt6.QtBluetooth',
    'PyQt6.QtHelp', 'PyQt6.QtLocation', 'PyQt6.QtSvg', 'PyQt6.QtSvgWidgets',
    # Heavy Python packages we don't need in the bundle
    'cryptography', 'numpy', 'pandas', 'scipy', 'matplotlib',
    'tkinter', 'turtle', 'turtledemo', 'idlelib', 'lib2to3', 'distutils',
    'setuptools', 'pip', 'wheel', 'venv', 'ensurepip', 'pydoc', 'doctest',
    'unittest', 'test', 'xmlrpc', 'asyncio', 'multiprocessing', 'concurrent',
    'curses', 'readline', 'dbm', 'shelve', 'zoneinfo',
    # PyQt6 plugins we don't use (via excludes in Analysis)
    'PyQt6.Qt6.plugins.sqldrivers', 'PyQt6.Qt6.plugins.sceneparsers',
    'PyQt6.Qt6.plugins.assetimporters', 'PyQt6.Qt6.plugins.renderers',
    'PyQt6.Qt6.plugins.qmlls',
    'PyQt6.Qt6.plugins.tls', 'PyQt6.Qt6.plugins.qmllint',
    'PyQt6.Qt6.plugins.webengine',
    'PyQt6.Qt6.plugins.position', 'PyQt6.Qt6.plugins.printsupport',
    'PyQt6.Qt6.plugins.sensors', 'PyQt6.Qt6.plugins.serialport',
    'PyQt6.Qt6.plugins.bluetooth', 'PyQt6.Qt6.plugins.nfc',
    'PyQt6.Qt6.plugins.networkinformation', 'PyQt6.Qt6.plugins.generic',
    'PyQt6.Qt6.plugins.iconengines', 'PyQt6.Qt6.plugins.imageformats',
    'PyQt6.Qt6.plugins.platforminputcontexts', 'PyQt6.Qt6.plugins.platforms',
    'PyQt6.Qt6.plugins.platformthemes', 'PyQt6.Qt6.plugins.styles',
    'PyQt6.Qt6.plugins.wayland-decoration-client',
    'PyQt6.Qt6.plugins.wayland-graphics-integration-client',
    'PyQt6.Qt6.plugins.wayland-shell-integration',
]

# Binaries to exclude (DLLs that get bundled but we don't need)
BINARY_EXCLUDES = [
    'opengl32sw.dll',  # Software OpenGL fallback — we have GPU
    'Qt6Quick*.dll', 'Qt6Qml*.dll', 'Qt6Designer*.dll', 'Qt6Pdf*.dll',
    'Qt6ShaderTools*.dll', 'Qt6Quick3D*.dll',
    'Qt6WebEngine*.dll', 'Qt6WebView*.dll', 'Qt6WebSockets*.dll',
    'Qt63D*.dll', 'Qt6Sql*.dll', 'Qt6Test*.dll', 'Qt6Xml*.dll',
    'Qt6Charts*.dll', 'Qt6DataVisualization*.dll', 'Qt6NetworkAuth*.dll',
    'Qt6Nfc*.dll', 'Qt6Positioning*.dll', 'Qt6PrintSupport*.dll',
    'Qt6Purchasing*.dll', 'Qt6RemoteObjects*.dll', 'Qt6Scxml*.dll',
    'Qt6Sensors*.dll', 'Qt6SerialPort*.dll', 'Qt6StateMachine*.dll',
    'Qt6VirtualKeyboard*.dll', 'Qt6Bluetooth*.dll',
    'Qt6Help*.dll', 'Qt6Location*.dll', 'Qt6Svg*.dll',
    'd3dcompiler_*.dll',  # DirectX shader compiler — not needed
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/hero/*.png', 'assets/hero'),
        ('assets/deep_signal/*.png', 'assets/deep_signal'),
        # Launcher-owned originals only. Personal playlist paths remain local
        # config references and user MP3s are never copied into a release.
        ('assets/audio/music/*.wav', 'assets/audio/music'),
        ('assets/audio/voice/lyra/*.wav', 'assets/audio/voice/lyra'),
        ('assets/audio/voice/lyra/manifest.json', 'assets/audio/voice/lyra'),
        ('assets/*.png', 'assets'),
        ('assets/*.ico', 'assets'),
        ('CHANGELOG.md', '.'),
        ('README.md', '.'),
        # Package only reviewed public documentation. A wildcard here can
        # silently scoop local investigation notes into a public release.
        ('docs/MOD_AUTHORING.md', 'docs'),
        ('LICENSE', '.'),
        ('THIRD_PARTY_NOTICES.md', '.'),
        ('VERSION', '.'),
        ('licenses/*', 'licenses'),
        ('src/core/template_settings/*', 'src/core/template_settings'),
        ('src/core/helpers/*', 'src/core/helpers'),
    ],
    hiddenimports=[
        'PyQt6.QtWidgets', 'PyQt6.QtCore', 'PyQt6.QtGui',
        'PyQt6.QtMultimedia',
        'sqlite3', 'json', 'socket', 'threading', 'logging',
        'pathlib', 'dataclasses', 'subprocess', 'webbrowser',
        'calendar',  # required by urllib → email → calendar chain when frozen
        'src', 'src.app', 'src.config', 'src.constants', 'src.theme',
        'src.audio', 'src.audio.assets', 'src.audio.backends',
        'src.audio.controller', 'src.audio.events', 'src.audio.settings',
        'src.core.db', 'src.core.discovery', 'src.core.launcher',
        'src.core.server_launcher', 'src.core.process_tracker',
        'src.core.profiles', 'src.core.mod_manager', 'src.core.mod_manifest',
        'src.core.mod_activation_service', 'src.core.mod_activation_state',
        'src.core.mod_lifecycle_lock', 'src.core.mod_management',
        'src.core.mod_runtime_state',
        'src.core.character_creation', 'src.core.character_deletion',
        'src.core.native_maintenance',
        'src.core.overview_patch', 'src.core.overview_state',
        'src.core.groups',
        'src.core.platform', 'src.core.platform_win',
        'src.workers.db_worker', 'src.workers.portrait_worker', 'src.workers.server_worker',
        'src.workers.character_creation_worker', 'src.workers.character_deletion_worker',
        'src.workers.overview_patch_worker', 'src.workers.mod_management_worker',
        'src.widgets.title_bar', 'src.widgets.nav_panel', 'src.widgets.status_bar',
        'src.widgets.deep_signal_background', 'src.widgets.docking_traffic_overlay',
        'src.widgets.glass_panel',
        'src.widgets.page_header', 'src.widgets.status_ring', 'src.ui.motion',
        'src.widgets.shipboard_caption',
        'src.widgets.character_card', 'src.widgets.detail_panel', 'src.widgets.console_panel',
        'src.widgets.hero_banner', 'src.widgets.skeleton_card', 'src.widgets.toggle_switch',
        'src.widgets.update_button',
        'src.widgets.new_character_card', 'src.widgets.new_character_dialog',
        'src.pages.home_page', 'src.pages.characters_page', 'src.pages.mods_page',
        'src.pages.settings_page',
        'src.updater.github', 'src.updater.checker', 'src.updater.dialog', 'src.updater.installer',
        'src.updater.progress_dialog', 'src.updater.handoff',
        'src.utils.logger', 'src.utils.cache',
    ] + collect_submodules('uncompyle6') + collect_submodules('xdis') + collect_submodules('spark_parser'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Filter out excluded binaries
a.binaries = [b for b in a.binaries if not any(
    b[0].lower().endswith(exc.lower().replace('*', '')) or
    exc.lower().replace('*', '') in b[0].lower()
    for exc in BINARY_EXCLUDES
)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],  # No extra binaries — COLLECT handles them below
    exclude_binaries=True,
    name='EveJS-Launcher-V1',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/logo.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='EveJS-Launcher-V1',
)
