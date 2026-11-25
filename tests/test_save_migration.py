from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
from uuid import uuid4

import pytest

from src.core import save_migration
from src.core.save_migration import SaveInstallation, Transfer, migrate, verify_stock_game
from src.core.save_migration_storage import SaveMigrationError, resolve_storage

GAME = Path("_local/gameStore/gamestore.sqlite")
MARKET = Path("externalservices/market-server/data/generated/market.sqlite")
IMAGE = os.environ.get("EVEJS_SAVE_TEST_IMAGE", "node:22-alpine")


@pytest.fixture(autouse=True)
def isolate_native_fixture_ports(monkeypatch, tmp_path):
    # Reserve a port without listening so the real offline check can run
    # without consulting any Game or Market instance on the test host.
    with socket.socket() as offline:
        offline.bind(("127.0.0.1", 0))
        port = offline.getsockname()[1]

        def fixture_storage(root, settings):
            storage = resolve_storage(root, settings)
            if storage.runner is None and storage.root.is_relative_to(tmp_path):
                return replace(storage, ports=(int(settings.get("game_port", port)),))
            return storage

        monkeypatch.setattr(save_migration, "resolve_storage", fixture_storage)
        yield


@pytest.fixture(scope="module")
def docker_save_image(tmp_path_factory):
    root = tmp_path_factory.mktemp("stock-save-image")
    _code(root)
    assert "\n" not in IMAGE and "\r" not in IMAGE
    (root / "Dockerfile").write_text(
        f"FROM {IMAGE}\nWORKDIR /app\nCOPY config ./config\nCOPY server ./server\n"
        "COPY externalservices ./externalservices\n", encoding="utf-8")
    image = "evejs-save-qa-" + uuid4().hex[:12]
    subprocess.run(["docker", "build", "--network=none", "--pull=false", "-t", image, str(root)],
                   check=True, capture_output=True, text=True)
    try:
        yield image
    finally:
        subprocess.run(["docker", "image", "rm", image], check=True, capture_output=True)


def _db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return closing(sqlite3.connect(path))


def _game(path, identifier, *, escrow=False):
    with _db(path) as db:
        for name in ("accounts", "characters", "items", "marketEscrow", "marketRuntime"):
            db.execute(f'CREATE TABLE "{name}" (key TEXT PRIMARY KEY, json TEXT NOT NULL)')
        db.execute("INSERT INTO accounts VALUES ('account','{}')")
        db.execute("INSERT INTO characters VALUES ('1',?)", (json.dumps({"name": identifier}),))
        db.execute('INSERT INTO items VALUES (\'normal\',\'{"locationID":60003760}\')')
        if escrow:
            db.execute('INSERT INTO items VALUES (\'listed\',\'{"locationID":9200000007}\')')
            db.execute('INSERT INTO marketEscrow VALUES (\'7\',\'{"escrowType":"sell"}\')')
            db.execute('INSERT INTO marketRuntime VALUES (\'lastProcessedOrderEventId\',\'"42"\')')
        db.commit()


def _market(path, trading):
    with _db(path) as db:
        db.executescript("""
            CREATE TABLE manifest (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE stations (id INTEGER PRIMARY KEY);
            CREATE TABLE market_types (id INTEGER PRIMARY KEY);
            CREATE TABLE seed_stock (id INTEGER PRIMARY KEY);
            CREATE TABLE market_orders (id INTEGER PRIMARY KEY, escrow INTEGER);
            CREATE TABLE market_order_events (id INTEGER PRIMARY KEY);
            CREATE TABLE price_history (id INTEGER PRIMARY KEY);
            INSERT INTO manifest VALUES ('manifest_json', '{"schema_version":1}');
            INSERT INTO seed_stock VALUES (1);
        """)
        if trading:
            db.execute("INSERT INTO market_orders VALUES (7,500)")
            db.execute("INSERT INTO market_order_events VALUES (1)")
        db.commit()


