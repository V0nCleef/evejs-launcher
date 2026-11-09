"""Persist only explicit local approval for the exact tested combination."""
import json
import shutil
from pathlib import Path

import pytest

from src.app import MainWindow
from src.core.mod_compatibility_approvals import LocalCompatibilityApprovals, payload_fingerprint
from src.core.mod_manifest import scan_mods, set_mod_active
from src.core.mod_runtime_state import mod_state_key
from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lease, ModLifecycleBusyError
from src.ui.mod_start_compatibility import ModStartCompatibilityChoice, ModStartCompatibilityDecision, ModStartCompatibilityDialog
from test_app_mod_compatibility_choice import _root, _window, _write_loader


def _fixture(tmp_path):
    root = _root(tmp_path / "evejs")
    (root / "package.json").write_text('{"version":"261108.1"}')
    mod = scan_mods(root)[0]
    return root, mod


def _remember(root, mod, backend="native"):
    store = LocalCompatibilityApprovals(root)
    store.remember((mod,), backend, "261108.1", {mod_state_key(mod): payload_fingerprint(mod)})
    return store


def test_second_identical_start_has_no_dialog_but_payload_change_prompts(qapp, tmp_path):
    root, mod = _fixture(tmp_path)
    (mod.path / "module.js").write_text("module.exports = 1;\n")
    owner = _window(root)
    choices = []
    def choose(*args, **kwargs):
        choices.append(args)
        return ModStartCompatibilityDecision(ModStartCompatibilityChoice.REMEMBER, kwargs["mod_keys"])
    owner._choose_mod_start_compatibility = choose
    def prepare():
        mods = MainWindow._applicable_runtime_mods(str(root), backend="native")
        return owner._prepare_mod_start_compatibility(str(root), "native", mods, continuation=lambda: None, error_title="Start")
    try:
        assert prepare()[0] == "ready"
        assert len(choices) == 1
        assert prepare()[0] == "ready" and len(choices) == 1
        # The primary loader and version stay identical; a required file changes.
        (mod.path / "module.js").write_text("module.exports = 2;\n")
        assert prepare()[0] == "ready" and len(choices) == 2
        (root / "package.json").write_text('{"version":"261109.1"}')
        owner._choose_mod_start_compatibility = lambda *a, **k: choices.append(a) or ModStartCompatibilityChoice.CANCEL
        assert prepare()[0] == "cancelled" and len(choices) == 3
    finally:
        owner.deleteLater()


def test_different_build_backend_and_copied_root_do_not_inherit_approval(tmp_path):
    root, mod = _fixture(tmp_path)
    store = _remember(root, mod)
    assert store.approved(mod, "native", "261108.1")
    assert not store.approved(mod, "docker", "261108.1")
    assert not store.approved(mod, "native", "261109.1")
    copied = tmp_path / "copied"
    shutil.copytree(root, copied)
    assert not LocalCompatibilityApprovals(copied).approved(scan_mods(copied)[0], "native", "261108.1")


def test_declared_mutable_state_is_excluded_but_descriptor_and_payload_are_not(tmp_path):
    root, mod = _fixture(tmp_path)
    manifest = mod.path / "evejs-launcher.mod.json"
    document = json.loads(manifest.read_text())
    document["updates"] = {"provider":"github", "repository":"Author/Example", "asset":"Example-{version}.zip", "preserveFiles":["settings.json"]}
    manifest.write_text(json.dumps(document))
    settings = mod.path / "settings.json"
    settings.write_text('{"volume":1}')
    mod = scan_mods(root)[0]
    store = _remember(root, mod)
    settings.write_text('{"volume":2}')
    assert store.approved(scan_mods(root)[0], "native", "261108.1")
    document["description"] = "new same-version contract"
    manifest.write_text(json.dumps(document))
    assert not store.approved(scan_mods(root)[0], "native", "261108.1")


def test_disable_reenable_preserves_approval_and_forget_revokes_it(tmp_path):
    root, mod = _fixture(tmp_path)
    store = _remember(root, mod)
    set_mod_active(mod, False)
    disabled = scan_mods(root)[0]
    set_mod_active(disabled, True)
    assert store.approved(scan_mods(root)[0], "native", "261108.1")
    store.forget()
    assert not store.approved(scan_mods(root)[0], "native", "261108.1")


def test_changed_payload_during_dialog_is_rejected_without_storing(tmp_path):
    root, mod = _fixture(tmp_path)
    expected = {mod_state_key(mod):payload_fingerprint(mod)}
    (mod.path / "helper.js").write_text("changed")
    store = LocalCompatibilityApprovals(root)
    with pytest.raises(ValueError, match="payload changed"):
        store.remember((mod,), "native", "261108.1", expected)
    assert not store.approved(scan_mods(root)[0], "native", "261108.1")


@pytest.mark.parametrize("content", ["{", '{"schemaVersion":99}', '{"schemaVersion":1,"root":null,"records":{}}'])
def test_malformed_approvals_do_not_suppress_prompt(tmp_path, content):
    root, mod = _fixture(tmp_path)
    destination = root / "_local/launcher-mods/compatibility-approvals.json"
    destination.parent.mkdir(parents=True)
    destination.write_text(content)
    assert not LocalCompatibilityApprovals(root).approved(mod, "native", "261108.1")
    assert destination.read_text() == content


