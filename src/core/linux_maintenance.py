"""Typed interoperability with the separately downloaded Linux Setup tools.

No third-party setup code or native payload is bundled in the launcher. Each
operation validates the selected tool and its exact documented command ABI.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
import shutil

from .linux_installation import LinuxInstallation, LinuxSetupError, file_sha256, read_json, read_setup_config

SETUP_VERSION = '1.20'
SETUP_URL = 'https://github.com/Daghie/evejs-linux-setup/releases/download/v1.20/EveJS-261115.2-Universal-Linux-Setup-v1.20-Stock-Only.zip'
SETUP_ARCHIVE_SHA256 = '2f1648794a3c50c5887f1fc5a8bb22fca0384bf59a2524eca0c98f5f99374526'
UPDATER_SHA256 = 'ecc5987728430f07ab98c84a47ef79bd0e5ccbac27e36f7f80da533127ae9ff3'
INSTALLER_SHA256 = '00b9b6fb80497e640d75e45a11f90ca0a0cd990be085ad31ce39300201d05577'
SETUP_TOOL_HASHES = {
    'install-evejs-linux.sh': '00b9b6fb80497e640d75e45a11f90ca0a0cd990be085ad31ce39300201d05577',
    'evejs-linux-launcher.sh': '930cf69cce23e347e55a902ce9818a7fff301e95407d9094ac7fdda1d8228433',
    'evejs-stale-processes.sh': '2214fef6926b4ba8cc97b2be606323b3bbdb2bcf002313952e1a523c69bc78cd',
    'evejs-linux-update.py': 'ecc5987728430f07ab98c84a47ef79bd0e5ccbac27e36f7f80da533127ae9ff3',
    'update-evejs-linux.sh': '880a1f6388ade09d573a460d1f0ee8301c8410e250a9ef436143b71ed650641f',
    'evejs-linux-client-preflight.py': '9d9d244c5fff2cdb6538224c05131ad112f4959a32a480e5abea5bff81761748',
    'evejs-linux-lu-compat.py': 'bfa865991ccf5ee4d2c6cd020121f327b47dc1d92a6e6bd0a3d7f79c126f6a00',
    'portable/steamos-x86_64/manifest.json': 'f220e7b1e552e5e21d93df87532b712ce7eeddb64b1a1358405d8da39007b563',
    'portable/steamos-x86_64/payload.tar.xz': '0a72c00973d001d523235f2c7d1fde4d89c3bfb879b06f90d12b952656382a55',
    'compatibility/market-stock-v0.12.9.json': '4511e9fcc7c1ebcafa51f5c21dd405a668b76fb64dfa99fc3b484aa7f66ccfee',
    'compatibility/market-stock-v261018.1.json': 'cf94f298f3277fabbe9e1839262aeb17de3a410564927f5417d27899d674cbd8',
    'compatibility/market-stock-v261028.1.json': '7450605b93f8dc357d84c5c769945f20971bdeddf15185adaf75f04f7a35d8f5',
    'compatibility/market-stock-v261108.1.json': '2e1c5dc117303beacf48ad0eafa3d8ae0e69bdf7e2f88b790fe6f550c8bb3d7b',
    'compatibility/market-stock-v261115.2.json': '8f510765346484a07812050bc11a18eda58e423c1026c2e4bd2611bf60a1074b',
}


LEGACY_MAINTENANCE_TOOLS = frozenset({
    ('ddaff624167610346cf213e10b0c4d0b01e3756ff7e25c86d800227b2fb8b5c4',
     'b2958773075737be7c624bf29e0e0da32d8841f7bcb543e03051091e6b21b353'),
})


def verify_setup_bundle(folder: Path) -> Path:
    folder = folder.resolve(strict=True)
    for name, expected in SETUP_TOOL_HASHES.items():
        path = folder / name
        if not path.is_file() or path.is_symlink() or file_sha256(path) != expected:
            raise LinuxSetupError('The Linux Setup bundle is missing, modified or from an unsupported version.')
    actual_manifests = {path.relative_to(folder).as_posix() for path in (folder / 'compatibility').glob('market-stock-v*.json')}
    expected_manifests = {name for name in SETUP_TOOL_HASHES if name.startswith('compatibility/')}
    if actual_manifests != expected_manifests:
        raise LinuxSetupError('The Linux Setup compatibility inventory is unexpected.')
    return folder


def download_setup_bundle() -> Path:
    """Fetch a pinned external release into the user's reusable setup cache."""
    import os
    from pathlib import PurePosixPath
    import tempfile
    from urllib.request import Request, urlopen
    import zipfile
    from .host import cache_directory
    parent = cache_directory() / 'linux-setup'
    destination = parent / ('v' + SETUP_VERSION)
    if destination.exists():
        return verify_setup_bundle(destination)
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.download-', dir=parent) as temporary:
        stage = Path(temporary)
        archive = stage / 'setup.zip'
        with urlopen(Request(SETUP_URL, headers={'User-Agent': 'EveJS-Launcher'}), timeout=30) as response, archive.open('xb') as output:
            total = 0
            while chunk := response.read(65536):
                total += len(chunk)
                if total > 32 * 1024 * 1024:
                    raise LinuxSetupError('The Linux Setup download exceeds its size limit.')
                output.write(chunk)
        if file_sha256(archive) != SETUP_ARCHIVE_SHA256:
            raise LinuxSetupError('The Linux Setup download checksum did not match the supported release.')
        extracted = stage / 'extracted'
        with zipfile.ZipFile(archive) as zipped:
            if sum(entry.file_size for entry in zipped.infolist()) > 64 * 1024 * 1024:
                raise LinuxSetupError('The Linux Setup archive exceeds its expanded size limit.')
            for entry in zipped.infolist():
                name = PurePosixPath(entry.filename)
                if (name.is_absolute() or '..' in name.parts or '\\' in entry.filename
                        or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                    raise LinuxSetupError('The Linux Setup archive contains an unsafe path.')
            zipped.extractall(extracted)
        folders = [path for path in extracted.iterdir() if path.is_dir()]
        if len(folders) != 1:
            raise LinuxSetupError('The Linux Setup archive layout is unexpected.')
        folder = verify_setup_bundle(folders[0])
        try:
            os.rename(folder, destination)
        except FileExistsError:
            return verify_setup_bundle(destination)
    return verify_setup_bundle(destination)


class LinuxMaintenanceAction(str, Enum):
    PREVIEW_UPDATE = 'preview_update'
    UPDATE = 'update'
    LIST_BACKUPS = 'list_backups'
    RESTORE = 'restore'
    RESTORE_PROGRAM = 'restore_program'
    RESTORE_DATA = 'restore_data'
    PRUNE_BACKUPS = 'prune_backups'
    RESEED_MARKET = 'reseed_market'
    PREPARE_CDN_8443 = 'prepare_cdn_8443'
    RECOVER_CDN = 'recover_cdn'
    BUILD_MARKET = 'build_market'


@dataclass(frozen=True)
class LinuxMaintenanceRequest:
    action: LinuxMaintenanceAction
    root: Path
    release_zip: Path | None = None
    backup: str = ''
    keep: int = 3
    market_profile: str = 'v1'

    @property
    def mutates(self):
        return self.action not in (LinuxMaintenanceAction.PREVIEW_UPDATE, LinuxMaintenanceAction.LIST_BACKUPS)


def maintenance_command(request: LinuxMaintenanceRequest) -> tuple[list[str], Path]:
    if not isinstance(request, LinuxMaintenanceRequest) or not isinstance(request.action, LinuxMaintenanceAction):
        raise LinuxSetupError('Choose a supported Linux maintenance action.')
    root = request.root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise LinuxSetupError('Select an existing EveJS installation.')
    values = read_setup_config(root / '.evejs-linux.conf')
    recorded = Path(values.get('EVEJS_REPO', ''))
    if not recorded.is_absolute() or recorded.resolve() != root:
        raise LinuxSetupError('The Linux Setup configuration belongs to another installation.')
    helper = root / '.evejs-linux-tools/evejs-linux-update.py'
    if not helper.is_file() or helper.is_symlink():
        raise LinuxSetupError('Prepare runtime compatibility with a supported Linux Setup before using maintenance.')
    compat = helper.parent / 'evejs-linux-lu-compat.py'
    if not compat.is_file() or compat.is_symlink():
        raise LinuxSetupError('The Linux runtime compatibility helper is missing or modified.')
    pair = (file_sha256(helper), file_sha256(compat))
    trusted = {(UPDATER_SHA256, SETUP_TOOL_HASHES['evejs-linux-lu-compat.py']), *LEGACY_MAINTENANCE_TOOLS}
    if pair not in trusted:
        raise LinuxSetupError('Prepare runtime compatibility with a supported Linux Setup; modified or mixed maintenance tools were refused.')
    python = shutil.which('python3')
    if python is None:
        raise LinuxSetupError('Linux Setup maintenance requires python3 on PATH.')
    command = [python, str(helper), '--repo', str(root)]
    action = request.action
    if action in (LinuxMaintenanceAction.PREPARE_CDN_8443, LinuxMaintenanceAction.RECOVER_CDN, LinuxMaintenanceAction.BUILD_MARKET):
        return [], root
    if action in (LinuxMaintenanceAction.PREVIEW_UPDATE, LinuxMaintenanceAction.UPDATE):
        if request.release_zip is None or not request.release_zip.is_file():
            raise LinuxSetupError('Select the EveJS release ZIP.')
        command += ['update', str(request.release_zip.resolve())]
        command += ['--preview'] if action is LinuxMaintenanceAction.PREVIEW_UPDATE else ['--yes']
    elif action is LinuxMaintenanceAction.LIST_BACKUPS:
        command += ['list']
    elif action in (LinuxMaintenanceAction.RESTORE, LinuxMaintenanceAction.RESTORE_DATA, LinuxMaintenanceAction.RESTORE_PROGRAM):
        command += ['restore']
        if request.backup:
            if (request.backup.startswith('-') or Path(request.backup).name != request.backup
                    or any(ord(char) < 32 for char in request.backup) or '\\' in request.backup):
                raise LinuxSetupError('Select a backup name from this installation.')
            backup = root / '_backup' / request.backup
            if (not backup.is_dir() or backup.is_symlink()
                    or not backup.resolve().is_relative_to(root / '_backup')):
                raise LinuxSetupError('The selected backup is unavailable.')
            command.append(request.backup)
        if action is LinuxMaintenanceAction.RESTORE_DATA:
            command.append('--data-only')
        elif action is LinuxMaintenanceAction.RESTORE_PROGRAM:
            command.append('--program-only')
        command.append('--yes')
    elif action is LinuxMaintenanceAction.PRUNE_BACKUPS:
        if type(request.keep) is not int or not 1 <= request.keep <= 20:
            raise LinuxSetupError('Keep between 1 and 20 backups.')
        command += ['prune', '--keep', str(request.keep), '--yes']
    elif action is LinuxMaintenanceAction.RESEED_MARKET:
        if request.market_profile not in ('v1', 'v2', 'v3', 'v4'):
            raise LinuxSetupError('Select a supported Market seed profile.')
        if request.market_profile == 'v4' and not (root / 'tools/market-seederv4').is_dir():
            raise LinuxSetupError('This EveJS release does not provide Market v4.')
        command += ['reseed-market', request.market_profile, '--yes']
    else:
        raise LinuxSetupError('Unsupported Linux maintenance action.')
    return command, root


def assert_maintenance_stopped(request: LinuxMaintenanceRequest):
    if not request.mutates:
        return
    from .overview_patch import is_eve_client_running
    from .server_launcher import is_server_running
    ports = {26000, 40110, 40111, 5222, 26002, 26003, 26400}
    try:
        data = read_json(request.root / 'config/server.json')
        port = data.get('network', {}).get('serverPort', 26000)
        if type(port) is int and 1 <= port <= 65535:
            ports.add(port)
    except (ValueError, OSError, AttributeError):
        pass  # The pinned updater also performs its own final stopped check.
    if is_eve_client_running() or any(is_server_running(port=p) for p in sorted(ports)):
        raise LinuxSetupError('Stop Game, Market and EVE clients before changing this installation. No processes were stopped automatically.')


def run_maintenance(request: LinuxMaintenanceRequest) -> str:
    from contextlib import ExitStack
    from .linux_client import client_launch_lock
    from .mod_lifecycle_lock import acquire_mod_lifecycle_lease
    from .linux_process import run_bounded
    with ExitStack() as locks:
        if request.mutates:
            locks.enter_context(acquire_mod_lifecycle_lease(request.root))
            locks.enter_context(client_launch_lock(timeout_seconds=1))
        # Revalidate after acquiring both locks, including the final live check.
        command, root = maintenance_command(request)
        assert_maintenance_stopped(request)
        if request.action is LinuxMaintenanceAction.BUILD_MARKET:
            from .linux_market import build_market
            return build_market(LinuxInstallation.load(root))
        if request.action in (LinuxMaintenanceAction.PREPARE_CDN_8443, LinuxMaintenanceAction.RECOVER_CDN):
            values = read_setup_config(root / '.evejs-linux.conf')
            client = Path(values.get('TQ_PATH', ''))
            if not client.is_absolute() or not client.is_dir():
                raise LinuxSetupError('The copied client path is unavailable.')
            locks.enter_context(acquire_mod_lifecycle_lease(client))
            from .linux_cdn import prepare_unprivileged_cdn, recover_cdn_profile
            return (prepare_unprivileged_cdn(root) if request.action is LinuxMaintenanceAction.PREPARE_CDN_8443
                    else recover_cdn_profile(root))
        return run_bounded(command, cwd=root, timeout=3600, limit=2 * 1024 * 1024)


class LinuxSetupMode(str, Enum):
    INSTALL = 'install'
    PREPARE = 'compat'
    PREFIX = 'prefix'


@dataclass(frozen=True)
class LinuxSetupRequest:
    mode: LinuxSetupMode = LinuxSetupMode.INSTALL
    root: Path | None = None


def installer_command(setup_folder: Path, request: LinuxSetupRequest = LinuxSetupRequest()) -> list[str]:
    if not isinstance(request, LinuxSetupRequest) or not isinstance(request.mode, LinuxSetupMode):
        raise LinuxSetupError('Choose a supported Linux Setup operation.')
    setup_folder = verify_setup_bundle(setup_folder)
    helper = setup_folder / 'install-evejs-linux.sh'
    if not helper.is_file() or helper.is_symlink() or file_sha256(helper) != INSTALLER_SHA256:
        raise LinuxSetupError('Select the original Linux Setup v1.20 folder.')
    bash = shutil.which('bash')
    if bash is None:
        raise LinuxSetupError('Linux Setup requires Bash.')
    # Linux Setup owns its interactive Zenity/KDialog wizard. Never pipe a
    # series of blind confirmations into an installation or dependency action.
    command = [bash, str(helper.resolve()), '--no-launch', '--mode', request.mode.value]
    if request.mode is not LinuxSetupMode.INSTALL:
        if request.root is None or not request.root.is_absolute():
            raise LinuxSetupError('Select the existing EveJS installation first.')
        root = request.root.resolve(strict=True)
        if not (root / 'server/index.js').is_file() or not (root / 'config/version.json').is_file():
            raise LinuxSetupError('The selected folder is not an EveJS installation.')
        if (root / '.evejs-linux.conf').exists():
            values = read_setup_config(root / '.evejs-linux.conf')
            recorded = Path(values.get('EVEJS_REPO', ''))
            if not recorded.is_absolute() or recorded.resolve() != root:
                raise LinuxSetupError('The Linux Setup configuration belongs to another installation.')
        command += ['--existing', str(root)]
    elif request.root is not None:
        raise LinuxSetupError('Choose a fresh installation destination in the Linux Setup wizard.')
    return command


def terminal_command(command: list[str]) -> list[str]:
    """Start one interactive wizard with argv intact and a waitable terminal."""
    for terminal, arguments in (
        ('konsole', ['--separate', '--nofork', '-e']),
        ('gnome-terminal', ['--wait', '--']),
        ('xterm', ['-e']),
    ):
        executable = shutil.which(terminal)
        if executable:
            return [executable, *arguments, *command]
    raise LinuxSetupError('Install Konsole, GNOME Terminal or xterm to run the interactive Linux Setup wizard.')


def run_installer(request: LinuxSetupRequest = LinuxSetupRequest()) -> str:
    import os
    from contextlib import ExitStack
    from .linux_client import client_launch_lock
    from .mod_lifecycle_lock import acquire_mod_lifecycle_lease
    from .linux_process import run_bounded
    if not isinstance(request, LinuxSetupRequest) or not isinstance(request.mode, LinuxSetupMode):
        raise LinuxSetupError('Choose a supported Linux Setup operation.')
    if request.root is not None:
        if not request.root.is_absolute():
            raise LinuxSetupError('Select an absolute EveJS installation path.')
        request = replace(request, root=request.root.resolve(strict=True))
    if os.geteuid() == 0:
        raise LinuxSetupError('Run the launcher as your normal desktop user. Linux Setup asks for elevated access only when needed.')
    setup = download_setup_bundle()
    with ExitStack() as locks:
        if request.root is not None:
            locks.enter_context(acquire_mod_lifecycle_lease(request.root))
        locks.enter_context(client_launch_lock(timeout_seconds=1))
        command = terminal_command(installer_command(setup, request))
        # Existing preparation/prefix operations are bound to the captured root;
        # fresh setup is pinned to install mode and owns its destination wizard.
        stopped = LinuxMaintenanceRequest(LinuxMaintenanceAction.UPDATE, request.root or setup)
        assert_maintenance_stopped(stopped)
        if request.root is not None and (request.root / '.evejs-linux.conf').is_file():
            values = read_setup_config(request.root / '.evejs-linux.conf')
            client = Path(values.get('TQ_PATH', ''))
            if client.is_absolute() and client.is_dir():
                locks.enter_context(acquire_mod_lifecycle_lease(client))
        run_bounded(command, cwd=setup, timeout=7200, limit=65536)
    return ('Linux Setup has closed. If setup completed successfully, select its EveJS folder '
            'in Settings to import the prepared client, Proton and runtime paths.')