def _code(root, *, market_contract="same"):
    config = root / "config"
    config.mkdir(parents=True)
    (config / "version.json").write_text('{"evejsVersion":"261115.2","configSchemaVersion":1}')
    game = root / "server/src/gameStore"
    game.mkdir(parents=True)
    tables = ["accounts", "characters", "items", "marketEscrow", "marketRuntime"]
    table_text = "".join(f'  "{name}",\n' for name in tables)
    (game / "index.js").write_text(f"const SQLITE_TABLES = new Set([\n{table_text}]);")
    (game / "sqliteStore.js").write_text("// stock")
    for member in ("src/state.rs", "crates/market-common/src/lib.rs"):
        path = root / "externalservices/market-server" / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pub const MARKET_SCHEMA_VERSION: u32 = 1;\n" + market_contract)


def installation(root, tag, *, trading=False, market_contract="same"):
    _code(root, market_contract=market_contract)
    _game(root / GAME, tag, escrow=trading)
    _market(root / MARKET, trading)
    (root / GAME.parent / "data").mkdir()
    portrait = root / GAME.parent / "images/Character/1.png"
    portrait.parent.mkdir(parents=True)
    portrait.write_bytes(tag.encode())
    return SaveInstallation(tag, root, {"runtime_backend": "native"})


def _row(path, sql):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute(sql).fetchone()


@pytest.mark.parametrize("move", [False, True], ids=["copy", "move"])
@pytest.mark.parametrize("depth", [
    "standard",
    pytest.param("long-save-path", marks=pytest.mark.skipif(os.name != "nt", reason="Windows long paths")),
    pytest.param("long-install-path", marks=pytest.mark.skipif(os.name != "nt", reason="Windows long paths")),
])
def test_native_route(tmp_path, move, depth):
    base = tmp_path
    if depth != "standard":
        endpoint = Path("source") / GAME.parent if depth == "long-save-path" else Path("source")
        while len(str(base / endpoint)) <= 260:
            base /= "Données QA"
        if depth == "long-save-path":
            assert len(str(base / "source")) < 260
    source = installation(base / "source", "player", trading=True)
    target = installation(base / "target", "target")
    with (target.root / GAME.parent / "data/keep.json").open("w") as stream:
        stream.write("target static data")
    result = migrate(Transfer(source, target, move, True))
    assert result.moved == move
    assert not result.note
    assert _row(target.root / GAME, "SELECT json FROM characters") == ('{"name": "player"}',)
    assert _row(target.root / MARKET, "SELECT escrow FROM market_orders") == (500,)
    assert _row(target.root / GAME, "SELECT count(*) FROM marketEscrow") == (1,)
    assert (target.root / GAME.parent / "images/Character/1.png").read_bytes() == b"player"
    assert (target.root / GAME.parent / "data/keep.json").read_text() == "target static data"
    assert (source.root / GAME).exists() is not move
    assert (source.root / MARKET).exists() is not move
    assert _row(result.backup / "destination/game", "SELECT json FROM characters") == ('{"name": "target"}',)
    assert _row(result.backup / "destination/market", "SELECT count(*) FROM market_orders") == (0,)
    assert (result.backup / "source/game").is_file() is move
    assert json.loads((result.backup / "transfer.json").read_text())["state"] == ("moved" if move else "copied")
    for world in (source.root, target.root):
        for parent in (GAME.parent, MARKET.parent):
            assert not list((world / parent).glob(".evejs-save-migration-*"))


def test_fresh_market_keeps_normal_inventory_and_old_market(tmp_path):
    source = installation(tmp_path / "source", "player", trading=True)
    target = installation(tmp_path / "target", "target", market_contract="other")
    result = migrate(Transfer(source, target, True, False))
    assert result.moved
    assert _row(target.root / GAME, "SELECT key FROM items") == ("normal",)
    assert _row(target.root / MARKET, "SELECT count(*) FROM market_orders") == (0,)
    assert _row(target.root / GAME, "SELECT count(*) FROM marketEscrow") == (0,)
    assert _row(target.root / GAME, "SELECT count(*) FROM marketRuntime") == (0,)
    assert _row(source.root / MARKET, "SELECT escrow FROM market_orders") == (500,)
    assert _row(result.backup / "source/game", "SELECT count(*) FROM marketEscrow") == (1,)


