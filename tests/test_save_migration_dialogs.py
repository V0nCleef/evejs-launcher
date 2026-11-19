"""Real Qt transfer dialogs report outcomes without slowing the transaction."""
import json
import time

import pytest
from PyQt6.QtCore import QEvent, QThread, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QCheckBox, QDialog, QLabel, QPlainTextEdit, QPushButton, QWidget

from src.core.save_migration import SaveMigrationError, TransferResult, verify_stock_game
from src.i18n import set_language, translate_ui_phrase
from src.pages import save_migration_page as ui


def page_for(tmp_path, *, names=("Copy", "Move"), backends=("native", "native")):
    owner = QWidget()
    owner.setProperty("animationsEnabled", False)
    page = ui.SaveMigrationPage(parent=owner, verifier=verify_stock_game,
                               can_start=lambda: True, installation_label=lambda entry: entry["name"])
    entries = []
    for identity, name, backend in zip(("source", "target"), names, backends):
        root = tmp_path / identity
        (root / "config").mkdir(parents=True)
        (root / "config/version.json").write_text(json.dumps({"evejsVersion": "261115.2"}))
        for relative in ("src/state.rs", "crates/market-common/src/lib.rs"):
            definition = root / "externalservices/market-server" / relative
            definition.parent.mkdir(parents=True, exist_ok=True)
            definition.write_text("pub const MARKET_SCHEMA_VERSION: u32 = 1;")
        entries.append({"id": identity, "name": name,
                        "settings": {"evejs_root": str(root), "runtime_backend": backend}})
    page.set_installations(entries, "source")
    page.bring.setChecked(True)
    return owner, page


class DialogDriver:
    def __init__(self, page, *, back=False, enter=False):
        self.page, self.back, self.enter = page, back, enter
        self.reviews, self.outcomes, self.errors = [], [], []
        self.timer = QTimer(page)
        self.timer.setInterval(5)
        self.timer.timeout.connect(self.tick)
        self.guard = QTimer(page)
        self.guard.setSingleShot(True)
        self.guard.timeout.connect(self.timeout)

    def start(self):
        self.timer.start()
        self.guard.start(5000)

    def stop(self):
        self.timer.stop()
        self.guard.stop()

    def timeout(self):
        self.errors.append("A migration dialog did not finish promptly")
        for dialog in self.page.findChildren(QDialog):
            if dialog.isVisible() and not isinstance(dialog, ui._TransferProgressDialog):
                dialog.reject()

    def tick(self):
        try:
            for dialog in self.page.findChildren(QDialog):
                if not dialog.isVisible():
                    continue
                if dialog.objectName() == "saveMigrationReview":
                    buttons = dialog.findChildren(QPushButton)
                    action = "Move playthrough" if self.page.move.isChecked() else "Copy playthrough"
                    confirm = next(button for button in buttons if button.text() == translate_ui_phrase(action))
                    back = next(button for button in buttons if button.text() == translate_ui_phrase("Back"))
                    self.reviews.append({"title": dialog.windowTitle(), "confirmEnabled": confirm.isEnabled(),
                                         "backDefault": back.isDefault(), "width": dialog.width(),
                                         "literalNames": [label.accessibleName() for label in dialog.findChildren(QLabel)
                                                          if label.property("i18nIgnore")],
                                         "labels": [label.text() for label in dialog.findChildren(QLabel)]})
                    if self.back and self.enter:
                        QTest.keyClick(dialog, Qt.Key.Key_Return)
                    else:
                        (back if self.back else confirm).click()
                elif dialog.objectName() == "saveMigrationOutcome":
                    detail = dialog.findChild(QPlainTextEdit, "saveMigrationDetail")
                    route = dialog.findChild(QLabel, "saveMigrationRoute")
                    self.outcomes.append({"title": dialog.windowTitle(), "heading": dialog.heading.text(),
                                          "route": route.text(), "detail": detail.toPlainText() if detail else "",
                                          "literalRoute": route.accessibleName(),
                                          "detailReadOnly": detail.isReadOnly() if detail else None,
                                          "accent": dialog.flight._accent.name(), "position": dialog.flight._target,
                                          "timer": dialog.flight._timer, "width": dialog.width()})
                    next(button for button in dialog.findChildren(QPushButton)
                         if button.text() == translate_ui_phrase("OK")).click()
        except Exception as error:
            self.errors.append(repr(error))
            self.timeout()


def dispose(qapp, owner, driver=None):
    if driver:
        driver.stop()
    owner.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("outcome", ["copied", "moved", "copy-only", "stopped", "empty-error"])
