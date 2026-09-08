import json
from dataclasses import replace
import time
import pytest

from src.core.mod_contributions import ContributionOwner, ContributionStore, ContributionConflict, FileTarget, KeyEdit
from src.widgets.mod_conflict_dialog import ModConflictDialog
from PyQt6.QtWidgets import QMainWindow


def fixture(root):
    a, b = ContributionOwner(root, "mods/a"), ContributionOwner(root, "mods/b")
    target = FileTarget.capture(root / "shared.json", root, "json")
    target.path.write_text('{"value": 1, "manual": 3}')
    store = ContributionStore(root)
    store.commit(store.plan_edits(a, [KeyEdit(target, ("value",), 10)]))
    store.commit(store.plan_edits(b, [KeyEdit(target, ("value",), 20, allow_override=True)]))
    target.path.write_text('{"value": 99, "manual": 4}')
    return a, b, target, store


@pytest.mark.parametrize("preserve, expected", [(True, 99), (False, 10)])
def test_removal_review_retains_neighbors_and_preserves_original_bytes(tmp_path, preserve, expected):
    a, b, target, store = fixture(tmp_path)
    before = target.path.read_bytes()
    review = store.review_remove_locked([b])
    assert target.path.read_bytes() == before
    assert {owner.mod_relative_path for owner in review.owners} == {"mods/a", "mods/b"}
    chosen = review.preserve if preserve else review.restore
    result = store.commit(chosen)
    assert json.loads(target.path.read_text()) == {"value": expected, "manual": 4}
    if not preserve:
        backup = store.transactions / result.transaction_id / "0.before"
        assert backup.read_bytes() == before
    store.commit(store.plan_remove(a))
    assert json.loads(target.path.read_text()) == {"value": 99 if preserve else 1, "manual": 4}


def test_stale_review_cannot_overwrite_new_edits(tmp_path):
    _, b, target, store = fixture(tmp_path)
    review = store.review_remove_locked([b])
    target.path.write_text('{"value": 100, "manual": 5}')
    with pytest.raises(ContributionConflict, match="changed after planning"):
        store.commit(review.restore)
    assert json.loads(target.path.read_text()) == {"value": 100, "manual": 5}


def test_conflict_dialog_shows_actual_choices_without_writing(qapp, tmp_path):
    _, b, target, store = fixture(tmp_path)
    before = target.path.read_bytes()
    dialog = ModConflictDialog(store.review_remove_locked([b]))
    assert '99' in dialog.current.toPlainText() and '99' in dialog.proposed.toPlainText()
    dialog.policy.setCurrentIndex(1)
    assert '10' in dialog.proposed.toPlainText()
    dialog.accept()
    assert dialog.selected_plan == dialog.review.restore
    assert target.path.read_bytes() == before
    dialog.deleteLater()


def test_coordinator_reviews_commits_and_retries_after_worker_teardown(qapp, tmp_path, monkeypatch):
    from src.ui import mod_coordinator
    from src.core.mod_contributions import RemovalEditConflict, RemovalReviewRequired
    _, b, target, store = fixture(tmp_path)
    window = QMainWindow()
    window._close_in_progress = False
    window._set_operation_controls_busy = lambda _busy: None
    coordinator = mod_coordinator.ModCoordinator(window)
    class ChooseRestore(ModConflictDialog):
        def exec(self):
            self.policy.setCurrentIndex(1)
            self.accept()
            return 1
    monkeypatch.setattr(mod_coordinator, "ModConflictDialog", ChooseRestore)
    calls = []
    def operation():
        calls.append("attempt")
        try:
            plan = store.plan_remove(b)
        except RemovalEditConflict:
            raise RemovalReviewRequired(store.review_remove_locked([b]))
        store.commit(plan)
    coordinator.run(operation, coordinator._operation_result)
    deadline = time.monotonic() + 5
    while (len(calls) < 2 or coordinator._token is not None) and time.monotonic() < deadline:
        qapp.processEvents()
        if coordinator._thread is not None:
            coordinator._thread.wait(5)
    assert calls == ["attempt", "attempt"] and coordinator._token is None
    assert json.loads(target.path.read_text())["value"] == 10
    window.deleteLater()


def test_keep_current_helper_files_does_not_retry_or_write(qapp, tmp_path, monkeypatch):
    from src.ui import mod_coordinator
    from src.core.mod_contributions import RemovalReviewRequired
    _, owner, target, store = fixture(tmp_path)
    review = replace(store.review_remove_locked([owner]), kind="helper")
    before = target.path.read_bytes()
    window = QMainWindow()
    window._close_in_progress = False
    window._set_operation_controls_busy = lambda _busy: None
    coordinator = mod_coordinator.ModCoordinator(window)
    class KeepCurrent(ModConflictDialog):
        def exec(self):
            self.accept()
            return 1
    monkeypatch.setattr(mod_coordinator, "ModConflictDialog", KeepCurrent)
    calls = []
    def operation():
        calls.append("attempt")
        raise RemovalReviewRequired(review)
    coordinator.run(operation, coordinator._operation_result)
    deadline = time.monotonic() + 5
    while coordinator._token is not None and time.monotonic() < deadline:
        qapp.processEvents()
        if coordinator._thread is not None:
            coordinator._thread.wait(5)
    qapp.processEvents()
    assert calls == ["attempt"] and coordinator._token is None
    assert target.path.read_bytes() == before
    window.deleteLater()