@pytest.mark.skipif(os.name != "nt", reason="Windows rename sharing errors")
@pytest.mark.parametrize("persistent", [False, True], ids=["transient", "persistent"])
def test_windows_rename_conflict_retries_or_restores_destination(tmp_path, monkeypatch, persistent):
    source = installation(tmp_path / "source", "player", trading=True)
    target = installation(tmp_path / "target", "target")
    marker = tmp_path / "rename-attempts"
    fixture = tmp_path / "rename-conflict.js"
    fixture.write_text("""
const fs = require("node:fs");
const path = require("node:path");
const rename = fs.renameSync;
let injected = false;
fs.renameSync = (from, to) => {
  if (path.basename(from) === "incoming" && path.basename(to) === "gamestore.sqlite"
      && (!injected || process.env.EVEJS_TEST_PERSISTENT_RENAME === "1")) {
    injected = true;
    fs.appendFileSync(process.env.EVEJS_TEST_RENAME_MARKER, "x");
    throw Object.assign(new Error("fixture rename denied"), { code: "EPERM" });
  }
  return rename(from, to);
};
""", encoding="utf-8")
    monkeypatch.setenv("NODE_OPTIONS", f'--require "{fixture.as_posix()}"')
    monkeypatch.setenv("EVEJS_TEST_RENAME_MARKER", str(marker))
    monkeypatch.setenv("EVEJS_TEST_PERSISTENT_RENAME", "1" if persistent else "0")

    transfer = Transfer(source, target, False, True)
    if persistent:
        with pytest.raises(SaveMigrationError, match="fixture rename denied"):
            migrate(transfer)
    else:
        migrate(transfer)

    assert marker.read_text() == ("xxxx" if persistent else "x")
    expected = "target" if persistent else "player"
    assert _row(target.root / GAME, "SELECT json FROM characters") == (json.dumps({"name": expected}),)
    assert _row(target.root / MARKET, "SELECT count(*) FROM market_orders") == (int(not persistent),)
    assert _row(source.root / GAME, "SELECT json FROM characters") == ('{"name": "player"}',)
    assert not list((target.root / GAME.parent).glob(".evejs-save-migration-*"))


@pytest.mark.parametrize("reject", [True, False], ids=["rejected", "copied"])
def test_workspace_cleanup_error_preserves_transfer_result(tmp_path, monkeypatch, caplog, reject):
    class LockedWorkspace(save_migration.TemporaryDirectory):
        def cleanup(self):
            super().cleanup()
            if Path(self.name).name.startswith("work-"):
                raise PermissionError("fixture cleanup lock")

    monkeypatch.setattr(save_migration, "TemporaryDirectory", LockedWorkspace)
    source = installation(tmp_path / "source", "player")
    target = installation(tmp_path / "target", "target")

    def check_game(db, request, destination_tables):
        if reject:
            raise SaveMigrationError("Fixture format rejection")

    transfer = Transfer(source, target, False, False)
    if reject:
        with pytest.raises(SaveMigrationError, match="Fixture format rejection"):
            migrate(transfer, verify_game=check_game)
    else:
        migrate(transfer, verify_game=check_game)

    area = target.root / ".evejs-launcher/save-migrations"
    receipt, = area.glob("*/transfer.json")
    assert json.loads(receipt.read_text())["state"] == ("stopped" if reject else "copied")
    assert _row(source.root / GAME, "SELECT json FROM characters") == ('{"name": "player"}',)
    expected = "target" if reject else "player"
    assert _row(target.root / GAME, "SELECT json FROM characters") == (json.dumps({"name": expected}),)
    assert "Could not remove save transfer workspace" in caplog.text
    assert "fixture cleanup lock" in caplog.text


