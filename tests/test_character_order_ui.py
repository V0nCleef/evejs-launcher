"""Real Qt card gestures, drop validation and retained roster presentation."""
from PyQt6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt
from PyQt6.QtGui import QContextMenuEvent, QDragEnterEvent, QDropEvent
from PyQt6.QtTest import QSignalSpy, QTest
import pytest

from src.core.db import Account, Character
from src.core.groups import CharacterGroup, GroupMember, TargetGroupState
from src.core.process_tracker import ProcessTracker
from src.pages.characters_page import CharactersPage
from src.widgets import character_card as card_module


def _accounts():
    return [Account(username=f'account-{i}', account_id=i, role='0', banned=False, characters=[
        Character(char_id=100+i, name=f'Pilot {i}')]) for i in (1, 10, 2, 3)]


def _ids(page):
    return [character.char_id for _username, character in page._rotation_pool]


def _page(qapp):
    page=CharactersPage()
    page.resize(1080,700)
    page.set_character_order((), 'installation-a')
    page.refresh(_accounts(), [], ProcessTracker())
    page.show()
    qapp.processEvents()
    return page


def _close(qapp,page):
    page.cancel_portrait_loads(invalidate=True)
    page.close()
    page.deleteLater()
    qapp.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_saved_order_survives_refresh_resize_search_and_account_rename(qapp):
    page=_page(qapp)
    try:
        cards=dict(page._cards)
        page.set_character_order(((1,101),(2,102),(3,103),(10,110)), 'installation-a')
        assert _ids(page)==[101,102,103,110]
        accounts=_accounts()
        accounts[0].username='renamed-account'
        page.refresh(accounts, [], ProcessTracker())
        page.resize(660,680)
        page.search_edit.setText('Pilot 2')
        page.search_edit.clear()
        qapp.processEvents()
        assert _ids(page)==[101,102,103,110]
        assert page._cards[('account-2',102)] is cards[('account-2',102)]
        assert page._grid.itemAtPosition(0,0).widget() is page._new_character_card
    finally:
        _close(qapp,page)


def test_reordering_filtered_group_keeps_other_cards_and_group_launch_order(qapp):
    page=_page(qapp)
    try:
        group=CharacterGroup('group','Pair',members=(GroupMember(1,101),GroupMember(3,103)))
        page.set_group_state(TargetGroupState((group,),'group'))
        changed=QSignalSpy(page.character_order_changed)
        page._move_card(page._cards[('account-3',103)],page._cards[('account-1',101)],False)
        assert _ids(page)==[103,110,102,101]
        assert len(changed)==1
        assert page._group_state.selected_group.members==group.members
    finally:
        _close(qapp,page)


def test_body_and_overflow_open_the_same_menu_and_capture_character_identity(qapp, monkeypatch):
    page = _page(qapp)
    try:
        card = page._cards[("account-1", 101)]
        seen = []
        monkeypatch.setattr(card_module.QMenu, "popup", lambda menu, point: seen.append((menu, point)))
        body = card.rect().center()
        absolute = card.mapToGlobal(body)
        card.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, body, absolute))
        button = card._overflow_btn
        cursor = button.mapToGlobal(button.rect().center())
        monkeypatch.setattr(card_module.QCursor, "pos", lambda: cursor)
        card._on_overflow_clicked()
        assert [point for _menu, point in seen] == [absolute, cursor]
        assert [action.text() for action in seen[0][0].actions()] == [
            action.text() for action in seen[1][0].actions()
        ]
        requested = QSignalSpy(page.rename_character_requested)
        rename = next(action for action in seen[0][0].actions() if action.text() == "Rename Character…")
        card.set_character_name("A Different Name")
        rename.trigger()
        assert list(requested[0]) == ["account-1", "Pilot 1", 101]
    finally:
        _close(qapp, page)


def test_rename_refreshes_existing_card_and_search_under_the_same_id(qapp):
    page = _page(qapp)
    try:
        original = page._cards[("account-1", 101)]
        original.set_selected(True)
        page._selected_key = ("account-1", 101)
        accounts = _accounts()
        accounts[0].characters[0].name = "New Pilot Name"
        page.refresh(accounts, [], ProcessTracker())
        assert page._cards[("account-1", 101)] is original
        assert page._selected_key == ("account-1", 101)
        assert original.accessibleName() == "New Pilot Name"
        assert original._name_label.toolTip() == "New Pilot Name"
        page.search_edit.setText("New Pilot Name")
        assert not original.isHidden()
        page.search_edit.setText("Pilot 1")
        assert original.isHidden()
    finally:
        _close(qapp, page)


