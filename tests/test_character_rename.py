"""Character rename guards and real, explicitly staged EveJS storage checks."""
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import sqlite3
import subprocess
import time

import pytest

from src.core import character_rename as rename
from src.core.runtime.data import docker_project_identity
from src.core.runtime.docker_cli import DockerCommandRunner
from src.core.runtime.docker_compose import ComposeTarget, parse_compose_config


def _request(root, **kwargs):
    return replace(
        rename.CharacterRenameRequest(root, {}, "qa-account", 504, 701, "Repeated Pilot", "New Pilot"),
        **kwargs,
    )


def _metadata(root, version="261115.2"):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "version.json").write_text(
        json.dumps({"evejsVersion": version, "configSchemaVersion": 1}), encoding="utf-8",
    )


def test_unsupported_release_and_bad_identity_fail_before_backup(tmp_path):
    _metadata(tmp_path, "261108.1")
    with pytest.raises(rename.CharacterRenameError, match="reviewed EveJS"):
        rename.rename_character(_request(tmp_path))
    with pytest.raises(rename.CharacterRenameError, match="request is invalid"):
        rename.rename_character(_request(tmp_path, character_id=True))
    assert not (tmp_path / "_local").exists()


def test_native_active_port_blocks_rename(tmp_path):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        storage = rename.SaveStorage(tmp_path, {}, "node", (server.getsockname()[1],))
        with pytest.raises(rename.CharacterRenameError, match="Stop Game and Market"):
            rename._require_native_offline(storage, tmp_path)


def _project(root, mode="volume"):
    config = {
        "name": "rename-qa",
        "services": {
            "init": {},
            "market": {"ports": ["127.0.0.1:40110:40110"]},
            "server": {
                "image": "reviewed-test-image", "pull_policy": "never",
                "ports": [f"127.0.0.1:{port}:{port}" for port in (26000, 26001, 26002, 26003, 5222)],
                "volumes": [{"type": "volume", "source": "save", "target": "/var/lib/evejs"}],
            },
        },
        "volumes": {"save": {"name": "rename-qa-save"}},
    }
    if mode == "bind":
        store = root / "save copy"
        store.mkdir()
        (store / "gamestore.sqlite").write_bytes(b"qa")
        config["services"]["server"]["volumes"].append(
            {"type": "bind", "source": str(store), "target": "/var/lib/evejs/gameStore"},
        )
    return config


def _target(root, document):
    file = root / "compose.yaml"
    file.write_text("name: rename-qa", encoding="utf-8")
    target = ComposeTarget(file, root)
    config = parse_compose_config(document)
    return target, docker_project_identity(target, config.project_name, config=config)