@pytest.mark.skipif(os.name != "nt", reason="requires Windows file sharing semantics")
@pytest.mark.parametrize("reject", [True, False], ids=["rejected", "completed"])
@pytest.mark.parametrize("move", [False, True], ids=["copy", "move"])
def test_real_windows_workspace_lock_preserves_result(tmp_path, monkeypatch, caplog, reject, move):
    held = []

    class LockedWorkspace(save_migration.TemporaryDirectory):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if Path(self.name).name.startswith("work-"):
                handle = (Path(self.name) / "held-scratch-file").open("w+b")
                handle.write(b"disposable locked scratch data")
                handle.flush()
                held.append((handle, Path(self.name)))

    monkeypatch.setattr(save_migration, "TemporaryDirectory", LockedWorkspace)
    source = installation(tmp_path / "source", "player", trading=True)
    target = installation(tmp_path / "target", "target", trading=True)

    def check_game(db, request, destination_tables):
        if reject:
            raise SaveMigrationError("Fixture format rejection")
        verify_stock_game(db, request, destination_tables)

    try:
        transfer = Transfer(source, target, move, True)
        if reject:
            with pytest.raises(SaveMigrationError, match="Fixture format rejection") as failure:
                migrate(transfer, verify_game=check_game)
            assert str(failure.value.__cause__) == "Fixture format rejection"
        else:
            result = migrate(transfer, verify_game=check_game)
            assert result.moved is move and not result.note

        receipt, = (target.root / ".evejs-launcher/save-migrations").glob("*/transfer.json")
        expected_state = "stopped" if reject else "moved" if move else "copied"
        assert json.loads(receipt.read_text())["state"] == expected_state
        assert _row(receipt.parent / "destination/game", "SELECT json FROM characters") == ('{"name": "target"}',)
        if move:
            assert _row(receipt.parent / "source/game", "SELECT json FROM characters") == ('{"name": "player"}',)
        if reject or not move:
            assert _row(source.root / GAME, "SELECT json FROM characters") == ('{"name": "player"}',)
        else:
            assert not (source.root / GAME).exists()
        expected_target = "target" if reject else "player"
        assert _row(target.root / GAME, "SELECT json FROM characters") == (json.dumps({"name": expected_target}),)
        assert any(record.name == save_migration.__name__ and record.exc_info
                   and getattr(record.exc_info[1], "winerror", None) == 32 for record in caplog.records)
    finally:
        for handle, scratch in held:
            handle.close()
            assert scratch.resolve().is_relative_to(tmp_path.resolve())
            if scratch.exists():
                shutil.rmtree(scratch)


@pytest.mark.parametrize("reason", ["market", "busy", "stale"])
def test_dangerous_input_leaves_both_worlds_intact(tmp_path, reason):
    source = installation(tmp_path / "source", "player",
                          market_contract="other" if reason == "market" else "same")
    target = installation(tmp_path / "target", "target")
    def progress(message):
        if reason == "stale" and message.startswith("Checking the game and market"):
            with _db(source.root / GAME) as db:
                db.execute("INSERT INTO items VALUES ('new', '{}')")
                db.commit()

    with socket.socket() as service:
        if reason == "busy":
            service.bind(("127.0.0.1", 0))
            service.listen(1)
            source.settings["game_port"] = service.getsockname()[1]
        with pytest.raises(SaveMigrationError, match="port .* is in use" if reason == "busy" else None):
            migrate(Transfer(source, target, True, True), progress=progress)
    assert _row(target.root / GAME, "SELECT json FROM characters") == ('{"name": "target"}',)
    assert _row(source.root / GAME, "SELECT json FROM characters") == ('{"name": "player"}',)
    assert (source.root / MARKET).is_file()


