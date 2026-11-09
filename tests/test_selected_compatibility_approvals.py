"""Remember only checked physical mods, then review the unresolved remainder."""
import pytest
from PyQt6.QtWidgets import QCheckBox

from src.core.mod_compatibility_approvals import LocalCompatibilityApprovals, payload_fingerprint
from src.core.mod_evejs_compatibility import assess_active_runtime_mods
from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lease
from src.core.mod_manifest import scan_mods, set_mod_active
from src.core.mod_runtime_state import mod_state_key
from src.ui import mod_start_compatibility as ui
from test_app_mod_compatibility_choice import _root, _window, _write_loader


def _review(tmp_path, backend="native"):
    root = _root(tmp_path / "evejs")
    (root / "package.json").write_text('{"version":"261108.1"}')
    for index in range(4):
        _write_loader(root, f"extra-{index}", ["261028.1"])
    owner = _window(root)
    owner._docker_mode = lambda: backend != "native"
    owner._mod_lifecycle_lease = acquire_mod_lifecycle_lease(root)
    mods = owner._applicable_runtime_mods(str(root), backend=backend)
    return root, owner, mods


def _prepare(owner, root, backend="native", **kwargs):
    return owner._prepare_mod_start_compatibility(
        str(root), backend, owner._applicable_runtime_mods(str(root), backend=backend),
        continuation=lambda: pytest.fail("Compatibility review cannot start services"),
        error_title="Start", **kwargs,
    )


def _remember(keys):
    return ui.ModStartCompatibilityDecision(ui.ModStartCompatibilityChoice.REMEMBER, tuple(keys))


def _close(owner):
    owner._release_mod_lifecycle_lease()
    owner.deleteLater()


def test_real_dialog_returns_only_two_checked_mod_keys(qapp, tmp_path):
    root, owner, mods = _review(tmp_path)
    keys = tuple(mod_state_key(mod) for mod in mods)
    dialog = ui.ModStartCompatibilityDialog(
        assess_active_runtime_mods(mods, "261108.1"), "261108.1", mod_keys=keys,
    )
    try:
        checks = dialog.findChildren(QCheckBox)
        assert len(checks) == 5 and not any(check.isChecked() for check in checks)
        assert not dialog.remember_button.isEnabled()
        dialog._remember()
        assert dialog.choice is ui.ModStartCompatibilityChoice.CANCEL
        checks[1].click()
        checks[4].click()
        assert dialog.remember_button.isEnabled()
        checks[1].click()
        checks[4].click()
        assert not dialog.remember_button.isEnabled()
        checks[1].click()
        checks[4].click()
        assert all(check.accessibleName() and check.accessibleDescription() for check in checks)
        dialog.remember_button.click()
        assert dialog.choice is ui.ModStartCompatibilityChoice.REMEMBER
        assert dialog.selected_mod_keys == (keys[1], keys[4])
        assert "selected" in dialog.remember_button.text()
        assert dialog.cancel_button.isDefault()
    finally:
        dialog.deleteLater()
        _close(owner)


def test_mainwindow_chooser_returns_the_actual_checkbox_selection(qapp, tmp_path, monkeypatch):
    root, owner, mods = _review(tmp_path)
    keys = tuple(mod_state_key(mod) for mod in mods)

    def choose_in_dialog(dialog):
        dialog.mod_checkboxes[keys[0]].click()
        dialog.mod_checkboxes[keys[3]].click()
        dialog.remember_button.click()
        return dialog.result()

    monkeypatch.setattr(ui.ModStartCompatibilityDialog, "exec", choose_in_dialog)
    try:
        decision = owner._choose_mod_start_compatibility(
            assess_active_runtime_mods(mods, "261108.1"), "261108.1", mod_keys=keys,
        )
        assert decision == _remember((keys[0], keys[3]))
    finally:
        _close(owner)


