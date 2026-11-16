"""Exercise the real asynchronous dialog without taking desktop input."""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest
from PyQt6.QtCore import QEvent, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog

from src import config
from src.app import MainWindow
from src.core.application_operations import active_mutations, update_blocked
from src.core.runtime.data import NativeDataSource, SqliteGameStoreDataSource
from src.core.shared_client_settings import SharedSettingsError
from src.i18n import current_language, set_language, translate_ui_phrase
from src.pages.settings_page import SettingsPage
from src.translations_drone_recovery import SOURCE_PHRASES, UI_PHRASES_BY_LANGUAGE
from src.ui import drone_recovery as ui
from test_drone_recovery_batch import recovery_world


def settle(qapp, dialog):
    deadline=time.monotonic()+10
    qapp.processEvents()
    while dialog.busy:
        assert time.monotonic()<deadline,'Recovery worker did not finish'
        qapp.processEvents()
        QTest.qWait(3)
    qapp.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def test_async_review_and_all_character_apply(qapp,recovery_world):
    context,old,_=recovery_world
    dialog=ui.DroneRecoveryDialog(context)
    try:
        settle(qapp,dialog)
        assert dialog.model.rowCount()==2
        assert dialog.model.data(dialog.model.index(0,0))=='Character 101'
        assert dialog.model.data(dialog.model.index(0,2))=='6'
        assert dialog.sources.currentData()==str(old).lower()
        assert dialog.restore.isEnabled()
        dialog.restore.click()
        assert dialog.busy and not dialog.cancel.isEnabled()
        dialog.reject()
        assert dialog.busy  # No destructive QThread teardown on Escape/close.
        settle(qapp,dialog)
        assert not dialog.restore.isEnabled()
        assert dialog.status.text().startswith('Group recovery complete.')
        dialog.rescan.click()
        settle(qapp,dialog)
        assert dialog.model.rowCount()==0
        assert dialog.status.text().startswith('No verified missing memberships')
    finally:
        settle(qapp,dialog)
        dialog.deleteLater()


def test_changed_selection_blocks_apply_without_writing(qapp,recovery_world):
    context,_,_=recovery_world
    valid=[True]
    dialog=ui.DroneRecoveryDialog(context,selection_valid=lambda:valid[0])
    try:
        settle(qapp,dialog)
        valid[0]=False
        dialog.restore.click()
        assert not dialog.busy and not dialog.restore.isEnabled()
        assert 'different installation' in dialog.status.text()
        assert not (context.storage_root/'native-drones').exists()
    finally:
        dialog.deleteLater()


def test_running_native_client_reports_close_guidance(qapp,recovery_world,monkeypatch):
    context,_,_=recovery_world
    monkeypatch.setattr('src.core.overview_patch.is_eve_client_running',lambda:True)
    dialog=ui.DroneRecoveryDialog(context)
    try:
        settle(qapp,dialog)
        assert dialog.status.text()==ui.CLOSE_CLIENTS
        assert not dialog.restore.isEnabled()
    finally:
        dialog.deleteLater()


def test_readonly_scan_keeps_gui_timer_responsive(qapp,recovery_world,monkeypatch):
    context,_,_=recovery_world
    original=ui.scan_recovery
    def slow_scan(context):
        QTest.qWait(100)
        return original(context)
    monkeypatch.setattr(ui,'scan_recovery',slow_scan)
    dialog=ui.DroneRecoveryDialog(context)
    ticks=[]
    timer=QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda:ticks.append(1))
    timer.start()
    try:
        settle(qapp,dialog)
        assert len(ticks)>=5
    finally:
        timer.stop()
        dialog.deleteLater()


def test_provider_context_uses_verified_bind_and_never_stale_host_database(tmp_path):
    cfg=dict(runtime_backend='native',client_settings_mode='shared',
             active_installation_profile='default',evejs_root=str(tmp_path/'native'))
    native=SimpleNamespace(data_source=NativeDataSource(cfg['evejs_root']),target_identity='native',group_identity=None)
    context=ui.recovery_context(cfg,native,tmp_path/'launcher/Profiles')
    assert context.database==tmp_path/'native/_local/gameStore/gamestore.sqlite'
    assert context.target_identity is None
    cfg['runtime_backend']='docker_compose'
    bind=SimpleNamespace(data_source=SqliteGameStoreDataSource(tmp_path/'verified-bind'),
                         target_identity='exact-compose-target',group_identity='exact-persistent-store')
    context=ui.recovery_context(cfg,bind,tmp_path/'launcher/Profiles')
    assert context.database==tmp_path/'verified-bind/gamestore.sqlite'
    assert context.target_identity=='exact-compose-target'
    volume=SimpleNamespace(data_source=object(),target_identity='exact-compose-target',group_identity='volume')
    with pytest.raises(SharedSettingsError,match='verified local game database'):
        ui.recovery_context(cfg,volume,tmp_path/'launcher/Profiles')
    with pytest.raises(SharedSettingsError):
        ui.recovery_context(cfg,native,tmp_path/'launcher/Profiles')


def test_ownership_blocks_update_runtime_change_and_real_close(recovery_world):
    context,_,_=recovery_world
    owner=SimpleNamespace(_drone_recovery_request=context)
    assert active_mutations(owner)==('drone_recovery',)
    assert update_blocked(owner)
    assert MainWindow._lifecycle_active(owner)
    assert MainWindow._settings_save_rejection(owner,{}) is not None
    from PyQt6.QtGui import QCloseEvent
    event=QCloseEvent()
    MainWindow._close_launcher(owner,event)
    assert not event.isAccepted()


def test_settings_action_is_shared_only_and_translated(qapp,tmp_path,monkeypatch):
    monkeypatch.setattr(config,'CONFIG_DIR',tmp_path)
    monkeypatch.setattr(config,'CONFIG_FILE',tmp_path/'config.json')
    language=current_language()
    set_language('nl')
    page=SettingsPage()
    try:
        assert page.drone_recovery_btn.isHidden()
        page.client_settings_mode_combo.setCurrentIndex(1)
        assert not page.drone_recovery_btn.isHidden()
        assert page.drone_recovery_btn.text()=='Dronegroepen herstellen'
        page.set_lifecycle_busy(True)
        assert not page.drone_recovery_btn.isEnabled()
        page.set_lifecycle_busy(False)
        assert page.drone_recovery_btn.isEnabled()
    finally:
        page.deleteLater()
        set_language(language)


def test_complete_translation_catalog_preserves_placeholders():
    import string
    for language,catalog in UI_PHRASES_BY_LANGUAGE.items():
        assert set(catalog)==set(SOURCE_PHRASES)
        for source,text in catalog.items():
            assert text.strip() and text!=source
            fields=lambda value:{name for _,name,_,_ in string.Formatter().parse(value) if name is not None}
            assert fields(text)==fields(source)
            assert translate_ui_phrase(source,language)==text