def test_native_wal_commits_make_it_through_the_backup(tmp_path):
    source = installation(tmp_path / "source", "player")
    target = installation(tmp_path / "target", "target")
    with _db(source.root / GAME) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO characters VALUES ('2','{}')")
        db.commit()
        assert (source.root / (str(GAME) + "-wal")).stat().st_size
        migrate(Transfer(source, target, False, True))
        assert _row(target.root / GAME, "SELECT count(*) FROM characters") == (2,)
        assert _row(source.root / GAME, "SELECT count(*) FROM characters") == (2,)


@pytest.mark.parametrize("state", ["lease", "outbox"])
def test_destination_with_pending_game_writes_is_never_replaced(tmp_path, state):
    import time

    source = installation(tmp_path / "source", "source")
    target = installation(tmp_path / "target", "target")
    with _db(target.root / GAME) as db:
        if state == "lease":
            db.execute("CREATE TABLE _persistence_owners (active INTEGER, lease_expires_at INTEGER)")
            db.execute("INSERT INTO _persistence_owners VALUES (1,?)", (int(time.time()*1000)+60000,))
        else:
            db.execute("CREATE TABLE _persistence_outbox (pending TEXT)")
            db.execute("INSERT INTO _persistence_outbox VALUES ('unfinished')")
        db.commit()
    before = (target.root / GAME).read_bytes()
    with pytest.raises(SaveMigrationError, match="owns|unfinished"):
        migrate(Transfer(source, target, True, True))
    assert (target.root / GAME).read_bytes() == before
    assert _row(source.root / GAME, "SELECT json FROM characters") == ('{"name": "source"}',)



@pytest.mark.parametrize("cleanup_error", [False, True], ids=["clean-workspace", "locked-workspace"])
def test_source_cleanup_failure_keeps_the_verified_destination(tmp_path, monkeypatch, caplog, cleanup_error):
    from src.core.save_migration_storage import SaveStorage

    if cleanup_error:
        class LockedWorkspace(save_migration.TemporaryDirectory):
            def cleanup(self):
                super().cleanup()
                if Path(self.name).name.startswith("work-"):
                    raise PermissionError("fixture cleanup lock")
        monkeypatch.setattr(save_migration, "TemporaryDirectory", LockedWorkspace)

    source = installation(tmp_path / "source", "player", trading=True)
    destination = installation(tmp_path / "target", "target")
    original = SaveStorage.action

    def action(storage, operation, *args, **kwargs):
        if storage.root == source.root and operation == "replace":
            raise SaveMigrationError("A simulated file lock prevented the removal.")
        return original(storage, operation, *args, **kwargs)

    monkeypatch.setattr(SaveStorage, "action", action)
    result = migrate(Transfer(source, destination, True, True))
    assert not result.moved
    assert "destination copy passed" in result.note
    assert _row(source.root / GAME, "SELECT json FROM characters") == ('{"name": "player"}',)
    assert _row(destination.root / GAME, "SELECT json FROM characters") == ('{"name": "player"}',)
    assert (result.backup / "source/game").is_file()
    assert (result.backup / "destination/game").is_file()
    assert json.loads((result.backup / "transfer.json").read_text())["state"] == "copy-only"
    if cleanup_error:
        assert "Could not remove save transfer workspace" in caplog.text


def test_fresh_market_uses_a_separate_clean_seed(tmp_path):
    source = installation(tmp_path / "source", "source", trading=True)
    destination = installation(tmp_path / "target", "target", trading=True)
    seed = tmp_path / "clean.sqlite"
    _market(seed, False)
    result = migrate(Transfer(source, destination, False, False, seed))
    assert _row(destination.root / MARKET, "SELECT count(*) FROM market_orders") == (0,)
    assert _row(result.backup / "destination/market", "SELECT count(*) FROM market_orders") == (1,)
    assert _row(source.root / MARKET, "SELECT count(*) FROM market_orders") == (1,)
    assert _row(seed, "SELECT count(*) FROM market_orders") == (0,)