@pytest.mark.parametrize("version", [None, "unknown", ""])
def test_unknown_build_cannot_remember_even_with_checked_mod(qapp, tmp_path, version):
    root, owner, mods = _review(tmp_path)
    dialog = ui.ModStartCompatibilityDialog(assess_active_runtime_mods(mods, None), version)
    try:
        dialog.findChildren(QCheckBox)[0].click()
        assert not dialog.remember_button.isEnabled()
        dialog._remember()
        assert dialog.choice is ui.ModStartCompatibilityChoice.CANCEL
        assert not dialog.selected_mod_keys
    finally:
        dialog.deleteLater()
        _close(owner)


@pytest.mark.parametrize("backend", ["native", "docker"])
@pytest.mark.parametrize("remaining_choice", ["cancel", "run_anyway", "disable_affected"])
def test_partial_remember_then_remaining_choice(qapp, tmp_path, backend, remaining_choice):
    root, owner, mods = _review(tmp_path, backend)
    keys = tuple(mod_state_key(mod) for mod in mods)
    selected = (keys[1], keys[4])
    reviews, disabled = [], []

    def choose(results, version, **kwargs):
        reviews.append(tuple(kwargs["mod_keys"]))
        if len(reviews) == 1:
            return _remember(selected)
        return ui.ModStartCompatibilityChoice(remaining_choice)

    def disable(_root, _backend, affected, **kwargs):
        disabled.extend(mod_state_key(mod) for mod in affected)
        owner._release_mod_lifecycle_lease()
        for mod in affected:
            set_mod_active(mod, False)
        return True

    owner._choose_mod_start_compatibility = choose
    owner._queue_compatibility_mod_disable = disable
    try:
        outcome, consent = _prepare(owner, root, backend)
        assert reviews == [keys, tuple(key for key in keys if key not in selected)]
        store = LocalCompatibilityApprovals(root)
        assert {mod_state_key(mod) for mod in mods if store.approved(mod, backend, "261108.1")} == set(selected)
        other_backend = "docker" if backend == "native" else "native"
        assert not any(store.approved(mod, other_backend, "261108.1") for mod in mods)
        assert outcome == {"cancel": "cancelled", "run_anyway": "ready", "disable_affected": "queued"}[remaining_choice]
        if remaining_choice == "run_anyway":
            assert consent.matches(root, backend, "261108.1", mods)
        elif remaining_choice == "disable_affected":
            assert disabled == [key for key in keys if key not in selected]
            assert {mod_state_key(mod) for mod in scan_mods(root) if mod.active} == set(selected)
        else:
            assert consent is None
            assert all(mod.active for mod in scan_mods(root))
            owner._choose_mod_start_compatibility = lambda results, version, **kw: reviews.append(tuple(kw["mod_keys"])) or ui.ModStartCompatibilityChoice.CANCEL
            assert _prepare(owner, root, backend)[0] == "cancelled"
            assert reviews[-1] == tuple(key for key in keys if key not in selected)
    finally:
        _close(owner)


def test_selecting_all_mods_finishes_without_a_second_dialog(qapp, tmp_path):
    root, owner, mods = _review(tmp_path)
    calls = []
    owner._choose_mod_start_compatibility = lambda results, version, **kw: calls.append(kw) or _remember(kw["mod_keys"])
    try:
        assert _prepare(owner, root)[0] == "ready"
        assert len(calls) == 1
        assert len(LocalCompatibilityApprovals(root)._load()["records"]) == 5
    finally:
        _close(owner)


@pytest.mark.parametrize("selection", ["bare-enum", "empty", "duplicate", "foreign"])
def test_invalid_or_implicit_remember_never_approves_all(qapp, tmp_path, selection):
    root, owner, mods = _review(tmp_path)
    key = mod_state_key(mods[0])
    def choose(*args, **kwargs):
        if selection == "bare-enum":
            return ui.ModStartCompatibilityChoice.REMEMBER
        return _remember({"empty": (), "duplicate": (key, key), "foreign": ("path:mods/unreviewed",)}[selection])
    owner._choose_mod_start_compatibility = choose
    try:
        assert _prepare(owner, root)[0] == "cancelled"
        assert not LocalCompatibilityApprovals(root)._load()["records"]
    finally:
        _close(owner)