def test_dialogs_follow_worker_outcome_without_waiting_for_flight(qapp, tmp_path, monkeypatch, outcome):
    set_language("en")
    owner, page = page_for(tmp_path)
    page.move.setChecked(outcome in ("moved", "copy-only"))
    backup = tmp_path / "backup"
    note = "The destination copy passed. Source removal stopped. Disposable source lock"
    error = "Game still owns this save. Stop it and let its last write lease expire."
    failed = outcome in ("stopped", "empty-error")
    if outcome == "empty-error":
        error = "RuntimeError"
    phases = [
        "Checking that both installations are stopped…",
        "Backing up and verifying the playthrough and market…",
        "Checking the game and market formats…",
    ]
    if not failed:
        phases.append("Installing the verified save at the destination…")
        if page.move.isChecked():
            phases.append("Removing the original save after verification…")
    seen, ownership, completed, threads = [], [], [], []
    update = ui._TransferProgressDialog.update_progress

    def observe(dialog, text):
        threads.append(QThread.currentThread() is qapp.thread())
        update(dialog, text)
        seen.append((text, dialog.flight._target))

    def migrate(request, *, progress, verify_game):
        assert QThread.currentThread() is not qapp.thread()
        for phase in phases:
            progress(phase)
        if outcome == "stopped":
            raise SaveMigrationError(error)
        if outcome == "empty-error":
            raise RuntimeError()
        return TransferResult(backup, outcome == "moved", note if outcome == "copy-only" else "")

    monkeypatch.setattr(ui._TransferProgressDialog, "update_progress", observe)
    monkeypatch.setattr(ui, "migrate", migrate)
    page.busy_changed.connect(ownership.append)
    page.transfer_finished.connect(completed.append)
    driver = DialogDriver(page)
    driver.start()
    try:
        started = time.monotonic()
        page._review()
        while not driver.outcomes and time.monotonic() - started < 5:
            QTest.qWait(10)
        assert not driver.errors
        assert len(driver.reviews) == len(driver.outcomes) == 1
        assert driver.reviews[0]["backDefault"] and driver.reviews[0]["confirmEnabled"]
        actual = driver.outcomes[0]
        expected = "Save Migration stopped" if failed else (
            "Playthrough moved." if outcome == "moved" else "Playthrough copied.")
        assert actual["title"] == actual["heading"] == expected
        assert actual["route"] == "Copy  →  Move"
        assert actual["detail"] == (error if failed else note if outcome == "copy-only" else "")
        assert actual["accent"] == ("#ff8c96" if failed else "#ffcf72" if outcome == "copy-only" else "#72f0c1")
        assert actual["position"] == (0.0 if failed else 1.0)
        if actual["detail"]:
            assert actual["detailReadOnly"]
        assert [text for text, position in seen] == phases
        positions = [position for text, position in seen]
        assert all(0 < position < 1 for position in positions)
        assert all(left < right for left, right in zip(positions, positions[1:]))
        assert all(threads) and not page.busy
        assert len(ownership) == 2 and ownership[-1] is None
        assert len(completed) == (0 if failed else 1)
        assert page._last_backup == (None if failed else backup)
    finally:
        dispose(qapp, owner, driver)


@pytest.mark.parametrize("backends", [("native", "docker_compose"), ("docker_compose", "native"),
                                     ("docker_compose", "docker_compose")])
def test_dialog_flow_preserves_selected_backend_route(qapp, tmp_path, monkeypatch, backends):
    owner, page = page_for(tmp_path, backends=backends)
    requests = []
    def migrate(request, **kwargs):
        requests.append(request)
        return TransferResult(tmp_path / "backup", False)
    monkeypatch.setattr(ui, "migrate", migrate)
    driver = DialogDriver(page)
    driver.start()
    try:
        page._review()
        deadline = time.monotonic() + 5
        while not driver.outcomes and time.monotonic() < deadline:
            QTest.qWait(10)
        assert not driver.errors and len(requests) == len(driver.outcomes) == 1
        assert tuple(record.settings["runtime_backend"] for record in
                     (requests[0].source, requests[0].destination)) == backends
        assert driver.outcomes[0]["title"] == "Playthrough copied."
        assert "Docker" in (page.source_detail.text() + page.target_detail.text())
    finally:
        dispose(qapp, owner, driver)


def test_preflight_rejection_shows_stopped_without_starting_worker(qapp, tmp_path, monkeypatch):
    owner, page = page_for(tmp_path)
    target = tmp_path / "target/config/version.json"
    target.write_text(json.dumps({"evejsVersion": "261115.3"}))
    called = []
    monkeypatch.setattr(ui._TransferThread, "start", lambda worker: called.append(worker))
    driver = DialogDriver(page)
    driver.start()
    try:
        page._review()
        assert not driver.errors and not called and not driver.reviews
        assert driver.outcomes[0]["title"] == "Save Migration stopped"
        assert "same verified EveJS base version" in driver.outcomes[0]["detail"]
        assert not page.busy
    finally:
        dispose(qapp, owner, driver)


@pytest.mark.parametrize("enter", [False, True], ids=["back-button", "default-enter"])
def test_back_dismisses_review_without_mutation(qapp, tmp_path, monkeypatch, enter):
    owner, page = page_for(tmp_path)
    called = []
    monkeypatch.setattr(ui._TransferThread, "start", lambda worker: called.append(worker))
    if enter:
        page.fresh.setChecked(True)
    driver = DialogDriver(page, back=True, enter=enter)
    driver.start()
    try:
        page._review()
        assert not called and not driver.errors and not driver.outcomes
        assert driver.reviews[0]["backDefault"]
        assert not page.busy
    finally:
        dispose(qapp, owner, driver)