@pytest.mark.parametrize("move", [False, True], ids=["copy", "move"])
def test_launcher_page_runs_copy_after_fresh_market_acknowledgement(qapp, tmp_path, move):
    import time
    from PyQt6.QtCore import QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QCheckBox, QDialog, QPushButton
    from src.pages.save_migration_page import SaveMigrationPage

    source = installation(tmp_path / "source", "source", trading=True)
    destination = installation(tmp_path / "destination", "destination")
    page = SaveMigrationPage(verifier=verify_stock_game, can_start=lambda: True,
                             installation_label=lambda entry: entry["name"])
    profiles = [
        {"id": "first", "name": "Live", "settings": {**source.settings, "evejs_root": str(source.root)}},
        {"id": "second", "name": "Testing", "settings": {**destination.settings, "evejs_root": str(destination.root)}},
    ]
    page.set_installations(profiles, "first")
    page.move.setChecked(move)
    assert page.review.isEnabled()
    assert "won’t appear" in page.market_note.text()
    verified = []
    def click_review():
        dialog = next(dialog for dialog in page.findChildren(QDialog) if dialog.isVisible())
        action = next(button for button in dialog.findChildren(QPushButton)
                      if button.text() == ("Move playthrough" if move else "Copy playthrough"))
        verified.append(not action.isEnabled())
        dialog.findChild(QCheckBox).setChecked(True)
        action.click()
    outcomes = []
    close_result = QTimer(page)
    close_result.setInterval(10)
    def dismiss_result():
        for dialog in page.findChildren(QDialog):
            if dialog.objectName() == "saveMigrationOutcome" and dialog.isVisible():
                outcomes.append(dialog.windowTitle())
                dialog.accept()
    close_result.timeout.connect(dismiss_result)
    close_result.start()
    try:
        QTimer.singleShot(0, click_review)
        page.review.click()
        end = time.monotonic() + 30
        while page.busy and time.monotonic() < end:
            QTest.qWait(20)
    finally:
        close_result.stop()
    assert not page.busy
    assert verified == [True]
    expected = "Playthrough moved." if move else "Playthrough copied."
    assert page.status.text() == expected
    assert outcomes == [expected]
    assert (source.root / GAME).exists() is not move
    assert _row(destination.root / GAME, "SELECT key FROM items") == ("normal",)
    page.deleteLater()
    qapp.processEvents()


