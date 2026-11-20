"""Launch a prepared stock client through Proton with named account settings."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import threading
import uuid

from .host import config_directory
from .linux_installation import LinuxInstallation, LinuxSetupError, file_sha256
from .linux_process import ProtonClientProcess, run_bounded

_LOCK = threading.RLock()
_LOCAL = threading.local()
_CHECKED: dict[str, tuple] = {}
_PREFLIGHT_SHA256 = "9d9d244c5fff2cdb6538224c05131ad112f4959a32a480e5abea5bff81761748"


@contextmanager
def _thread_launch_lock(timeout_seconds: float):
    if not _LOCK.acquire(timeout=timeout_seconds):
        raise LinuxSetupError('Another launcher operation is preparing an EVE client; try again.')
    try:
        yield
    finally:
        _LOCK.release()


@contextmanager
def client_launch_lock(timeout_seconds: float = 660):
    """Reentrant within one thread; an advisory user lock across launchers."""
    import fcntl
    import time
    with _thread_launch_lock(timeout_seconds):
        if getattr(_LOCAL, "depth", 0):
            _LOCAL.depth += 1
            try:
                yield
            finally:
                _LOCAL.depth -= 1
            return
        directory = config_directory()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "client-trust.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "r+b") as stream:
            if os.fstat(stream.fileno()).st_uid != os.getuid():
                raise LinuxSetupError("The Linux client lock belongs to another user.")
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LinuxSetupError("Another launcher is preparing an EVE client; try again.") from None
                    time.sleep(0.1)
            _LOCAL.depth = 1
            try:
                yield
            finally:
                _LOCAL.depth = 0
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def wine_path(path: Path) -> str:
    # The normal Proton Z: mapping is verified before using this conversion.
    return "Z:" + str(path.absolute()).replace("/", "\\")


def settings_base(installation: LinuxInstallation, host: str = "127.0.0.1") -> Path:
    if host != "127.0.0.1":
        raise LinuxSetupError("Linux stage 1 requires the prepared IPv4 loopback endpoint.")
    key = wine_path(installation.client).lower().replace(":", "").replace("\\", "_").replace(" ", "_")
    return (installation.prefix / "pfx/drive_c/users/steamuser/AppData/Local/CCP/EVE"
            / (key + "_" + host))


def _ensure_x_authority(env: dict) -> None:
    """KDE Wayland's Xwayland needs XAUTHORITY; if the launcher was started
    without it (terminal/remote shell) Wine cannot open a window."""
    if not env.get("DISPLAY") or env.get("XAUTHORITY"):
        return
    runtime = Path(env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    try:
        candidates = sorted(runtime.glob("xauth_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        candidates += sorted(runtime.glob(".mutter-Xwaylandauth.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        candidates = []
    candidates.append(Path.home() / ".Xauthority")
    for candidate in candidates:
        if candidate.is_file():
            env["XAUTHORITY"] = str(candidate)
            return


def proton_environment(installation: LinuxInstallation, env: dict) -> dict:
    from .host import external_process_environment
    result = external_process_environment(env)
    result.update(STEAM_COMPAT_DATA_PATH=str(installation.prefix),
                  STEAM_COMPAT_CLIENT_INSTALL_PATH=str(installation.steam),
                  STEAM_COMPAT_APP_ID="0", SteamAppId="0", SteamGameId="0", WINEDEBUG="-all")
    ca = installation.root / "_local/certs/xmpp-ca-cert.pem"
    for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        result[name] = wine_path(ca)
    result.pop("SSL_CERT_DIR", None)
    result["EO_REMOTEFILECACHEFOLDER"] = wine_path(installation.cache / "ResFiles")
    result.pop("WINEPREFIX", None)
    _ensure_x_authority(result)
    return result


def require_prefix(installation: LinuxInstallation) -> None:
    prefix = installation.prefix
    with installation.proton.open('rb') as stream:
        script = stream.read(2 * 1024 * 1024).decode('utf-8', errors='replace')
    match = re.search(r'''(?m)^[ \t]*CURRENT_PREFIX_VERSION[ \t]*=[ \t]*["']([^"'\n]+)["']''', script)
    version_file = prefix / "version"

    def check_version():
        if version_file.is_file():
            with version_file.open('rb') as stream:
                content = stream.read(1025)
            existing = content.decode('utf-8', errors='replace').strip()
            selected = match[1].strip() if match is not None else ''
            def version(text):
                found = re.fullmatch(r'(\d+)\.(\d+)(?:-(\d+))?', text)
                return tuple(int(value or 0) for value in found.groups()) if found else None
            old, new = version(existing), version(selected)
            if len(content) <= 1024 and selected and (existing == selected or (old is not None and new is not None and old <= new)):
                return
            raise LinuxSetupError("This prefix was created by a newer/unknown Proton; select a compatible Proton in Linux Setup.")
        if (prefix / 'pfx/drive_c').exists():
            raise LinuxSetupError('This existing prefix has no Proton version record. Select a verified prefix in Linux Setup; it was not rebuilt.')

    check_version()
    mapping = prefix / "pfx/dosdevices/z:"
    if not mapping.exists():
        env = proton_environment(installation, os.environ.copy())
        run_bounded([str(installation.proton), "run", "cmd.exe", "/c", "exit"],
                    cwd=installation.client, env=env, timeout=60)
        check_version()
    if not mapping.is_symlink() or mapping.resolve() != Path("/"):
        raise LinuxSetupError("The selected Proton prefix has no normal Z: drive mapping. Repair it with Linux Setup.")


def require_client_trust(installation: LinuxInstallation) -> None:
    """Verify preparation in place; this stage never rotates a live shared CA."""
    ca = installation.root / "_local/certs/xmpp-ca-cert.pem"
    try:
        expected = ssl.PEM_cert_to_DER_cert(ca.read_text().strip())
        ssl.create_default_context(cafile=str(ca))
        bundles = [installation.client / relative for relative in (
            "bin64/cacert.pem", "bin64/packages/certifi/cacert.pem", "bin/cacert.pem", "bin/packages/certifi/cacert.pem")]
        bundles = [bundle for bundle in bundles if bundle.is_file()]
        if not bundles:
            raise ValueError("no client trust bundles")
        for bundle in bundles:
            blocks = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", bundle.read_text(), re.S)
            if sum(ssl.PEM_cert_to_DER_cert(block) == expected for block in blocks) != 1:
                raise ValueError("client trust differs from the selected CA")
    except (OSError, ValueError, ssl.SSLError) as exc:
        raise LinuxSetupError("The copied client's CA trust is not prepared for this installation. Repair certificates with Linux Setup after closing EVE.") from exc


def local_ca_context(ca: Path) -> ssl.SSLContext:
    """Trust only the local EveJS CA. Python 3.13+ turns on strict X.509 checks by
    default, and the locally generated cert has no Authority Key Identifier, so
    strict mode wrongly rejects it. Chain and hostname checks stay on."""
    context = ssl.create_default_context(cafile=str(ca))
    if hasattr(context, 'verify_flags'):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def require_backend_tls(installation: LinuxInstallation, game_port: int) -> None:
    from .linux_services import http_health
    if not http_health(40110) or not http_health(26002):
        raise LinuxSetupError("The prepared Linux Market/proxy health endpoint is unavailable.")
    ca = installation.root / "_local/certs/xmpp-ca-cert.pem"
    context = local_ca_context(ca)
    for port, host in ((5222, "localhost"), (26003, "dev-public-gateway.evetech.net"), (installation.cdn_port, "localhost")):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
                with context.wrap_socket(connection, server_hostname=host):
                    pass
        except (OSError, ssl.SSLError) as exc:
            raise LinuxSetupError(f"The prepared Linux TLS service on port {port} is unavailable or has the wrong certificate.") from exc
    for port in (game_port, 26001, 26002, 26400, 40110, 40111):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                pass
        except OSError as exc:
            raise LinuxSetupError(f"The prepared Linux service on port {port} is unavailable.") from exc


def require_client_patches(installation: LinuxInstallation) -> None:
    helper = installation.root / ".evejs-linux-tools/evejs-linux-client-preflight.py"
    if not helper.is_file():
        helper = installation.root / "evejs-linux-client-preflight.py"
    if not helper.is_file() or file_sha256(helper) != _PREFLIGHT_SHA256:
        raise LinuxSetupError("The supported Linux Setup client preflight helper is missing or unrecognized. Prepare runtime compatibility again.")
    # The helper refuses checks while EVE runs. Later multibox launches may reuse
    # a successful read-only check only while every relevant input is unchanged.
    # Recheck the helper's semantic INI identity on every launch. Endpoint-only
    # edits performed by the launcher do not require re-running its stopped-
    # client check; a changed build/server/crypto identity always fails closed.
    ini = installation.client / 'start.ini'
    if ini.is_symlink() or not ini.is_file():
        raise LinuxSetupError('The copied client requires an ordinary start.ini.')
    with ini.open('rb') as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise LinuxSetupError('The copied client identity file is too large.')
    identity = {}
    for line in content.decode('utf-8', errors='replace').splitlines():
        if '=' not in line:
            continue
        name, value = (part.strip() for part in line.split('=', 1))
        name = name.lower()
        if name in ('server', 'serverip', 'cryptopack', 'build'):
            name = 'server' if name == 'serverip' else name
            if name in identity:
                raise LinuxSetupError('The copied client has a duplicate identity field.')
            identity[name] = value
    if identity != {'server': '127.0.0.1', 'cryptopack': 'Placebo', 'build': str(installation.build)}:
        raise LinuxSetupError('The copied client identity must use the supported build, IPv4 loopback and Placebo crypto.')
    paths = [helper]
    for name in ('exefile.exe', 'launchdarkly_stackless_client_sdk.pyd'):
        path = installation.client / 'bin64' / name
        if path.is_symlink() or not path.is_file():
            raise LinuxSetupError('The copied client network binaries are missing or redirected.')
        paths.append(path)
    setup = installation.root / 'tools/ClientSETUP'
    from .linux_installation import read_json
    for name, target in (
        ('launchdarkly_patch_recipe.json', 'launchdarkly_stackless_client_sdk.pyd'),
        ('launchdarkly_localhost_upgrade_recipe.json', 'launchdarkly_stackless_client_sdk.pyd'),
        ('crash_upload_patch_recipe.json', 'exefile.exe'),
    ):
        recipe = setup / name
        if read_json(recipe).get('source', {}).get('filename') != target:
            raise LinuxSetupError('The client network recipes do not match the supported checked binaries.')
        paths.append(recipe)
    paths.extend(sorted((installation.client / "bin64").glob("*.dll")))
    signature = tuple((str(path), file_sha256(path)) for path in paths) + (('start.ini.identity', tuple(sorted(identity.items()))),)
    key = str(installation.root) + ":" + str(installation.client) + ":" + str(installation.cdn_port)
    if _CHECKED.get(key) == signature:
        return
    python = shutil.which("python3")
    if python is None:
        raise LinuxSetupError("Linux Setup's client preflight requires python3 on PATH.")
    run_bounded([python, str(helper), "check", "--repo", str(installation.root), "--tq", str(installation.client),
                 "--build", str(installation.build), "--cdn-port", str(installation.cdn_port)],
                cwd=installation.root, timeout=60)
    _CHECKED[key] = signature


def account_profile_name(installation: LinuxInstallation, username: str, scope: str = "") -> str:
    identity = hashlib.sha256((str(installation.root) + "\0" + username + ("\0" + scope if scope else "")).encode()).hexdigest()[:24]
    return "EveJSLauncher_" + identity


def prepare_account_settings(installation: LinuxInstallation, username: str, scope: str = "") -> tuple[Path, str]:
    from .profiles import prefill_username
    from .shared_client_settings import require_shared_settings_support
    require_shared_settings_support(installation.client)
    profile = account_profile_name(installation, username, scope)
    base = settings_base(installation)
    folder = base / ("settings_" + profile)
    if folder.is_symlink():
        raise LinuxSetupError("The account settings folder must not be redirected.")
    folder.mkdir(parents=True, exist_ok=True)
    templates = Path(__file__).parent / "template_settings"
    for name in ("prefs.ini", "core_public__.yaml"):
        destination = folder / name
        if destination.is_symlink():
            raise LinuxSetupError("Account settings files must not be redirected.")
        source = base / "settings" / name
        if not source.is_file():
            source = templates / name
        if not destination.exists():
            shutil.copy2(source, destination)
    prefill_username(username, settings_dir=folder, update_username=True)
    return folder, "/settingsprofile=" + profile


def perform_linux_client_launch(request):
    from .launcher import build_env, _resolve_client_resource_cache, wait_for_client_endpoints
    from .profiles import configure_profile_game_endpoint
    docker = request.launch_context.target_identity is not None
    backend = "docker" if docker else "native"
    setup_root = (request.linux_setup_root or request.evejs_root) if docker else request.evejs_root
    if request.client_settings_mode not in ('separate', 'shared'):
        raise LinuxSetupError('The Linux account settings mode is invalid.')
    installation = LinuxInstallation.load(setup_root)
    if Path(request.client_path).resolve() != installation.client:
        raise LinuxSetupError("Select the copied client recorded by this installation's Linux Setup.")
    if not docker:
        port, proxy = installation.client_endpoints()
        if (request.launch_context.game_host != "127.0.0.1" or request.launch_context.game_port != port
                or request.launch_context.proxy_url != proxy):
            raise LinuxSetupError("The launch endpoints differ from Linux Setup. Import this installation again in Settings.")
        installation.require_supported_mods()
        installation.verified_market_binary()

    def check_backend():
        if docker:
            from .linux_docker_client import require_docker_runtime
            require_docker_runtime(request, installation)
            if request.require_account_creation:
                from .runtime.docker_autologin import require_docker_account_creation
                require_docker_account_creation(request.docker_auto_login_probe, request.launch_context)
        else:
            require_backend_tls(installation, request.launch_context.game_port)
    _resolve_client_resource_cache(installation.client, str(installation.cache / "tq"))
    wait_for_client_endpoints(request.launch_context)
    from .linux_profiles import bind_installation, enroll_profile
    from .profiles import create_profile, prefill_username
    from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
    from contextlib import ExitStack
    scope = request.launch_context.target_identity or ""
    roots = {installation.root, installation.client}
    if docker:
        roots.add(Path(request.evejs_root).resolve(strict=True))
    with client_launch_lock(), ExitStack() as locks, bind_installation(installation, settings_scope=scope):
        for root in sorted(roots):
            locks.enter_context(acquire_mod_lifecycle_lock(root))
        if LinuxInstallation.load(setup_root) != installation:
            raise LinuxSetupError('The Linux runtime selection changed during launch; try again.')
        require_client_trust(installation)
        check_backend()
        require_client_patches(installation)
        require_prefix(installation)
        profile_dir = create_profile(request.username, str(installation.client), request.profiles_root, bootstrap_settings=False)
        enroll_profile(profile_dir, installation, request.username, settings_scope=scope)
        shared = request.client_settings_mode == 'shared' and request.account_id is not None and request.character_id is not None
        if shared:
            from .shared_settings_profile import prepare_shared_profile_launch
            shared_settings = prepare_shared_profile_launch(profile_dir / 'tq', request.shared_settings_source,
                account_id=request.account_id, character_id=request.character_id, evejs_root=request.evejs_root,
                host=request.launch_context.game_host, target_identity=request.launch_context.target_identity, legacy_folder=request.shared_settings_folder)
            settings, option = shared_settings.folder, shared_settings.argument
            prefill_username(request.username, settings_dir=settings, update_username=True)
        else:
            settings, option = prepare_account_settings(installation, request.username, scope)
        configure_profile_game_endpoint(request.username, installation.client, host=request.launch_context.game_host,
                                        port=request.launch_context.game_port, settings_dir=settings, write_preferences=not shared)
        env = proton_environment(installation, build_env(str(installation.root), request.launch_context.proxy_url))
        # EVE settings stay stable, while each process receives a unique alias.
        # A second launcher can start the same account without sharing shutdown
        # authority merely because the account settings name is identical.
        launch_profile = 'EveJSLauncher_run_' + uuid.uuid4().hex
        option = '/settingsprofile=' + launch_profile
        alias = settings_base(installation) / ('settings_' + launch_profile)
        arguments = (option, f"/server:{request.launch_context.game_host}", f"/port:{request.launch_context.game_port}")
        if request.auto_login_enabled and (request.character_id is not None or not request.character_name):
            from .client_autologin import AutoLoginLaunch, inspect_auto_login_capability, inspect_client_auto_login_capability, require_auto_login_arguments
            verified = False
            if docker:
                from .runtime.docker_autologin import inspect_docker_auto_login_capability
                supported = inspect_client_auto_login_capability(request.client_path).supported
                if supported:
                    verified = inspect_docker_auto_login_capability(request.docker_auto_login_probe, request.launch_context).enabled
                supported = supported and verified
            else:
                supported = inspect_auto_login_capability(request.evejs_root, request.client_path).supported
            if supported:
                arguments += require_auto_login_arguments(AutoLoginLaunch(request.username, request.character_id),
                    evejs_root=request.evejs_root, client_path=request.client_path, game_host=request.launch_context.game_host,
                    server_password_bypass_verified=verified)
        exe = installation.client / "bin64/exefile.exe"
        from .client_autologin import NO_CONSOLE_ARGUMENT, supports_client_no_console
        if request.hide_console and NO_CONSOLE_ARGUMENT not in arguments and supports_client_no_console(exe):
            arguments += (NO_CONSOLE_ARGUMENT,)
        if request.overview_bridge is not None:
            env['EVEJS_OVERVIEW_BRIDGE'] = request.overview_bridge.command
            env['EVEJS_OVERVIEW_ACK_PATH'] = wine_path(request.overview_bridge.ack_path)
        from .mod_api_runtime import prepare_public_client_mods
        from .mod_client_notifications import start_client_notifications
        public_mods = prepare_public_client_mods(request.evejs_root, installation.client, profile_dir / 'tq',
            backend=backend, protected_environment=env, protected_arguments=arguments, client_lock_held=True,
            profile_settings_root=settings,
            **({'docker_operation': request.docker_mod_operation} if request.docker_mod_operation is not None else {}))
        env.update(public_mods.environment)
        arguments += public_mods.arguments
        # Preserve GUI stdout/stderr inheritance. Never kill the prefix's shared wineserver.
        alias_created = False
        try:
            from .client_renderer import configure_client_renderer
            mod_environment = {key.upper(): value for key, value in public_mods.environment.items()}
            configure_client_renderer(
                request.evejs_root, exe.parent, env,
                override=mod_environment.get('TRINITYPLATFORM'),
            )
            wait_for_client_endpoints(request.launch_context)
            check_backend()
            alias.symlink_to(settings.resolve(strict=True), target_is_directory=True)
            alias_created = True
            raw = subprocess.Popen([str(installation.proton), "run", str(exe), *arguments],
                                   cwd=str(installation.client), env=env, start_new_session=True)
            process = ProtonClientProcess(raw, option, exe)
            process.settings_alias = (alias, settings.resolve(strict=True))
        except Exception as exc:
            if alias_created and alias.is_symlink() and alias.resolve() == settings.resolve():
                alias.unlink()
            start_client_notifications(public_mods, backend=backend, error_type=type(exc).__name__)
            raise
        start_client_notifications(public_mods, backend=backend, process=process)
        return process