def test_repeated_transfers_retire_dialogs_flights_and_workers(qapp, tmp_path, monkeypatch):
    from src.widgets.save_transfer_flight import SaveTransferFlight
    owner, page = page_for(tmp_path)
    monkeypatch.setattr(ui, "migrate", lambda *args, **kwargs: TransferResult(tmp_path / "backup", False))
    driver = DialogDriver(page)
    try:
        for count in range(1, 4):
            driver.start()
            page._review()
            deadline = time.monotonic() + 5
            while len(driver.outcomes) < count and time.monotonic() < deadline:
                QTest.qWait(10)
            driver.stop()
            assert not driver.errors and len(driver.outcomes) == count
            qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert not page.busy
            assert not page.findChildren(SaveTransferFlight)
            assert not page.findChildren(ui._TransferThread)
    finally:
        dispose(qapp, owner, driver)


@pytest.mark.parametrize("screen", ["review", "result"])
def test_long_names_fit_a_small_dialog_and_remain_accessible(qapp, tmp_path, screen):
    names = ("COPY" * 20, "MOVE" * 20)
    owner, page = page_for(tmp_path, names=names)
    driver = DialogDriver(page, back=True)
    driver.start()
    try:
        if screen == "review":
            page._review()
            actual = driver.reviews[0]
            assert all(name in actual["literalNames"] for name in names)
        else:
            request = ui.Transfer(page.source.currentData(), page.target.currentData(), False, True)
            page._show_outcome(request, "Playthrough copied.")
            actual = driver.outcomes[0]
            assert actual["literalRoute"] == f"{names[0]}  →  {names[1]}"
        assert not driver.errors
        assert actual["width"] <= min(768, qapp.primaryScreen().availableGeometry().width() - 32)
    finally:
        dispose(qapp, owner, driver)


@pytest.mark.parametrize("language", ["nl", "de", "fr", "ru", "zh_CN", "ja", "ko"])
def test_outcome_translation_preserves_names_and_diagnostic_path(qapp, tmp_path, language):
    owner, page = page_for(tmp_path)
    request = ui.Transfer(page.source.currentData(), page.target.currentData(), False, True)
    diagnostic = "Game still owns this save. Stop it and let its last write lease expire."
    path = str(tmp_path / "Copy" / "Move")
    driver = DialogDriver(page)
    try:
        set_language(language)
        driver.start()
        page._show_outcome(request, "Save Migration stopped", diagnostic + "\n" + path)
        assert not driver.errors and len(driver.outcomes) == 1
        actual = driver.outcomes[0]
        assert actual["title"] == actual["heading"] == translate_ui_phrase("Save Migration stopped")
        assert actual["route"] == "Copy  →  Move"
        assert actual["detail"] == translate_ui_phrase(diagnostic) + "\n" + path
    finally:
        set_language("en")
        dispose(qapp, owner, driver)


def test_flight_tracks_stages_without_inventing_progress_and_stops_when_hidden(qapp):
    owner = QWidget()
    owner.setProperty("animationsEnabled", True)
    dialog = ui._TransferProgressDialog(owner)
    try:
        dialog.show()
        QTest.qWait(80)
        flight = dialog.flight
        assert flight._timer.isActive() and flight._phase > 0
        assert flight._target == flight._position == 0.0
        dialog.update_progress("Checking the game and market formats…")
        QTest.qWait(90)
        assert 0 < flight._position < flight._target == .44
        dialog.update_progress("A literal technical diagnostic")
        assert flight._target == .44 and dialog.message.text() == "A literal technical diagnostic"
        dialog.hide()
        phase = flight._phase
        QTest.qWait(70)
        assert not flight._timer.isActive() and flight._phase == phase
    finally:
        dispose(qapp, owner)


def test_motion_preference_is_static_and_updates_an_open_dialog(qapp):
    owner = QWidget()
    owner.setProperty("animationsEnabled", False)
    dialog = ui._TransferProgressDialog(owner)
    try:
        dialog.show()
        dialog.update_progress("Installing the verified save at the destination…")
        QTest.qWait(60)
        assert not dialog.flight._timer.isActive()
        assert dialog.flight._position == dialog.flight._target == .71
        first = dialog.flight.grab().toImage()
        QTest.qWait(70)
        assert dialog.flight.grab().toImage() == first
        owner.setProperty("animationsEnabled", True)
        QTest.qWait(70)
        assert dialog.flight._timer.isActive() and dialog.flight._phase > 0
        owner.setProperty("animationsEnabled", False)
        QTest.qWait(70)
        assert not dialog.flight._timer.isActive()
        assert dialog.flight._position == dialog.flight._target
    finally:
        dispose(qapp, owner)