@pytest.mark.parametrize("mode,drift", [("bind", None), ("volume", None), ("volume", "image"), ("bind", "container")])
def test_docker_uses_only_the_verified_authoritative_save_mount(tmp_path, monkeypatch, mode, drift):
    _metadata(tmp_path)
    document = _project(tmp_path, mode)
    target, identity = _target(tmp_path, document)
    helper_commands = []
    observations = []
    endpoint = "unix:///fixture/engine"
    commands = []
    images = []

    def completed(argv, stdout):
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    def execute(argv, cwd, _timeout):
        commands.append(argv)
        assert cwd == tmp_path
        if argv[1:3] == ("context", "inspect"):
            return completed(argv, endpoint)
        if argv[1] == "version":
            return completed(argv, "linux")
        assert argv[1:3] == ("--host", endpoint)
        action = argv[3:]
        if action[:2] == ("volume", "inspect"):
            return completed(argv, json.dumps([{
                "Name": "rename-qa-save", "Driver": "local",
                "Labels": {"com.docker.compose.project": "rename-qa"},
                "Mountpoint": "/fixture", "CreatedAt": "test timestamp",
            }]))
        if action[:2] == ("image", "inspect"):
            image = "sha256:" + ("f" if drift == "image" and images else "e") * 64
            images.append(image)
            return completed(argv, image)
        if action[0] == "ps":
            return completed(argv, "other-container" if drift == "container" and action == ("ps", "-q") else "")
        if action[:2] == ("inspect", "other-container"):
            source = tmp_path / "save copy"
            if os.name == "nt":
                source = "/run/desktop/mnt/host/" + source.drive[0].lower() + "/" + "/".join(source.parts[1:])
            return completed(argv, json.dumps([{"Mounts": [{"Type": "bind", "Source": str(source)}]}]))
        assert action[0] == "compose"
        observations.append(action)
        return completed(argv, json.dumps(document))

    def execute_input(argv, cwd, _timeout, input_bytes):
        assert cwd == tmp_path and argv[1:3] == ("--host", endpoint)
        helper_commands.append(argv[3:])
        request = json.loads(input_bytes)
        result = {
            "ok": True, "operationId": request["operationId"], "characterId": 701,
            "name": "New Pilot", "changed": True, "backupCreated": True,
        }
        return completed(argv, rename._RESULT_PREFIX + json.dumps(result))

    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(rename, "DockerCommandRunner", lambda: DockerCommandRunner(
        "docker", execute=execute, execute_input=execute_input,
    ))
    request = _request(tmp_path, compose_target=target, target_identity=identity,
                       settings={"runtime_backend": "docker_compose", "docker_control_policy": "managed"})
    if drift:
        message = "changed" if drift == "image" else "container is using"
        with pytest.raises(rename.CharacterRenameError, match=message):
            rename.rename_character(request)
        assert not helper_commands
        return
    result = rename.rename_character(request)
    assert result.character_id == 701 and result.changed and result.name == "New Pilot"
    assert len(observations) == 2
    (argv,) = helper_commands
    assert argv[:7] == ("run", "--rm", "-i", "--pull", "never", "--network", "none")
    assert "--read-only" in argv and "NODE_OPTIONS=" in argv
    assert "sha256:" + "e" * 64 in argv
    mount_arguments = [argv[index + 1] for index, value in enumerate(argv) if value == "--mount"]
    assert len(mount_arguments) == 2
    assert "target=/run/evejs-launcher/helpers" in mount_arguments[1] and "readonly" in mount_arguments[1]
    if mode == "bind":
        assert "target=/var/lib/evejs/gameStore" in mount_arguments[0]
        assert str(tmp_path / "save copy") in mount_arguments[0]
        assert isinstance(result.backup_path, Path) and result.backup_path.is_relative_to(tmp_path / "save copy")
    else:
        assert "source=rename-qa-save" in mount_arguments[0]
        assert "volume-nocopy" in mount_arguments[0]
        assert str(result.backup_path).startswith("rename-qa-save:gameStore/.launcher-character-renames/")


def test_docker_rejects_changed_authority_and_unreviewed_bind(tmp_path):
    raw = _project(tmp_path)
    target, identity = _target(tmp_path, raw)
    raw["services"]["server"]["volumes"].append(
        {"type": "bind", "source": str(tmp_path), "target": "/app/server/src/gameStore"},
    )
    with pytest.raises(ValueError, match="identity changed"):
        rename._compose_projection(json.dumps(raw), target, identity, tmp_path)
    _, new_identity = _target(tmp_path, raw)
    with pytest.raises(ValueError, match="overrides reviewed files"):
        rename._compose_projection(json.dumps(raw), target, new_identity, tmp_path)


def test_uncorrelated_or_incomplete_helper_reply_is_rejected(tmp_path):
    marker = rename._RESULT_PREFIX + '{"ok":true,"operationId":"old"}'
    with pytest.raises(ValueError, match="Uncorrelated"):
        rename._parse_result(marker, 0, "new")
    with pytest.raises(rename.CharacterRenameError) as error:
        rename._finish({"ok": True, "characterId": 702}, _request(tmp_path), tmp_path / "backup")
    assert error.value.needs_review


