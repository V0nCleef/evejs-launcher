"""Qt ownership, stale-delivery, and shutdown tests for portraits."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import threading
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QObject, QThread, Qt
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

from src import app as app_module
from src import config
from src.app import MainWindow
from src.core.db import Account, Character
from src.core.process_tracker import ProcessTracker
from src.core.runtime.portraits import (
    PortraitImageResult,
    PortraitRequest,
    PortraitTarget,
)
from src.pages import characters_page as characters_page_module
from src.pages.characters_page import CharactersPage
from src.utils.cache import PortraitCache
from src.workers.portrait_worker import PortraitLoader


def _wait_until(qapp: QApplication, predicate, timeout_ms: int = 2_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    assert predicate()


def _image(color: QColor, size: int = 128) -> QImage:
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(color)
    return image


def _account() -> Account:
    return Account(
        username="fixture-account",
        account_id=501,
        role="0",
        banned=False,
        characters=[Character(char_id=9001, name="Fixture Character")],
    )


def _close_page(qapp: QApplication, page: CharactersPage) -> None:
    page.cancel_portrait_loads(invalidate=True)
    _wait_until(qapp, lambda: not page.portrait_loads_active())
    page.deleteLater()
    qapp.processEvents()


def test_portrait_worker_factory_runs_off_gui_thread_and_emits_qimage(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    gui_thread_id = threading.get_ident()
    provider_thread_ids: list[int] = []
    received: list[PortraitImageResult] = []
    target = PortraitTarget("native:worker", native_root=tmp_path)
    request = PortraitRequest("native:worker", 9001, 128, 1, object())

    class Provider:
        def __init__(self, actual_target, *, cache_dir: Path) -> None:
            assert actual_target == target
            assert cache_dir == tmp_path
            provider_thread_ids.append(threading.get_ident())

        def load(self, actual_request: PortraitRequest) -> PortraitImageResult:
            assert actual_request is request
            return PortraitImageResult(actual_request, _image(QColor("red")), "native")

    thread = QThread()
    worker = PortraitLoader(
        target,
        request,
        cache_dir=tmp_path,
        provider_factory=Provider,
    )
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.loaded.connect(received.append)
    worker.cleanup.connect(worker.deleteLater, Qt.ConnectionType.DirectConnection)
    worker.destroyed.connect(thread.quit)
    thread.start()

    try:
        _wait_until(qapp, lambda: bool(received) and not thread.isRunning())
        assert provider_thread_ids
        assert provider_thread_ids[0] != gui_thread_id
        assert isinstance(received[0].image, QImage)
        assert not isinstance(received[0].image, QPixmap)
        assert received[0].request is request
    finally:
        if thread.isRunning():
            worker.request_cancel()
            thread.quit()
            _wait_until(qapp, lambda: not thread.isRunning())
        thread.deleteLater()
        qapp.processEvents()


def test_characters_page_creates_qpixmap_only_in_gui_receiver(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    PortraitCache.clear()
    started = threading.Event()
    release = threading.Event()
    provider_threads: list[int] = []
    receiver_threads: list[int] = []
    target = PortraitTarget("native:page-gui", native_root=tmp_path)

    class Provider:
        def __init__(self, actual_target, *, cache_dir: Path) -> None:
            assert actual_target == target

        def load(self, request: PortraitRequest) -> PortraitImageResult:
            provider_threads.append(threading.get_ident())
            started.set()
            assert release.wait(2.0)
            return PortraitImageResult(request, _image(QColor("blue")), "native")

    class InjectedLoader(PortraitLoader):
        def __init__(self, actual_target, request) -> None:
            super().__init__(
                actual_target,
                request,
                cache_dir=tmp_path,
                provider_factory=Provider,
            )

    monkeypatch.setattr(characters_page_module, "PortraitLoader", InjectedLoader)
    page = CharactersPage()
    try:
        page.refresh([_account()], [], ProcessTracker(), portrait_target=target)
        _wait_until(qapp, started.is_set)
        card = page._cards[("fixture-account", 9001)]
        original_set_portrait = card.set_portrait

        def capture_receiver(pixmap) -> None:
            receiver_threads.append(threading.get_ident())
            original_set_portrait(pixmap)

        card.set_portrait = capture_receiver  # type: ignore[method-assign]
        release.set()
        _wait_until(
            qapp,
            lambda: card._portrait_pixmap is not None
            and not page.portrait_loads_active(),
        )

        assert provider_threads and provider_threads[0] != threading.get_ident()
        assert receiver_threads == [threading.get_ident()]
        assert isinstance(card._portrait_pixmap, QPixmap)
    finally:
        release.set()
        _close_page(qapp, page)


def test_target_switch_clears_visible_portrait_and_rejects_late_old_result(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    PortraitCache.clear()
    starts = {"native:old": threading.Event(), "native:new": threading.Event()}
    releases = {"native:old": threading.Event(), "native:new": threading.Event()}
    old_target = PortraitTarget("native:old", native_root=tmp_path / "old")
    new_target = PortraitTarget("native:new", native_root=tmp_path / "new")

    class Provider:
        def __init__(self, target, *, cache_dir: Path) -> None:
            self.target = target

        def load(self, request: PortraitRequest) -> PortraitImageResult:
            identity = self.target.target_identity
            starts[identity].set()
            assert releases[identity].wait(2.0)
            color = QColor("red") if identity == "native:old" else QColor("blue")
            return PortraitImageResult(request, _image(color), "native")

    class InjectedLoader(PortraitLoader):
        def __init__(self, target, request) -> None:
            super().__init__(
                target,
                request,
                cache_dir=tmp_path / "cache",
                provider_factory=Provider,
            )

    monkeypatch.setattr(characters_page_module, "PortraitLoader", InjectedLoader)
    page = CharactersPage()
    try:
        page.refresh([_account()], [], ProcessTracker(), portrait_target=old_target)
        _wait_until(qapp, starts["native:old"].is_set)
        old_request = next(
            request
            for _loader, request in page._portrait_threads.values()
            if request.target_identity == "native:old"
        )

        page.refresh([_account()], [], ProcessTracker(), portrait_target=new_target)
        _wait_until(qapp, starts["native:new"].is_set)
        card = page._cards[("fixture-account", 9001)]
        assert card._portrait_pixmap is None

        page._on_portrait_loaded(
            PortraitImageResult(old_request, _image(QColor("red")), "native")
        )
        assert card._portrait_pixmap is None

        releases["native:new"].set()
        _wait_until(qapp, lambda: card._portrait_pixmap is not None)
        assert card._portrait_pixmap.toImage().pixelColor(0, 0) == QColor("blue")

        releases["native:old"].set()
        _wait_until(qapp, lambda: not page.portrait_loads_active())
        assert card._portrait_pixmap.toImage().pixelColor(0, 0) == QColor("blue")
    finally:
        for release in releases.values():
            release.set()
        _close_page(qapp, page)


def test_removed_card_releases_portrait_token_and_can_reload_when_readded(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = CharactersPage()
    target = PortraitTarget("native:removal", native_root=tmp_path)
    key = ("fixture-account", 9001)
    loads: list[tuple[str, int]] = []

    def capture_load(card) -> None:
        loads.append((card.username, card.char_id))
        page._portrait_tokens[key] = object()

    monkeypatch.setattr(page, "_load_portrait_for_card", capture_load)
    try:
        page.refresh([_account()], [], ProcessTracker(), portrait_target=target)
        assert loads == [key]
        assert key in page._portrait_tokens

        page.refresh([], [], ProcessTracker(), portrait_target=target)

        assert key not in page._portrait_tokens

        page.refresh([_account()], [], ProcessTracker(), portrait_target=target)
        assert loads == [key, key]
    finally:
        _close_page(qapp, page)


def test_rejected_close_does_not_cancel_active_portrait_work(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Tracker:
        running_count = 1

        def kill_all(self) -> int:
            raise AssertionError("Rejected close must not kill clients")

    @dataclass
    class Page:
        cancelled: bool = False

        def cancel_portrait_loads(self, *, invalidate: bool = False) -> None:
            self.cancelled = True

        def portrait_loads_active(self) -> bool:
            return True

    class Event:
        ignored = False

        def ignore(self) -> None:
            self.ignored = True

    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    page = Page()
    window._update_install_worker = None
    window._docker_log_thread = None
    window._tracker = Tracker()
    window._characters_page = page
    window._close_in_progress = False
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.No,
    )
    event = Event()

    window.closeEvent(event)

    assert event.ignored is True
    assert page.cancelled is False
    assert window._close_in_progress is False
    window.deleteLater()
    qapp.processEvents()


def test_main_window_close_cancels_portrait_without_gui_thread_wait(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    PortraitCache.clear()
    started = threading.Event()
    release = threading.Event()
    created_loaders: list[PortraitLoader] = []
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": str(tmp_path / "runtime"),
            "client_path": "",
            "hide_test_characters": False,
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )

    class Provider:
        def __init__(self, _target, *, cache_dir: Path) -> None:
            pass

        def load(self, request: PortraitRequest) -> PortraitImageResult:
            started.set()
            assert release.wait(2.0)
            return PortraitImageResult(request, _image(QColor("green")), "native")

    class InjectedLoader(PortraitLoader):
        def __init__(self, target, request) -> None:
            super().__init__(
                target,
                request,
                cache_dir=tmp_path / "cache",
                provider_factory=Provider,
            )
            created_loaders.append(self)

    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: [_account()])
    monkeypatch.setattr(characters_page_module, "PortraitLoader", InjectedLoader)
    monkeypatch.setattr(app_module, "is_server_running", lambda **_kwargs: False)

    window = MainWindow()
    try:
        window._status_timer.stop()
        window._prune_timer.stop()
        _wait_until(qapp, started.is_set)

        before = time.monotonic()
        window.close()
        elapsed = time.monotonic() - before

        assert elapsed < 0.2
        assert window._close_in_progress is True
        assert window._characters_page.portrait_loads_active() is True
        assert created_loaders and created_loaders[0]._cancel.is_set()

        release.set()
        _wait_until(qapp, lambda: not window._background_data_active())
        assert window._accounts == [_account()]
    finally:
        release.set()
        window.close()
        _wait_until(qapp, lambda: not window._background_data_active())
        window.deleteLater()
        qapp.processEvents()