@pytest.mark.skipif(os.environ.get("EVEJS_SAVE_TEST_DOCKER") != "1", reason="requires opt-in disposable Docker fixtures")
@pytest.mark.parametrize("move", [False, True], ids=["copy", "move"])
@pytest.mark.parametrize("backends", [("native", "docker"), ("docker", "native"), ("docker", "docker")])
def test_docker_route(tmp_path, backends, move, docker_save_image):
    token = "save-qa-" + uuid4().hex[:12]
    volumes = []

    def docker(*arguments):
        return subprocess.run(["docker", *arguments], check=True, capture_output=True, text=True).stdout

    def prepare(name, backend, *, trading):
        install = installation(tmp_path / name, name, trading=trading)
        if backend == "native":
            return install
        project = token + "-" + name
        volume = project + "-data"
        docker("volume", "create", "--label", "com.docker.compose.project=" + project, volume)
        volumes.append(volume)
        profile = install.settings
        composition = install.root / "compose.yaml"
        spec = {
            "name": project,
            "services": {kind: {"image": docker_save_image, "volumes": ["data:/var/lib/evejs"]}
                         for kind in ("server", "market")},
            "volumes": {"data": {"name": volume}},
        }
        composition.write_text(json.dumps(spec), encoding="utf-8")
        profile.update(runtime_backend="docker_compose", docker_compose_file=str(composition),
                       docker_project_name=project)
        fixture = install.root / "fixture"
        fixture.mkdir()
        shutil.copyfile(install.root / GAME, fixture / "game")
        shutil.copyfile(install.root / MARKET, fixture / "market")
        docker("run", "--rm", "--network", "none", "--pull", "never",
               "--mount", f"type=bind,source={fixture},target=/input,readonly",
               "--mount", f"type=volume,source={volume},target=/world",
               "--entrypoint", "sh", docker_save_image, "-ec",
               "mkdir -p /world/gameStore/data /world/market; cp /input/game /world/gameStore/gamestore.sqlite; "
               "cp /input/market /world/market/market.sqlite")
        return install

    try:
        source = prepare("source", backends[0], trading=True)
        target = prepare("target", backends[1], trading=False)
        result = migrate(Transfer(source, target, move, True))
        assert result.moved == move, result.note
        assert not result.note
        for name, endpoint in (("source-final", source), ("destination-final", target)):
            staging = tmp_path / name
            staging.mkdir()
            resolve_storage(endpoint.root, endpoint.settings).action("capture", staging)
            if endpoint is source and move:
                assert not (staging / "game").exists()
                assert not (staging / "market").exists()
            else:
                assert _row(staging / "game", "SELECT json FROM characters") == ('{"name": "source"}',)
                assert _row(staging / "market", "SELECT escrow FROM market_orders") == (500,)
        assert (result.backup / "source/game").is_file() is move
        assert (result.backup / "destination/market").is_file()
    finally:
        for volume in volumes:
            docker("volume", "rm", volume)


@pytest.mark.parametrize("move", [False, True])
def test_optional_client_settings_use_numeric_identity_and_backup(tmp_path, monkeypatch, move):
    from src.core import overview_patch
    from src.core import save_migration_client_settings as client

    source = replace(installation(tmp_path / "source", "source"), profile_id="a" * 32)
    target = replace(installation(tmp_path / "target", "target"), profile_id="b" * 32)
    with _db(source.root / GAME) as db:
        db.execute("UPDATE accounts SET json=?", ('{"id":42}',))
        db.execute("UPDATE characters SET json=?", ('{"characterName":"source","accountId":42}',))
        db.commit()

    monkeypatch.setattr(client, "IS_LINUX", False)
    monkeypatch.setattr(client, "PROFILES_ROOT", tmp_path / "config/Profiles")
    monkeypatch.setattr(client.platform, "get_eve_settings_path",
                        lambda tq: Path(tq).parent / "settings")
    monkeypatch.setattr(overview_patch, "is_eve_client_running", lambda: False)
    paths = [client._windows_settings(client.main_location(i), ("account",))("account")[0]
             for i in (source, target)]
    for path in paths:
        path.mkdir(parents=True)
    for name, data in {"core_user_42.dat": b"~account", "core_char_1.dat": b"~character",
                       "core_char_999.dat": b"~other", "core_public__.yaml": b"private"}.items():
        (paths[0] / name).write_bytes(data)
    (paths[1] / "core_char_1.dat").write_bytes(b"~previous")

    result = migrate(Transfer(source, target, move, True, carry_client_settings=True))
    assert result.moved is move
    assert result.client_settings_count == 2
    assert {file.name: file.read_bytes() for file in paths[1].iterdir()} == {
        "core_user_42.dat": b"~account", "core_char_1.dat": b"~character"}
    assert b"~previous" in {file.read_bytes() for file in
                            (result.backup / "client-settings/backups").glob("*.bin")}
    assert (paths[0] / "core_char_1.dat").read_bytes() == b"~character"


