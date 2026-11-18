"""Ordering commits use the currently attributed installation and real storage."""
from types import SimpleNamespace
import pytest

from src import app as app_module
from src.core import character_order
from src.core.groups import installation_group_identity


def _window():
    cfg={'active_installation_profile':'alpha','runtime_backend':'native'}
    selection=SimpleNamespace(target_identity='native:current')
    scope=installation_group_identity(cfg,selection)
    calls=[]
    page=SimpleNamespace(_order_scope=scope,set_character_order=lambda *args,**kwargs:calls.append((args,kwargs)))
    return SimpleNamespace(_cfg=cfg,_data_selection=selection,_group_storage_identity=scope,
        _character_order=((1,101),(2,102)),_character_order_writable=True,
        _characters_page=page,_data_load_error=''),calls


def test_reorder_persists_exact_ids_and_reload_recovers_positions(tmp_path,monkeypatch):
    path=tmp_path/'order.json'
    monkeypatch.setattr(character_order,'ORDER_FILE',path)
    window,calls=_window()
    app_module.MainWindow._on_character_order_changed(window,((2,102),(1,101)))
    assert character_order.load_character_order(window._group_storage_identity)==((2,102),(1,101))
    assert window._character_order==((2,102),(1,101))


def test_failed_save_restores_previous_layout_without_overwriting(tmp_path,monkeypatch):
    path=tmp_path/'order.json'
    monkeypatch.setattr(character_order,'ORDER_FILE',path)
    window,calls=_window()
    character_order.save_character_order(window._group_storage_identity,window._character_order)
    original=path.read_bytes()
    def fail(*_args,**_kwargs):raise OSError('disposable write failure')
    monkeypatch.setattr(app_module,'save_character_order',fail)
    warnings=[]
    monkeypatch.setattr(app_module.QMessageBox,'warning',lambda *args:warnings.append(args))
    app_module.MainWindow._on_character_order_changed(window,((2,102),(1,101)))
    assert window._character_order==((1,101),(2,102))
    assert calls[-1][0][0]==window._character_order
    assert path.read_bytes()==original and len(warnings)==1


def test_stale_drop_after_installation_switch_cannot_write_old_or_new_scope(tmp_path,monkeypatch):
    path=tmp_path/'order.json'
    monkeypatch.setattr(character_order,'ORDER_FILE',path)
    window,calls=_window()
    window._cfg={**window._cfg,'active_installation_profile':'beta'}
    app_module.MainWindow._on_character_order_changed(window,((2,102),(1,101)))
    assert not path.exists()


@pytest.mark.parametrize('backend',['native','docker_compose'])
def test_saved_positions_survive_runtime_upgrade_for_each_backend(tmp_path,monkeypatch,backend):
    monkeypatch.setattr(character_order,'ORDER_FILE',tmp_path/'order.json')
    cfg={'active_installation_profile':'alpha','runtime_backend':backend}
    selection=SimpleNamespace(target_identity='runtime:before',group_identity='persistent-store:a')
    scope=installation_group_identity(cfg,selection)
    character_order.save_character_order(scope,((2,102),(1,101)))
    window=SimpleNamespace(_cfg=cfg,_data_selection=selection)
    app_module.MainWindow._sync_character_groups(window,selection.target_identity)
    assert window._character_order==((2,102),(1,101))
    window._data_selection=SimpleNamespace(target_identity='runtime:after-upgrade',group_identity='persistent-store:a')
    app_module.MainWindow._sync_character_groups(window,window._data_selection.target_identity)
    assert window._character_order==((2,102),(1,101))
    assert window._group_storage_identity==scope