class _Enter(QDragEnterEvent):
    def __init__(self,source,mime):
        super().__init__(QPoint(8,8),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        self.origin=source
    def source(self):return self.origin


class _Drop(QDropEvent):
    def __init__(self,source,mime,position=None):
        super().__init__(position or QPointF(8,8),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        self.origin=source
    def source(self):return self.origin


@pytest.mark.parametrize('origin',['card','portrait','name','account'])
def test_drag_gesture_reaches_qt_drop_without_launching_or_selecting(qapp,monkeypatch,origin):
    page=_page(qapp)
    try:
        source=page._cards[('account-3',103)]
        target=page._cards[('account-1',101)]
        selected, launched=QSignalSpy(page.character_selected),QSignalSpy(page.launch_character)
        class Drag:
            def __init__(self,widget):assert widget is source
            def setMimeData(self,mime):self.mime=mime
            def setPixmap(self,_pixmap):pass
            def setHotSpot(self,_point):pass
            def exec(self,action):
                assert action==Qt.DropAction.MoveAction
                enter=_Enter(source,self.mime)
                QCoreApplication.sendEvent(target,enter)
                assert enter.isAccepted()
                drop=_Drop(source,self.mime)
                QCoreApplication.sendEvent(target,drop)
                assert drop.isAccepted()
                return action
        from src.widgets import character_card_motion
        monkeypatch.setattr(character_card_motion,'QDrag',Drag)
        widget={'card':source,'portrait':source._portrait,'name':source._name_label,'account':source._account_label}[origin]
        QTest.mousePress(widget,Qt.MouseButton.LeftButton,pos=QPoint(8,8))
        QTest.qWait(source.HOLD_MS + 25)
        QTest.mouseMove(widget,QPoint(50,15))
        QTest.mouseRelease(widget,Qt.MouseButton.LeftButton,pos=QPoint(50,15))
        assert _ids(page)==[103,101,110,102]
        assert len(selected)==len(launched)==0
    finally:
        _close(qapp,page)


def test_simple_click_still_selects_and_launch_button_still_launches(qapp):
    page=_page(qapp)
    try:
        card=page._cards[('account-1',101)]
        selected,launched=QSignalSpy(page.character_selected),QSignalSpy(page.launch_character)
        QTest.mouseClick(card,Qt.MouseButton.LeftButton,pos=QPoint(15,15))
        assert len(selected)==1
        QTest.mouseClick(card._launch_btn,Qt.MouseButton.LeftButton)
        assert len(launched)==1
    finally:
        _close(qapp,page)


def test_external_cross_page_and_previous_installation_drops_are_rejected(qapp):
    page,other=_page(qapp),_page(qapp)
    try:
        target=page._cards[('account-1',101)]
        source=other._cards[('account-3',103)]
        mime=QMimeData();mime.setData(card_module.CHARACTER_CARD_MIME,b'1')
        source._active_drag_context=source._reorder_context
        assert not target.accepts_character_drag(_Enter(source,mime))
        source=page._cards[('account-3',103)]
        source._active_drag_context=source._reorder_context
        page.set_character_order((), 'installation-b')
        assert not target.accepts_character_drag(_Enter(source,mime))
        assert not target.accepts_character_drag(_Enter(None,mime))
        assert _ids(page)==[101,110,102,103]
    finally:
        _close(qapp,page);_close(qapp,other)


def test_drop_on_empty_grid_space_moves_card_to_the_end(qapp):
    page=_page(qapp)
    try:
        source=page._cards[('account-1',101)]
        last=page._cards[('account-3',103)]
        source._active_drag_context=page._order_context
        mime=QMimeData();mime.setData(card_module.CHARACTER_CARD_MIME,b'1')
        enter=_Enter(source,mime)
        QCoreApplication.sendEvent(page._grid_container,enter)
        assert enter.isAccepted()
        position=QPointF(last.geometry().right()+4,last.geometry().bottom()+25)
        drop=_Drop(source,mime,position)
        QCoreApplication.sendEvent(page._grid_container,drop)
        assert drop.isAccepted()
        assert _ids(page)==[110,102,103,101]
    finally:
        _close(qapp,page)


def test_cancelling_a_drag_stops_scroll_and_clears_indicators(qapp):
    page=_page(qapp)
    try:
        page._drag_scroll_delta=16
        page._drag_scroll_timer.start()
        first=page._cards[('account-1',101)]
        first._drop_after=True
        first.drag_finished.emit()
        assert not page._drag_scroll_timer.isActive()
        assert first._drop_after is None
    finally:
        _close(qapp,page)


def test_drag_auto_scrolls_long_roster_and_stops_when_source_disappears(qapp):
    page=_page(qapp)
    try:
        accounts=[Account(f'account-{i}',i,'0',False,[Character(100+i,f'Pilot {i}')]) for i in range(1,31)]
        page.refresh(accounts,[],ProcessTracker())
        page.resize(660,550)
        qapp.processEvents()
        bar=page._scroll.verticalScrollBar()
        assert bar.maximum()>0
        source=page._cards[('account-1',101)]
        source._active_drag_context=page._order_context
        page._drag_source=source;page._drag_scroll_delta=16
        page._scroll_while_dragging()
        assert bar.value()==16
        page._cards.pop(('account-1',101))
        page._drag_scroll_timer.start()
        page._scroll_while_dragging()
        assert not page._drag_scroll_timer.isActive()
    finally:
        _close(qapp,page)


def test_ordering_phrases_cover_every_language():
    from src.i18n import LANGUAGES, UI_PHRASES_BY_LANGUAGE
    from src.translations_character_order import SOURCE_PHRASES
    for language in LANGUAGES:
        if language.code=='en':continue
        assert all(UI_PHRASES_BY_LANGUAGE[language.code][key].strip() for key in SOURCE_PHRASES)
