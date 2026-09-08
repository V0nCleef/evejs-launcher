import base64

import pytest

from src.core.mod_contributions import (
    ContributionConflict, ContributionError, ContributionOwner, ContributionStore,
    FileTarget, KeyEdit, RemovalEditConflict,
)


def setup(tmp_path, original):
    root = tmp_path / "runtime"
    root.mkdir()
    path = root / "shared.bin"
    if original is not None:
        path.write_bytes(original)
    return path, FileTarget.capture(path, root, "file"), ContributionStore(root), (
        ContributionOwner(root, "mods/alpha"), ContributionOwner(root, "mods/beta"))


def edit(target, content, override=False):
    return KeyEdit(target, ("$content",), base64.b64encode(content).decode("ascii"), allow_override=override)


@pytest.mark.parametrize("original", [None, b"original\x00\xff"])
def test_identical_file_owners_preserve_shared_file_until_last_removal(tmp_path, original):
    path, target, store, (alpha, beta) = setup(tmp_path, original)
    for owner in (alpha, beta):
        store.commit(store.plan_edits(owner, [edit(target, b"shared\x00\xfe")]))
    store.commit(store.plan_remove(alpha))
    assert path.read_bytes() == b"shared\x00\xfe"
    store.commit(store.plan_remove(beta))
    assert path.read_bytes() == original if original is not None else not path.exists()


def test_different_binary_replacements_require_precedence_and_preserve_later_user_edit(tmp_path):
    path, target, store, (alpha, beta) = setup(tmp_path, b"stock")
    store.commit(store.plan_edits(alpha, [edit(target, b"alpha")]))
    with pytest.raises(ContributionConflict):
        store.plan_edits(beta, [edit(target, b"beta")])
    assert path.read_bytes() == b"alpha"
    store.commit(store.plan_edits(beta, [edit(target, b"beta", override=True)]))
    store.commit(store.plan_remove(alpha))
    assert path.read_bytes() == b"beta"
    path.write_bytes(b"manual")
    with pytest.raises(RemovalEditConflict):
        store.plan_remove(beta)
    assert path.read_bytes() == b"manual"


def test_failed_overlay_commit_restores_exact_preoperation_bytes(tmp_path, monkeypatch):
    path, target, store, (alpha, _beta) = setup(tmp_path, b"stock\x00\xff")
    plan = store.plan_edits(alpha, [edit(target, b"replacement")])
    replace = store._replace_file
    failed = False
    def fail_index_once(destination, content):
        nonlocal failed
        if destination == store.index_path and not failed:
            failed = True
            raise OSError("Injected ownership write failure")
        return replace(destination, content)
    monkeypatch.setattr(store, "_replace_file", fail_index_once)
    with pytest.raises(OSError, match="Injected"):
        store.commit(plan)
    assert path.read_bytes() == b"stock\x00\xff"
    assert store.owners_for_path(path) == ()


@pytest.mark.parametrize("name", ["settings.json", "prefs.ini", "profile.yaml", "world.sqlite", "store.db"])
def test_raw_overlay_cannot_bypass_structured_settings_or_database_boundary(tmp_path, name):
    with pytest.raises(ContributionError):
        FileTarget.capture(tmp_path / name, tmp_path, "file")


@pytest.mark.parametrize("codec,bom", [("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe")])
@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_independent_text_regions_preserve_other_mod_and_user_edits(tmp_path, codec, bom, order):
    path = tmp_path / "shared.js"
    original = "// header\r\n<A>original A</A>\r\n<B>original B</B>\r\n// user\r\n"
    path.write_bytes(bom + original.encode(codec))
    target = FileTarget.capture(path, tmp_path, "text")
    store = ContributionStore(tmp_path)
    owners = [ContributionOwner(tmp_path, "mods/a"), ContributionOwner(tmp_path, "mods/b")]
    changes = [KeyEdit(target, ("<A>", "</A>"), "changed A"), KeyEdit(target, ("<B>", "</B>"), "changed B")]
    for index in order:
        store.commit(store.plan_edits(owners[index], [changes[index]]))
    installed = original.replace("original A", "changed A").replace("original B", "changed B").replace("// user", "// user edit")
    path.write_bytes(bom + installed.encode(codec))
    store.commit(store.plan_remove(owners[order[0]]))
    expected = installed.replace("changed A", "original A") if order[0] == 0 else installed.replace("changed B", "original B")
    assert path.read_bytes() == bom + expected.encode(codec)
    store.commit(store.plan_remove(owners[order[1]]))
    assert path.read_bytes() == bom + original.replace("// user", "// user edit").encode(codec)


def test_overlapping_regions_cannot_modify_another_owners_anchors(tmp_path):
    path = tmp_path / "shared.js"
    path.write_text("AaBbCcDd")
    target = FileTarget.capture(path, tmp_path, "text")
    store = ContributionStore(tmp_path)
    alpha, beta = ContributionOwner(tmp_path, "mods/a"), ContributionOwner(tmp_path, "mods/b")
    store.commit(store.plan_edits(alpha, [KeyEdit(target, ("A", "C"), "changed B text")]))
    before = path.read_bytes()
    with pytest.raises(ContributionConflict, match="overlap"):
        store.plan_edits(beta, [KeyEdit(target, ("B", "D"), "other")])
    assert path.read_bytes() == before


def test_repeated_or_missing_region_anchors_do_not_guess_an_edit(tmp_path):
    path = tmp_path / "shared.js"
    path.write_text("<A>one</A><A>two</A>")
    target = FileTarget.capture(path, tmp_path, "text")
    store = ContributionStore(tmp_path)
    owner = ContributionOwner(tmp_path, "mods/a")
    with pytest.raises(ValueError, match="exactly once"):
        store.plan_edits(owner, [KeyEdit(target, ("<A>", "</A>"), "replacement")])
    assert path.read_text() == "<A>one</A><A>two</A>"
