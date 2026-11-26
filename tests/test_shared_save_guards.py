from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.core import save_migration, shared_character_maintenance
from src.core.save_migration_storage import SaveStorage, _compose_layout


@pytest.fixture
def shared_compose(tmp_path):
    volume = {"type": "volume", "source": "world", "target": "/var/lib/evejs"}
    static_binds = [
        ("server/src/_secondary/image/generated/Alliance", False),
        ("server/certs", True),
        ("server/src/_secondary/express/certs", True),
    ]
    mounts = [
        {"type": "bind", "source": str(tmp_path / relative), "target": "/app/" + relative,
         "read_only": read_only, "bind": {"create_host_path": True}}
        for relative, read_only in static_binds
    ]
    return {
        "name": "qa",
        "services": {
            "server": {"image": "evejs-local", "volumes": [volume, *mounts]},
            "market": {"image": "evejs-local", "volumes": [volume]},
        },
        "volumes": {"world": {"name": "qa-data"}},
    }


def test_shared_storage_accepts_the_stock_server_binds(tmp_path, shared_compose):
    _, _, mounts, files = _compose_layout(shared_compose, shared=True, root=tmp_path)
    assert [(mount.kind, mount.source) for mount in mounts] == [("volume", "qa-data")]
    assert files["game"].as_posix() == "/live0/gameStore/gamestore.sqlite"
    assert files["market"].as_posix() == "/live0/market/market.sqlite"


@pytest.mark.parametrize(("role", "override", "message"), [
    ("server", {"target": "/app"}, "overrides the reviewed save-format code"),
    ("server", {"target": "/app/server/src/gameStore"}, "overrides the reviewed save-format code"),
    ("server", {"target": "/app/server/certs/another"}, "overrides the reviewed save-format code"),
    ("server", {"target": "/app/config/../server"}, "non-canonical mount target"),
    ("server", {"target": "//app/server"}, "non-canonical mount target"),
    ("server", {"read_only": False}, "overrides the reviewed save-format code"),
    ("server", {"source": "other-installation"}, "overrides the reviewed save-format code"),
    ("server", {"type": "volume"}, "overrides the reviewed save-format code"),
    ("server", {"bind": {"propagation": "rshared"}}, "overrides the reviewed save-format code"),
    ("market", {}, "overrides the reviewed save-format code"),
    ("market", {"target": "/app/externalservices/market-server/src"}, "overrides the reviewed save-format code"),
    ("server", {"target": "/var/lib/evejs/gameStore/data"}, "save storage needs a writable directory mount"),
])
def test_shared_storage_rejects_mounts_outside_stock_contract(tmp_path, shared_compose, role, override, message):
    server, market = (shared_compose["services"][key] for key in ("server", "market"))
    certificate = server["volumes"].pop(2)
    if override.get("source"):
        override = {**override, "source": str(tmp_path / override["source"])}
    selected = server if role == "server" else market
    selected["volumes"].append({**certificate, **override})
    with pytest.raises(save_migration.SaveMigrationError, match=message):
        _compose_layout(shared_compose, shared=True, root=tmp_path)


def test_shared_storage_refuses_a_link_at_the_stock_source(tmp_path, shared_compose):
    actual = tmp_path / "unexpected"
    actual.mkdir()
    source = tmp_path / "server/certs"
    source.parent.mkdir()
    try:
        source.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("This host cannot create directory symlinks.")
    with pytest.raises(save_migration.SaveMigrationError, match="cannot use symbolic links"):
        _compose_layout(shared_compose, shared=True, root=tmp_path)


def _installation(root, version):
    (root / "config").mkdir(parents=True)
    (root / "config/version.json").write_text(json.dumps({
        "evejsVersion": version, "configSchemaVersion": 1,
    }), encoding="utf-8")
    return save_migration.SaveInstallation(root.name, root, {})


@pytest.mark.parametrize("release", ["261125.3", "261126.2"])
def test_copy_accepts_shared_release_and_blocks_incomplete_setup(tmp_path, release):
    source = _installation(tmp_path / "source", release)
    target = _installation(tmp_path / "target", release)
    request = save_migration.Transfer(source, target, move=False, carry_market=False)
    save_migration.review_transfer(request)
    (target.root / "_local").mkdir()
    (target.root / "_local/setup-state.json").write_text(
        json.dumps({"schema": 1, "mode": "native", "pendingVersion": release}), encoding="utf-8",
    )
    with pytest.raises(save_migration.SaveMigrationError, match="Resume setup"):
        save_migration.review_transfer(request)


def test_copy_does_not_treat_a_legacy_save_as_a_shared_release_transfer(tmp_path):
    old = _installation(tmp_path / "old", "261115.2")
    new = _installation(tmp_path / "new", "261125.3")
    request = save_migration.Transfer(old, new, move=True, carry_market=False)
    with pytest.raises(save_migration.SaveMigrationError, match="same verified EveJS base version"):
        save_migration.review_transfer(request)
    with pytest.raises(save_migration.SaveMigrationError, match="same verified EveJS base version"):
        save_migration.review_transfer(replace(request, source=new, destination=old))


@pytest.mark.parametrize("verified_original", [True, False])
def test_failed_install_requires_fingerprint_evidence_to_allow_restart(tmp_path, monkeypatch, verified_original):
    root = tmp_path / "game"
    root.mkdir()
    game = root / "_local/gameStore/gamestore.sqlite"
    storage = SaveStorage(root, {"game": game}, "node")
    calls = []
    before = {"game": "old", "data": "unchanged", "manifest": None}
    after = {**before, "game": "updated"}

    def stage(*args):
        return before, after, Path("detached"), (1, 2)

    def fail(self, action, *args, **kwargs):
        calls.append(action)
        if action == "replace":
            raise shared_character_maintenance.SaveMigrationError("Injected install error.")
        return before if verified_original else after

    monkeypatch.setattr(shared_character_maintenance, "_stage", stage)
    monkeypatch.setattr(SaveStorage, "action", fail)
    with pytest.raises(shared_character_maintenance.CharacterMaintenanceError) as error:
        shared_character_maintenance.maintain_character(
            storage, {"operation": "create"}, tmp_path / "backup",
            require_offline=lambda: None, verify=lambda *_: None,
        )
    assert error.value.restart_safe is verified_original
    assert calls == ["replace", "inventory"]


def test_rejected_detached_operation_never_calls_install(tmp_path, monkeypatch):
    root = tmp_path / "game"
    root.mkdir()
    storage = SaveStorage(root, {"game": root / "_local/gameStore/gamestore.sqlite"}, "node")

    def reject(*_):
        raise ValueError("Detached verification failed.")

    def unexpected(*_, **__):
        pytest.fail("A failed detached operation reached the original storage.")

    monkeypatch.setattr(shared_character_maintenance, "_stage", reject)
    monkeypatch.setattr(SaveStorage, "action", unexpected)
    with pytest.raises(shared_character_maintenance.CharacterMaintenanceError, match="Detached verification") as error:
        shared_character_maintenance.maintain_character(
            storage, {"operation": "delete"}, tmp_path / "backup",
            require_offline=lambda: None, verify=lambda *_: None,
        )
    assert error.value.restart_safe
