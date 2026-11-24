"""Disposable real EveJS service/persistence check; never starts an EVE client.

Run in the dedicated Linux QA container with release/SDE/client inputs mounted
read-only. Only the compact report/logs are written to the project QA folder.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


def unzip(source, destination):
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            name = PurePosixPath(entry.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in entry.filename:
                raise ValueError('Archive path escapes the disposable world')
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unexpected archive symlink')
        archive.extractall(destination)


def run(command, cwd, timeout=600):
    print('prepare:', Path(command[0]).name, ' '.join(str(value) for value in command[1:3]), flush=True)
    completed = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=timeout)
    if completed.returncode:
        raise RuntimeError(completed.stdout[-6000:])
    print(completed.stdout[-800:], flush=True)


def wait_health(root, port, timeout=300):
    from src.core.linux_services import observe_backend
    deadline = time.monotonic() + timeout
    next_sample = 0
    while time.monotonic() < deadline:
        game, market = observe_backend(str(root), port)
        if game and market:
            return
        if time.monotonic() >= next_sample:
            memory = Path('/sys/fs/cgroup/memory.current')
            events = Path('/sys/fs/cgroup/memory.events')
            print('readiness:', game, market, 'memory:', memory.read_text().strip() if memory.exists() else '?',
                  events.read_text().strip().replace('\n', ';') if events.exists() else '', flush=True)
            next_sample = time.monotonic() + 15
        time.sleep(0.5)
    raise RuntimeError('Real Linux stack did not become semantically ready')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--sde', type=Path, required=True)
    parser.add_argument('--setup', type=Path, required=True)
    parser.add_argument('--client-cache', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--maintenance', action='store_true', help='Also exercise update, backups, restores, reseeding and pruning')
    parser.add_argument('--build-market', action='store_true', help='Compile and use native Market through the launcher Cargo provider')
    args = parser.parse_args()
    from src.core.linux_installation import file_sha256
    critical = [*sorted((PROJECT / 'src/core').glob('linux*.py')),
                *(PROJECT / path for path in ('src/core/server_launcher.py', 'src/core/native_runtime.py',
                    'src/core/native_maintenance.py', 'src/core/character_creation.py', 'src/core/character_deletion.py'))]
    source_hashes = {str(path.relative_to(PROJECT)): file_sha256(path) for path in critical}
    if file_sha256(args.release) != '373266a05fd9acf0c220d06d5469907637a6d09bf2cf926adf4aaee97a3ccc59':
        raise ValueError('Unexpected official release bytes')
    report = {'protocol': 'evejs_linux_real_stack_v1', 'status': 'preparing', 'clientLaunched': False,
              'protonExecutable': 'non-executed fixture placeholder', 'hostPortsPublished': False,
              'sourceSha256AtStart': source_hashes}
    game = market = None
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    with tempfile.TemporaryDirectory(prefix='evejs-real-linux-') as temporary:
        work = Path(temporary)
        root = work / 'stock world'
        root.mkdir()
        try:
            unzip(args.release, root)
            payload = args.setup / 'portable/steamos-x86_64/payload.tar.xz'
            if file_sha256(payload) != '9d05bbff9d087ef9b0036f3625bc8a531e65cbfc7e4eb0d715c69844ee5206ed':
                raise ValueError('Unexpected portable payload bytes')
            with tarfile.open(payload) as archive:
                archive.extractall(root, filter='data')
            sde = work / 'sde'
            sde.mkdir()
            unzip(args.sde, sde)
            run(['node', '--max-old-space-size=4096', str(root / 'tools/DatabaseCreator/database-creator.js'),
                 '--sde-dir', str(sde), '--out', str(root / '_local/gameStore/data'), '--build', '3396210',
                 '--sde-url', 'https://developers.eveonline.com/static-data/tranquility/eve-online-static-data-3396210-jsonl.zip', '--force'], root)
            certs = root / '_local/certs'
            certs.mkdir(parents=True, exist_ok=True)
            builder = root / 'tools/ClientSETUP/scripts/build-gateway-cert.js'
            ca, ca_key = certs / 'xmpp-ca-cert.pem', certs / 'xmpp-ca-key.pem'
            dns = 'localhost,dev-public-gateway.evetech.net,public-gateway.evetech.net,app.launchdarkly.com,clientstream.launchdarkly.com,clientsdk.launchdarkly.com,events.launchdarkly.com,mobile.launchdarkly.com,sdk.launchdarkly.com,stream.launchdarkly.com,sentry.io'
            for name, names, extra in [('xmpp-dev', 'localhost', []), ('gateway-dev', dns, []), ('gateway-dns', dns, ['--dns-only'])]:
                run(['node', str(builder), '--ensure-ca', '--ca-cert', str(ca), '--ca-key', str(ca_key),
                     '--dns', names, *([] if extra else ['--ip', '127.0.0.1']), '--out-cert', str(certs / (name + '-cert.pem')),
                     '--out-key', str(certs / (name + '-key.pem')), *extra], root)
            # Services need trust/executable identity, not a complete second
            # client tree. Gameplay is explicitly outside this harness.
            cache = work / 'copied-client'
            cache.mkdir()
            client = cache / 'tq'
            for relative in ('start.ini', 'code.ccp', 'bin64/exefile.exe', 'bin64/cacert.pem',
                             'bin64/packages/certifi/cacert.pem', 'bin64/launchdarkly_stackless_client_sdk.pyd'):
                source = args.client_cache / 'tq' / relative
                if source.is_file():
                    destination = client / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            (cache / 'ResFiles').symlink_to(args.client_cache / 'ResFiles', target_is_directory=True)
            shutil.copy2(args.client_cache / 'index_tranquility.txt', cache / 'index_tranquility.txt')
            for relative in ('bin64/cacert.pem', 'bin64/packages/certifi/cacert.pem'):
                bundle = client / relative
                if bundle.is_file():
                    bundle.write_bytes(bundle.read_bytes().rstrip() + b'\n' + ca.read_bytes())
            prefix, steam = work / 'prefix', work / 'Steam'
            prefix.mkdir(); steam.mkdir()
            proton = work / 'proton'
            proton.write_text('#!/bin/sh\nexit 99\n')
            proton.chmod(0o755)
            tools = root / '.evejs-linux-tools'
            tools.mkdir()
            for name in ('evejs-linux-lu-compat.py', 'evejs-linux-client-preflight.py', 'evejs-linux-update.py'):
                shutil.copy2(args.setup / name, tools / name)
            shutil.copytree(args.setup / 'compatibility', tools / 'compatibility')
            config = {'EVEJS_REPO': str(root), 'EVEJS_VERSION': '261108.1', 'SUPPORTED_BUILD': '3396210',
                      'TQ_PATH': str(client), 'CACHE_ROOT': str(cache), 'NODE_BIN': shutil.which('node'),
                      'PROTON_BIN': str(proton), 'PROTON_PREFIX': str(prefix), 'STEAM_ROOT': str(steam),
                      'EVEJS_CDN_PORT': '443', 'EVEJS_PLATFORM_MODE': 'standard-linux'}
            (root / '.evejs-linux.conf').write_text('\n'.join(f'{key}={shlex.quote(value)}' for key, value in config.items()) + '\n')
            seed = root / 'tools/market-seed'
            run([str(seed / 'target/release/market-seed'), '--config', 'config/market-seed.local.toml',
                 'build', '--force', '--preset', 'jita_new_caldari'], seed)
            run(['python3', str(tools / 'evejs-linux-lu-compat.py'), 'prepare', '--repo', str(root),
                 '--candidate-binary', str(root / 'externalservices/market-server/target/release/market-server')], root)
            from src.core.linux_maintenance import LinuxMaintenanceAction as Action, LinuxMaintenanceRequest, run_maintenance
            run_maintenance(LinuxMaintenanceRequest(Action.PREPARE_CDN_8443, root))
            report['actualClientCdn8443Preparation'] = True
            from src.core.linux_client import require_client_patches
            from src.core.linux_installation import LinuxInstallation
            require_client_patches(LinuxInstallation.load(root))
            report['actualClientPatchPreflight'] = True
            before_preview = file_sha256(root / '.evejs-linux.conf')
            run_maintenance(LinuxMaintenanceRequest(Action.PREVIEW_UPDATE, root, release_zip=args.release))
            assert file_sha256(root / '.evejs-linux.conf') == before_preview
            run_maintenance(LinuxMaintenanceRequest(Action.LIST_BACKUPS, root))
            report['maintenancePreviewAndInventory'] = True
            if args.build_market:
                run_maintenance(LinuxMaintenanceRequest(Action.BUILD_MARKET, root))
                from src.core.linux_installation import LinuxInstallation
                binary = LinuxInstallation.load(root).verified_market_binary()
                assert 'launcher-market-builds' in binary.parts
                report['actualCargoMarketBuild'] = {'binarySha256': file_sha256(binary), 'sourceDigest': LinuxInstallation.load(root).market_source_identity()[1]}
            from src.core.linux_services import start_game, start_market
            report['status'] = 'starting'
            market = start_market(str(root))
            game = start_game(str(root), 'vanilla')
            wait_health(root, 26000)
            report['initialBoot'] = True
            game.send_group_signal(__import__('signal').SIGINT)
            report['firstGameExit'] = game.wait(timeout=125)
            market.terminate(); report['firstMarketExit'] = market.wait(timeout=20)
            game = market = None
            from src.core.character_creation import CharacterCreationRequest, create_character
            result = create_character(CharacterCreationRequest(str(root), 'linux-fixture-account', 'Linux Fixture Pilot', False))
            report['createdCharacterID'] = result.character_id
            report['createdAccountID'] = result.account_id
            # The launcher creates a new account with its first character. Use
            # EveJS's own character service to seed a second fixture character
            # on that account, as the real client's creation flow does.
            fixture_helper = work / 'append-fixture-character.js'
            maintenance_helper = PROJECT / 'src/core/helpers/game_store_maintenance.js'
            terminal_helper = PROJECT / 'src/core/helpers/terminal_result.js'
            fixture_helper.write_text('''
process.env.EVEJS_GAMESTORE_OWNER_ROLE = 'maintenance';
const path = require('path');
const {runMaintenanceOperation, failureResult} = require(MAINTENANCE);
const {exitWithTerminalResult} = require(TERMINAL);
const database = require(path.join(process.cwd(), 'server/src/gameStore'));
runMaintenanceOperation(database, 'linux-qa-second-character', () => {}, () => {
    const Service = require(path.join(process.cwd(), 'server/src/services/character/charService'));
    const id = new Service().Handle_CreateCharacterWithDoll(
        ['Linux Fixture Second', 1, 1, 1, null, null, 11, 1], {userid: ACCOUNT});
    return {ok:true, characterId:Number(id)};
}).then(value => exitWithTerminalResult(value, 0), error => exitWithTerminalResult(failureResult(error), 1));
'''.replace('MAINTENANCE', json.dumps(str(maintenance_helper)))
   .replace('TERMINAL', json.dumps(str(terminal_helper))).replace('ACCOUNT', str(result.account_id)))
            fixture_env = dict(os.environ, EVEJS_GAMESTORE_SQLITE_PATH=str(root / '_local/gameStore/gamestore.sqlite'),
                EVEJS_GAMESTORE_DATA_DIR=str(root / '_local/gameStore/data'), EVEJS_GAMESTORE_OWNER_ROLE='maintenance')
            fixture_result = subprocess.run(['node', str(fixture_helper)], cwd=root, env=fixture_env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
            if fixture_result.returncode:
                raise RuntimeError('Additional character fixture failed: ' + fixture_result.stdout[-3000:])
            record = next(line.removeprefix('EVEJS_LAUNCHER_RESULT=') for line in reversed(fixture_result.stdout.splitlines())
                          if line.startswith('EVEJS_LAUNCHER_RESULT='))
            second_id = json.loads(record)['characterId']
            loader = root / 'mods/linux-fixture/loader.js'
            loader.parent.mkdir(parents=True)
            loader.write_text('process.stdout.write("EVEJS_LINUX_FIXTURE_PRELOAD=ready\\n");\n')
            from src.core.mod_manifest import scan_mods
            from src.core.mod_runtime_state import build_mod_runtime_plan
            mods = scan_mods(root)
            plan = build_mod_runtime_plan(root, mods, backend='native', mode='modded',
                runtime_identity='linux-real-fixture-restart', selected_loader_ids=[mod.id for mod in mods if mod.active])
            market = start_market(str(root)); game = start_game(str(root), 'modded', mod_runtime_plan=plan)
            wait_health(root, 26000)
            from src.core.db import load_accounts
            accounts = load_accounts(str(root))
            account = next(item for item in accounts if item.username == 'linux-fixture-account')
            assert any(item.char_id == result.character_id for item in account.characters)
            report['characterVisibleAfterRestart'] = True
            evidence = game.mod_runtime_receipt.status_log_path
            deadline = time.monotonic() + 3
            while 'EVEJS_LINUX_FIXTURE_PRELOAD=ready' not in evidence.read_text(errors='replace'):
                if time.monotonic() > deadline:
                    raise RuntimeError('The portable loader did not appear in the child-only runtime evidence')
                time.sleep(0.05)
            report['frozenPortableLoaderAndConsoleEvidence'] = True
            with sqlite3.connect(root / '_local/gameStore/gamestore.sqlite') as database:
                assert database.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
            report['restartAndSQLiteIntegrity'] = True
            game.send_group_signal(__import__('signal').SIGINT)
            report['secondGameExit'] = game.wait(timeout=125)
            market.terminate(); report['secondMarketExit'] = market.wait(timeout=20)
            game = market = None
            from src.core.character_deletion import CharacterDeletionRequest, CharacterDeletionScope, delete_character_or_account
            delete_character_or_account(CharacterDeletionRequest(str(root), result.request.username,
                result.account_id, result.character_id, result.request.character_name, CharacterDeletionScope.CHARACTER))
            account = next(item for item in load_accounts(str(root)) if item.username == result.request.username)
            assert [item.char_id for item in account.characters] == [second_id]
            report['offlineCharacterDeletion'] = True
            delete_character_or_account(CharacterDeletionRequest(str(root), result.request.username,
                result.account_id, second_id, 'Linux Fixture Second', CharacterDeletionScope.ACCOUNT))
            assert not any(item.username == result.request.username for item in load_accounts(str(root)))
            report['offlineAccountDeletion'] = True
            if args.maintenance:
                loader.unlink()  # Remove this test's portable fixture before stock Setup maintenance.
                retained = create_character(CharacterCreationRequest(str(root), 'maintenance-fixture', 'Maintenance Fixture Pilot', False))
                original_program = file_sha256(root / 'server/index.js')
                output = run_maintenance(LinuxMaintenanceRequest(Action.UPDATE, root, release_zip=args.release))
                backups = [path for path in (root / '_backup').iterdir()
                           if path.is_dir() and (path / 'manifest.json').is_file() and path.name.startswith('pre-update-')]
                assert len(backups) == 1, 'Expected exactly one new update backup'
                backup = backups[0].name
                account = next(item for item in load_accounts(str(root)) if item.username == retained.request.username)
                assert [item.char_id for item in account.characters] == [retained.character_id]
                report['actualEvejsUpdateAndPreservedCharacter'] = True
                delete_character_or_account(CharacterDeletionRequest(str(root), retained.request.username,
                    retained.account_id, retained.character_id, retained.request.character_name, CharacterDeletionScope.ACCOUNT))
                run_maintenance(LinuxMaintenanceRequest(Action.RESTORE_DATA, root, backup=backup))
                account = next(item for item in load_accounts(str(root)) if item.username == retained.request.username)
                assert [item.char_id for item in account.characters] == [retained.character_id]
                report['actualBackupDataRestore'] = True
                (root / 'server/index.js').write_bytes((root / 'server/index.js').read_bytes() + b'\n// disposable QA program edit\n')
                time.sleep(1.05)  # The upstream backup names have one-second resolution.
                run_maintenance(LinuxMaintenanceRequest(Action.RESTORE_PROGRAM, root, backup=backup))
                assert file_sha256(root / 'server/index.js') == original_program
                report['actualProgramRestore'] = True
                time.sleep(1.05)
                run_maintenance(LinuxMaintenanceRequest(Action.RESEED_MARKET, root, market_profile='v1'))
                report['actualMarketReseed'] = True
                run_maintenance(LinuxMaintenanceRequest(Action.PRUNE_BACKUPS, root, keep=1))
                remaining = [path for path in (root / '_backup').iterdir()
                             if path.is_dir() and (path / 'manifest.json').is_file() and not path.name.startswith('.')]
                assert len(remaining) == 1
                report['actualBackupPrune'] = True
                with sqlite3.connect(root / '_local/gameStore/gamestore.sqlite') as database:
                    assert database.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
            report['status'] = 'passed'
        except BaseException as exc:
            report['status'] = 'failed'
            report['error'] = str(exc)[-6000:]
            from src.core.server_launcher import get_server_console_log, get_market_console_log
            for name, path in [('gameConsole', get_server_console_log(root)),
                               ('marketConsole', get_market_console_log(root))]:
                if path.is_file():
                    with path.open('rb') as stream:
                        stream.seek(max(0, path.stat().st_size - 10000))
                        tail = stream.read().decode('utf-8', errors='replace')
                    report[name] = tail
                    print(name + ': ' + tail, flush=True)
            raise
        finally:
            for process in (game, market):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
            report['sourceChangedDuringRun'] = [name for name, digest in source_hashes.items()
                if not (PROJECT / name).is_file() or file_sha256(PROJECT / name) != digest]
            args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