def test_client_settings_failure_rolls_back_destination(tmp_path, monkeypatch):
    from src.core import overview_patch
    from src.core import save_migration_client_settings as client

    source = installation(tmp_path / "source", "source")
    target = installation(tmp_path / "target", "target")
    monkeypatch.setattr(overview_patch, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(client, "capture_client_settings", lambda *_args: {tmp_path / "unused": b"~sample"})

    def locked_preferences(*_args):
        raise OSError("Preferences file is locked")

    monkeypatch.setattr(client, "apply_client_settings", locked_preferences)
    with pytest.raises(SaveMigrationError, match="The destination save was restored"):
        migrate(Transfer(source, target, True, True, carry_client_settings=True))
    assert b"source" == (source.root / GAME.parent / "images/Character/1.png").read_bytes()
    assert b"target" == (target.root / GAME.parent / "images/Character/1.png").read_bytes()


@pytest.mark.parametrize("linux", [False, True])
def test_client_settings_ignores_shared_profiles_from_another_world(tmp_path, monkeypatch, linux):
    from hashlib import sha256
    from src.core import overview_patch
    from src.core import save_migration_client_settings as client
    from src.core.groups import installation_group_identity
    from src.core.native_drone_profiles import remember_personal_settings
    from src.core.shared_settings_profile import SharedSettingsSource, _json_bytes, shared_profile_main_folder

    source = replace(installation(tmp_path / "source", "source"), profile_id="a" * 32)
    target = replace(installation(tmp_path / "target", "target"), profile_id="b" * 32)
    source.settings["client_settings_mode"] = "shared"
    with _db(source.root / GAME) as db:
        db.execute("UPDATE accounts SET json=?", ('{"id":42}',))
        db.execute("UPDATE characters SET json=?", ('{"characterName":"source","accountId":42}',))
        db.commit()
    monkeypatch.setattr(client, "IS_LINUX", linux)
    monkeypatch.setattr(client, "PROFILES_ROOT", tmp_path / "config/Profiles")
    monkeypatch.setattr(client.platform, "get_eve_settings_path", lambda tq: Path(tq).parent / "settings")
    monkeypatch.setattr(client, "_linux_settings", lambda installation, *_args, **_kwargs:
                        lambda _name: (installation.root / "preferences",))
    monkeypatch.setattr(overview_patch, "is_eve_client_running", lambda: False)

    options = {**source.settings, "active_installation_profile": source.profile_id}
    base = client.client_profiles_root(options, client.PROFILES_ROOT)
    profile_tq = base / "account" / "tq"
    shared_root = base.parent / "SharedProfiles"

    def store(main, label):
        main.mkdir(parents=True)
        (main / "core_user_42.dat").write_bytes(b"~" + label + b" account")
        (main / "core_char_1.dat").write_bytes(b"~" + label + b" character")

    def legacy_world(root, label):
        owner = SharedSettingsSource("account", 42, 1, str(root))
        main = shared_profile_main_folder(profile_tq, owner, evejs_root=str(root))
        store(main, label)
        identity = {"source": owner.as_dict(), "serverRoot": os.path.normcase(str(root)),
                    "targetIdentity": None, "settingsFolder": os.path.normcase(str(main))}
        folder = shared_root / sha256(_json_bytes(identity)).hexdigest()
        folder.mkdir(parents=True)
        (folder / "active.json").write_bytes(_json_bytes({"identity": identity, "schemaVersion": 1}))

    legacy_world(tmp_path / "unrelated", b"wrong")
    if linux:
        legacy_world(source.root, b"correct")
        destination = target.root / "preferences"
    else:
        scope = installation_group_identity(options, None)
        owner = SharedSettingsSource("account", 42, 1, str(source.root))
        main = shared_profile_main_folder(profile_tq, owner, evejs_root=str(source.root), storage_identity=scope)
        store(main, b"correct")
        remember_personal_settings(shared_root, scope, 42, 1, main)
        destination = client._windows_settings(client.main_location(target), ("account",))("account")[0]

    result = migrate(Transfer(source, target, False, True, carry_client_settings=True))
    assert result.client_settings_count == 2
    assert (destination / "core_user_42.dat").read_bytes() == b"~correct account"
    assert (destination / "core_char_1.dat").read_bytes() == b"~correct character"