_FIXTURE_SEED = """
const Sqlite = require('./server/node_modules/better-sqlite3');
const sqliteStore = require('./server/src/gameStore/sqliteStore');
const file = process.argv[1];
sqliteStore.init(file);
sqliteStore.close();
const connection = new Sqlite(file);
for (const table of ['accounts', 'characters', 'corporations', 'items', 'marketEscrow']) {
  connection.exec('CREATE TABLE ' + table + ' (key TEXT PRIMARY KEY, json TEXT NOT NULL)');
  connection.prepare('INSERT INTO _migrations VALUES (?, ?)').run(table, '2026-11-25');
}
const rows = JSON.parse(require('fs').readFileSync(0, 'utf8'));
for (const [table, contents] of Object.entries(rows)) {
  const insert = connection.prepare('INSERT INTO ' + table + ' VALUES (?, ?)');
  for (const [key, data] of Object.entries(contents)) insert.run(key, JSON.stringify(data));
}
connection.close();
"""


def _prepare_linux_test_install(root):
    client = root / "client-cache" / "tq"
    cache = client.parent
    prefix = root / "prefix"
    steam = root / "steam"
    proton = root / "proton"
    node = shutil.which("node")
    assert node
    for directory in (client / "bin64", cache / "ResFiles", prefix, steam):
        directory.mkdir(parents=True)
    for file in (client / "bin64" / "exefile.exe", client / "start.ini",
                 cache / "index_tranquility.txt", root / "server" / "index.js", proton):
        file.write_text("fixture", encoding="utf-8")
    proton.chmod(0o755)
    settings = {"EVEJS_REPO": root, "EVEJS_VERSION": "261115.2", "SUPPORTED_BUILD": "3396210",
                "TQ_PATH": client, "CACHE_ROOT": cache, "PROTON_BIN": proton, "PROTON_PREFIX": prefix,
                "STEAM_ROOT": steam, "NODE_BIN": node, "EVEJS_CDN_PORT": "443"}
    (root / ".evejs-linux.conf").write_text(
        "\n".join(f"{key}={shlex.quote(str(value))}" for key, value in settings.items()), encoding="utf-8",
    )


def _table_rows(database, table):
    with closing(sqlite3.connect(database)) as connection:
        return dict(connection.execute(f'SELECT key, json FROM "{table}" ORDER BY key'))


