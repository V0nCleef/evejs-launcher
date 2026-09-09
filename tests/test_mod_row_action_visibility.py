from pathlib import Path
import pytest
from src.core.mod_manifest import Mod, ActivationKind
from src.core.mod_activation_state import project_mod_activation
from src.pages.mods_page import ModRow

@pytest.mark.parametrize('up,down', [(False,False),(False,True),(True,False),(True,True)])
def test_only_available_reorder_directions_are_visible(qapp,tmp_path,up,down):
    folder=tmp_path/'mods/AutoMiningDrones'
    folder.mkdir(parents=True)
    (folder/'loader.js').write_text('')
    mod=Mod('AutoMiningDrones',folder,True,evejs_root=tmp_path)
    projection=project_mod_activation(mod,None)
    row=ModRow(mod,projection=projection,projection_resolver=lambda _:projection,
               delegated_activation=True,can_move_up=up,can_move_down=down)
    try:
        row.show()
        qapp.processEvents()
        assert row.move_up_btn.isVisible() is up
        assert row.move_down_btn.isVisible() is down
        assert not row.move_up_btn.icon().isNull()
        assert not row.move_down_btn.icon().isNull()
        assert not row.configure_btn.isVisible()
        assert not row.helper_btn.isVisible()
        assert not row.update_btn.isVisible()
    finally:
        row.close()
        row.deleteLater()
