"""Optional feedback must preserve gestures, retained rows and action results."""
from pathlib import Path

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QAbstractAnimation, QEvent, QPoint, Qt
from PyQt6.QtGui import QFocusEvent
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QWidget

from src.core import mod_management
from src.core.mod_inventory import load_mod_inventory
from src.core.service_status import DockerControlPolicy, RuntimeBackend
from src.pages.mods_page import ModsPage
from src.ui.page_reveal import PageReveal
from src.widgets.character_card import CharacterCard


def test_scan_never_steals_destination_input_and_retires_on_hide(qapp):
    owner = QWidget()
    owner.resize(600, 400)
    child = QWidget(owner)
    owner.show()
    qapp.processEvents()
    reveal = PageReveal(owner)
    try:
        reveal.start(child)
        first = reveal._layer
        assert first.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert child.graphicsEffect() is None
        reveal.start(child)
        assert first.isHidden()
        assert reveal._layer is not first
        owner.setProperty('animationsEnabled', False)
        assert reveal._layer is None
        owner.setProperty('animationsEnabled', True)
        reveal.start(child)
        owner.hide()
        assert reveal._layer is None
        assert reveal._animation.state() == QAbstractAnimation.State.Stopped
    finally:
        owner.close()
        owner.deleteLater()


def test_drag_requires_hold_and_distance_but_quick_click_selects(qapp, monkeypatch):
    card = CharacterCard('user', 'Pilot', 7)
    card._reorder_context = object()
    card.show()
    qapp.processEvents()
    selected, dragged = QSignalSpy(card.selected), []
    monkeypatch.setattr(card, '_exec_drag', lambda drag: dragged.append(drag))
    try:
        QTest.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
        assert len(selected) == 1
        QTest.mousePress(card, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
        QTest.mouseMove(card, QPoint(60, 20))
        assert not dragged
        QTest.mouseRelease(card, Qt.MouseButton.LeftButton, pos=QPoint(60, 20))
        assert len(selected) == 1
        QTest.mousePress(card, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
        QTest.qWait(card.HOLD_MS + 25)
        assert card._armed and not dragged
        QTest.mouseMove(card, QPoint(60, 20))
        QTest.mouseRelease(card, Qt.MouseButton.LeftButton, pos=QPoint(60, 20))
        assert len(dragged) == 1 and len(selected) == 1
        assert card._active_drag_context is None and not card._armed
    finally:
        card.close()
        card.deleteLater()


@pytest.mark.parametrize('cancel', ['hide', 'focus', 'context'])
def test_pending_drag_cannot_outlive_its_context(qapp, monkeypatch, cancel):
    card = CharacterCard('user', 'Pilot', 7)
    card._reorder_context = object()
    card.show()
    qapp.processEvents()
    dragged = []
    monkeypatch.setattr(card, '_exec_drag', lambda drag: dragged.append(drag))
    try:
        QTest.mousePress(card, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
        if cancel == 'hide':
            card.hide()
        elif cancel == 'focus':
            card.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        else:
            card._reorder_context = object()
        QTest.qWait(card.HOLD_MS + 25)
        QTest.mouseMove(card, QPoint(60, 20))
        assert not dragged
        assert not card._armed
    finally:
        card.close()
        card.deleteLater()


def test_reduced_motion_preserves_hold_drag_without_charge_or_landing(qapp, monkeypatch):
    card = CharacterCard('user', 'Pilot', 7)
    card._reorder_context = object()
    card.setProperty('animationsEnabled', False)
    card.show()
    qapp.processEvents()
    dragged = []
    monkeypatch.setattr(card, '_exec_drag', lambda drag: dragged.append(drag))
    try:
        QTest.mousePress(card, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
        QTest.qWait(card.HOLD_MS + 25)
        assert card._armed and card._charge == 0.0
        QTest.mouseMove(card, QPoint(60, 20))
        QTest.mouseRelease(card, Qt.MouseButton.LeftButton, pos=QPoint(60, 20))
        assert len(dragged) == 1 and card.graphicsEffect() is None
        card.land()
        assert card._settle == 1.0
        assert card._landing_animation.state() == QAbstractAnimation.State.Stopped
    finally:
        card.close()
        card.deleteLater()


def _loader(root, name, active=True):
    folder = root / 'mods' / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ('loader.js' if active else 'loader.js.disabled')).write_text('module.exports = {};\n')
    return folder


def _mods_page(qapp, monkeypatch, root, backend):
    def unmanaged(_mod):
        raise mod_management.ModNotManagedError('isolated fixture')
    monkeypatch.setattr(mod_management, 'read_managed_mod_registration', unmanaged)
    page = ModsPage(defer_inventory=True)
    page.set_evejs_root(str(root))
    page.set_runtime_context(backend, DockerControlPolicy.MANAGED)
    page.resize(1400, 1200)
    page.show()
    qapp.processEvents()
    page.show_inventory(load_mod_inventory(str(root)))
    qapp.processEvents()
    return page


@pytest.mark.parametrize('backend', [RuntimeBackend.NATIVE, RuntimeBackend.DOCKER_COMPOSE])
def test_verified_mod_burst_retains_unaffected_rows_and_each_dissolve_space(qapp, monkeypatch, tmp_path, backend):
    folders = [_loader(tmp_path, name) for name in ('A', 'B', 'C', 'D')]
    page = _mods_page(qapp, monkeypatch, tmp_path, backend)
    cues, finished = QSignalSpy(page._feedback.cue_requested), QSignalSpy(page._feedback.effect_finished)
    kept = page._rows[0]
    try:
        page.show_inventory(load_mod_inventory(str(tmp_path)))
        assert page._rows[0] is kept and not cues
        for folder in folders[1:]:
            (folder/'loader.js').unlink()
            folder.rmdir()
            page.show_inventory(load_mod_inventory(str(tmp_path)))
            qapp.processEvents()
        assert len(page._feedback.layer.effects) == 3
        assert page._rows[0] is kept
        assert [cue[0] for cue in cues] == ['departure'] * 3
        # Restore a removed folder while its old silhouette still owns a slot.
        _loader(tmp_path, 'B')
        page.show_inventory(load_mod_inventory(str(tmp_path)))
        qapp.processEvents()
        assert page._rows[0] is kept
        assert len(page._feedback.layer.effects) == 4
        assert cues[-1][0] == 'arrival'
        assert len(page._list_container.findChildren(QWidget, 'departingModSpace')) == 3
        page.show_inventory(load_mod_inventory(str(tmp_path)))
        assert len(cues) == 4
        QTest.qWait(1300)
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not page._feedback.layer.effects
        assert not page._feedback.layer._timer.isActive()
        assert len(finished) == 4
        assert not page._list_container.findChildren(QWidget, 'departingModSpace')
        assert not sip.isdeleted(kept)
    finally:
        page.close()
        page.deleteLater()


def test_hidden_refresh_and_reduced_motion_never_replay_stale_changes(qapp, monkeypatch, tmp_path):
    _loader(tmp_path, 'A')
    page = _mods_page(qapp, monkeypatch, tmp_path, RuntimeBackend.NATIVE)
    cues = QSignalSpy(page._feedback.cue_requested)
    try:
        page.hide()
        _loader(tmp_path, 'B')
        page.show_inventory(load_mod_inventory(str(tmp_path)))
        page.show()
        qapp.processEvents()
        page.show_inventory(load_mod_inventory(str(tmp_path)))
        assert not cues and not page._feedback.layer.effects
        page.setProperty('animationsEnabled', False)
        _loader(tmp_path, 'C')
        page.show_inventory(load_mod_inventory(str(tmp_path)))
        qapp.processEvents()
        assert len(cues) == 1 and not page._feedback.layer.effects
        path = tmp_path/'mods/A/loader.js'
        path.rename(path.with_name('loader.js.disabled'))
        page.show_inventory(load_mod_inventory(str(tmp_path)))
        assert len(cues) == 1  # Activation pulses are silent.
    finally:
        page.close()
        page.deleteLater()


def test_failed_operation_advances_baseline_without_success_feedback(qapp, monkeypatch, tmp_path):
    _loader(tmp_path, 'A')
    page = _mods_page(qapp, monkeypatch, tmp_path, RuntimeBackend.NATIVE)
    cues = QSignalSpy(page._feedback.cue_requested)
    try:
        page.suppress_next_inventory_feedback()
        _loader(tmp_path, 'B')
        page.show_inventory(load_mod_inventory(str(tmp_path)))
        qapp.processEvents()
        assert not cues and not page._feedback.layer.effects
        page.show_inventory(load_mod_inventory(str(tmp_path)))
        assert not cues
        _loader(tmp_path, 'C')
        page.show_inventory(load_mod_inventory(str(tmp_path)))
        qapp.processEvents()
        assert len(cues) == 1 and cues[0][0] == 'arrival'
    finally:
        page.close()
        page.deleteLater()