def test_reviewed_native_save_preserves_IDs_membership_orders_and_other_names(tmp_path, monkeypatch):
    fixture = os.environ.get("EVEJS_CHARACTER_RENAME_QA_STOCK")
    if not fixture:
        pytest.skip("requires a disposable, dependency-prepared copy of the reviewed stock EveJS archive")
    archive_copy = Path(fixture).resolve(strict=True)
    assert "/.qa/" in archive_copy.as_posix(), "Integration source must be an explicit QA copy."
    root = tmp_path / "disposable world"
    shutil.copytree(archive_copy, root, ignore=shutil.ignore_patterns("_local"))
    if os.name != "nt":
        _prepare_linux_test_install(root)
    store = root / "_local" / "gameStore"
    (store / "data").mkdir(parents=True)
    database = store / "gamestore.sqlite"
    rows = {
        "accounts": {"qa-account": {"id": 504, "username": "qa-account"}},
        "characters": {
            701: {"accountId": 504, "characterName": "Repeated Pilot", "corporationID": 808,
                  "corporationRole": "director", "walletBalance": 10000, "shipID": 80801},
            702: {"accountId": 504, "characterName": "Repeated Pilot", "corporationID": 808},
            703: {"accountId": 504, "characterName": "Already Known", "corporationID": 808},
        },
        "corporations": {808: {"ceoID": 701, "memberIDs": [701, 702, 703]}},
        "items": {80801: {"ownerID": 701, "itemName": "Fixture Ship"}},
        "marketEscrow": {609: {"ownerId": 701, "amount": 4200}},
    }
    seeded = subprocess.run(("node", "-e", _FIXTURE_SEED, str(database)), cwd=root,
                            input=json.dumps(rows).encode(), capture_output=True)
    assert seeded.returncode == 0, seeded.stderr.decode("utf-8", errors="replace")
    market = root / "externalservices" / "market-server" / "data" / "generated" / "market.sqlite"
    market.parent.mkdir(parents=True)
    with closing(sqlite3.connect(market)) as conn:
        conn.execute("CREATE TABLE market_orders (order_id INTEGER, owner_id INTEGER, bid INTEGER, escrow INTEGER)")
        conn.executemany("INSERT INTO market_orders VALUES (?, ?, ?, ?)", [(609, 701, 1, 4200), (610, 701, 0, 0)])
        conn.commit()
    before = {table: _table_rows(database, table) for table in rows}
    market_hash = hashlib.sha256(market.read_bytes()).digest()
    # Verify the real storage helper with a QA store, without probing services
    # on the host's standard ports. The listener guard is covered separately.
    monkeypatch.setattr(rename, "_require_native_offline", lambda *_args: None)

    for edits, message in [
        ({"new_name": "already known"}, "already in use"),
        ({"new_name": "GM Guard"}, "reserves"),
        ({"old_name": "Stale Pilot"}, "Refresh and try again"),
    ]:
        with pytest.raises(rename.CharacterRenameError, match=message) as error:
            rename.rename_character(_request(root, **edits))
        assert not error.value.needs_review and error.value.backup_path is None
    unchanged = rename.rename_character(_request(root, new_name="Repeated Pilot"))
    assert not unchanged.changed and unchanged.backup_path is None
    result = rename.rename_character(_request(root, new_name="  New Pilot  "))
    assert result.name == "New Pilot" and result.character_id == 701 and result.changed
    assert isinstance(result.backup_path, Path)
    original = json.loads(result.backup_path.read_text(encoding="utf-8"))["originalRow"]
    assert original == before["characters"]["701"]
    after = dict(before["characters"])
    record = json.loads(original)
    record["characterName"] = "New Pilot"
    after["701"] = json.dumps(record, separators=(",", ":"))
    assert _table_rows(database, "characters") == after
    for table in rows.keys() - {"characters"}:
        assert _table_rows(database, table) == before[table]
    assert hashlib.sha256(market.read_bytes()).digest() == market_hash
    with closing(sqlite3.connect(database)) as connection:
        assert not connection.execute("SELECT 1 FROM _persistence_outbox").fetchone()
        assert not connection.execute("SELECT 1 FROM _persistence_owners WHERE active=1").fetchone()

    projected = tmp_path / "projected container storage" / "gameStore"
    (projected / "data").mkdir(parents=True)
    projected_db = projected / "gamestore.sqlite"
    with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(projected_db)) as destination:
        source.backup(destination)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("EVEJS_") and key not in {"NODE_OPTIONS", "NODE_PATH"}}
    environment.update(EVEJS_GAMESTORE_DATA_DIR=str(projected / "data"), EVEJS_LOG_LEVEL="0")
    operation_id = "1" * 32
    projected_request = _request(root, character_id=702, new_name="Volume Pilot")
    completed = subprocess.run(("node", str(rename._HELPERS / "rename_character.js")), cwd=root,
                               env=environment, input=rename._payload(projected_request, operation_id),
                               capture_output=True)
    payload = rename._parse_result(completed.stdout.decode("utf-8"), completed.returncode, operation_id)
    assert payload["ok"], payload
    assert json.loads(_table_rows(projected_db, "characters")["702"])["characterName"] == "Volume Pilot"
    assert _table_rows(database, "characters") == after
    assert (projected / rename._BACKUP / f"{operation_id}.json").is_file()

    def blocked_recovery(kind, message):
        with pytest.raises(rename.CharacterRenameError, match=message) as error:
            rename.rename_character(_request(root, character_id=702, new_name="Blocked Pilot"))
        assert not error.value.needs_review and not error.value.backup_path, kind
        assert _table_rows(database, "characters") == after

    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO _persistence_owners(owner_role, epoch, instance_id, active, exclusive, "
            "lease_expires_at, acquired_at) VALUES ('world', 1, 'qa-world', 1, 0, ?, 'qa')",
            (int(time.time() * 1000) + 120_000,),
        )
        connection.commit()
        blocked_recovery("owner", "still owns")
        connection.execute("DELETE FROM _persistence_owners WHERE owner_role='world'")
        connection.execute(
            "INSERT INTO _persistence_outbox(table_name, owner_role, owner_epoch, owner_instance_id, "
            "upserts_json, deletes_json, created_at) VALUES ('characters', 'world', 1, 'qa-world', '[]', '[]', 'qa')"
        )
        connection.commit()
        blocked_recovery("pending", "unfinished writes")