def test_unknown_version_cannot_be_remembered(qapp, tmp_path):
    root, mod = _fixture(tmp_path)
    with pytest.raises(ValueError):
        LocalCompatibilityApprovals(root).remember((mod,), "native", None, {mod_state_key(mod):payload_fingerprint(mod)})
    dialog = ModStartCompatibilityDialog((), None)
    try:
        assert not dialog.remember_button.isEnabled()
        assert dialog.cancel_button.isDefault()
        assert "Local user approval" in dialog.remember_button.accessibleDescription()
    finally:
        dialog.deleteLater()


def test_multiple_affected_mods_are_approved_atomically(tmp_path):
    root, mod = _fixture(tmp_path)
    _write_loader(root, "other-mod", ["261028.1"])
    mods = tuple(scan_mods(root))
    expected = {mod_state_key(item):payload_fingerprint(item) for item in mods}
    store = LocalCompatibilityApprovals(root)
    (mods[-1].path / "module.js").write_text("changed")
    with pytest.raises(ValueError):
        store.remember(mods, "native", "261108.1", expected)
    assert not any(store.approved(item, "native", "261108.1") for item in scan_mods(root))


def test_local_consent_rechecks_nested_payload_before_spawn(qapp, tmp_path):
    root, mod = _fixture(tmp_path)
    _remember(root, mod)
    owner = _window(root)
    owner._choose_mod_start_compatibility = lambda *a, **k: pytest.fail("Remembered combination prompted")
    mods = tuple(scan_mods(root))
    try:
        outcome, consent = owner._prepare_mod_start_compatibility(str(root), "native", mods, continuation=lambda:None, error_title="Start")
        assert outcome == "ready" and consent.matches(root,"native","261108.1",mods)
        (mod.path / "nested.js").write_text("changed")
        assert not consent.matches(root,"native","261108.1",tuple(scan_mods(root)))
    finally:
        owner.deleteLater()


def test_remember_reuses_owned_lease_and_refuses_a_competing_writer(tmp_path):
    root, mod = _fixture(tmp_path)
    store = LocalCompatibilityApprovals(root)
    expected = {mod_state_key(mod): payload_fingerprint(mod)}
    lease = acquire_mod_lifecycle_lease(root)
    try:
        with pytest.raises(ModLifecycleBusyError):
            store.remember((mod,), "native", "261108.1", expected)
        store.remember((mod,), "native", "261108.1", expected, lease=lease)
        assert store.approved(mod, "native", "261108.1")
    finally:
        lease.release()
    with pytest.raises(ValueError, match="lease"):
        store.remember((mod,), "native", "261108.1", expected, lease=lease)


def test_docker_start_remembers_only_its_backend(qapp, tmp_path):
    root, mod = _fixture(tmp_path)
    owner = _window(root)
    owner._docker_mode = lambda: True
    owner._choose_mod_start_compatibility = lambda *a, **k: ModStartCompatibilityDecision(ModStartCompatibilityChoice.REMEMBER, k["mod_keys"])
    mods = MainWindow._applicable_runtime_mods(str(root), backend="docker")
    try:
        assert mods
        assert owner._prepare_mod_start_compatibility(str(root), "docker", mods, continuation=lambda:None, error_title="Start")[0] == "ready"
        store = LocalCompatibilityApprovals(root)
        assert store.approved(mod, "docker_compose", "261108.1")
        assert not store.approved(mod, "native", "261108.1")
    finally:
        owner.deleteLater()


def test_disable_affected_does_not_disable_previously_approved_mod(qapp, tmp_path):
    root, mod = _fixture(tmp_path)
    _remember(root, mod)
    _write_loader(root, "second-mod", ["261028.1"])
    owner = _window(root)
    owner._choose_mod_start_compatibility = lambda *a, **k: ModStartCompatibilityChoice.DISABLE_AFFECTED
    selected = []
    owner._queue_compatibility_mod_disable = lambda root, backend, mods, **kwargs: selected.extend(mods) or True
    try:
        outcome, _ = owner._prepare_mod_start_compatibility(str(root), "native", tuple(scan_mods(root)), continuation=lambda:None, error_title="Start")
        assert outcome == "queued"
        assert [item.id for item in selected] == ["second-mod"]
    finally:
        owner.deleteLater()


def test_mod_page_exposes_revocation_and_translated_copy(qapp, tmp_path, monkeypatch):
    from PyQt6.QtTest import QSignalSpy
    from src import config
    from src.i18n import translate_ui_phrase
    from src.pages.mods_page import ModsPage
    from src.translations_mod_approvals import SOURCE_PHRASES, UI_PHRASES_BY_LANGUAGE
    monkeypatch.setattr(config, "get_setting", lambda key: "")
    page = ModsPage(defer_inventory=True)
    try:
        spy = QSignalSpy(page.forget_compatibility_requested)
        page.set_evejs_root(str(tmp_path))
        page.forget_compatibility_btn.click()
        assert len(spy) == 1
        page.set_lifecycle_busy(True)
        assert not page.forget_compatibility_btn.isEnabled()
        for language, catalog in UI_PHRASES_BY_LANGUAGE.items():
            assert set(catalog) == set(SOURCE_PHRASES)
            assert translate_ui_phrase("Forget local approvals", language) != "Forget local approvals"
            assert "{version}" in catalog["Remember selected for EveJS {version}"]
    finally:
        page.deleteLater()