@pytest.mark.parametrize("stage", [1, 2])
@pytest.mark.parametrize("backend,drift", [
    (backend, drift)
    for backend in ("native", "docker")
    for drift in ("selected-payload", "remaining-payload", "root", "backend", "version", "docker-target", "order")
    if backend == "docker" or drift != "docker-target"
])
def test_each_review_choice_rechecks_payload_and_target(qapp, tmp_path, backend, stage, drift):
    root, owner, mods = _review(tmp_path, backend)
    keys = tuple(mod_state_key(mod) for mod in mods)
    calls = []
    owner._docker_target_identity = lambda: ("original",)
    owner._current_observed_docker_target_identity = lambda: "docker:original"

    def choose(results, version, **kwargs):
        calls.append(kwargs)
        if len(calls) == stage:
            if drift.endswith("payload"):
                mod = mods[0] if drift == "selected-payload" else mods[-1]
                (mod.path / "nested.js").write_text("changed during review")
            elif drift == "root":
                owner._cfg["evejs_root"] = str(tmp_path)
            elif drift == "backend":
                owner._docker_mode = lambda: backend == "native"
            elif drift == "version":
                (root / "package.json").write_text('{"version":"261109.1"}')
            elif drift == "order":
                owner._applicable_runtime_mods = lambda *a, **kw: tuple(reversed(mods))
            else:
                owner._current_observed_docker_target_identity = lambda: "docker:changed"
        return _remember((keys[0],)) if len(calls) == 1 else ui.ModStartCompatibilityChoice.RUN_ANYWAY

    owner._choose_mod_start_compatibility = choose
    context = {"docker_target_identity": ("original",), "expected_observed_target": "docker:original"} if backend == "docker" else {}
    try:
        assert _prepare(owner, root, backend, **context)[0] == "cancelled"
        # A successful first explicit Remember persists even if later review drifts.
        assert len(LocalCompatibilityApprovals(root)._load()["records"]) == (0 if stage == 1 else 1)
    finally:
        _close(owner)


def test_preapproved_mod_stays_enabled_after_partial_remember_and_disable(qapp, tmp_path):
    root, owner, mods = _review(tmp_path)
    _write_loader(root, "preapproved-mod", ["261028.1"])
    all_mods = owner._applicable_runtime_mods(str(root), backend="native")
    preapproved = next(mod for mod in all_mods if mod.id == "preapproved-mod")
    store = LocalCompatibilityApprovals(root)
    store.remember((preapproved,), "native", "261108.1",
                   {mod_state_key(preapproved): payload_fingerprint(preapproved)},
                   lease=owner._mod_lifecycle_lease)
    selected = tuple(mod_state_key(mod) for mod in mods[:2])
    reviews, disabled = [], []

    def choose(results, version, **kwargs):
        reviews.append(tuple(kwargs["mod_keys"]))
        return _remember(selected) if len(reviews) == 1 else ui.ModStartCompatibilityChoice.DISABLE_AFFECTED

    def disable(_root, backend, remaining, **kwargs):
        owner._release_mod_lifecycle_lease()
        for mod in remaining:
            disabled.append(mod_state_key(mod))
            set_mod_active(mod, False)
        return True

    owner._choose_mod_start_compatibility = choose
    owner._queue_compatibility_mod_disable = disable
    try:
        assert _prepare(owner, root)[0] == "queued"
        assert [len(review) for review in reviews] == [5, 3]
        assert len(disabled) == 3
        kept = {*selected, mod_state_key(preapproved)}
        assert {mod_state_key(mod) for mod in scan_mods(root) if mod.active} == kept
        assert {row["modKey"] for row in store._load()["records"].values()} == kept
    finally:
        _close(owner)
