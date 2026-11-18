"""EveJS stores uploaded bytes unchanged under every size-suffixed filename."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import QColor, QImage

from src.core.runtime.docker_compose import Endpoint
from src.core.runtime.portraits import PortraitProvider, PortraitRequest, PortraitTarget


def _portrait(size):
    image=QImage(size,size,QImage.Format.Format_RGB32)
    image.fill(QColor('blue'))
    data=QByteArray()
    buffer=QBuffer(data)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer,'JPEG')
    return bytes(data)


@contextmanager
def _image_server(payload):
    paths=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type','image/jpeg')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self,*_args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield Endpoint(service='server',host='127.0.0.1',port=server.server_port,target=26001,protocol='tcp'),paths
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize('uploaded_size',[64,256,512,1024])
def test_http_size_aliases_scale_valid_uploaded_portrait_and_cache_offline(tmp_path,uploaded_size):
    with _image_server(_portrait(uploaded_size)) as (endpoint,paths):
        target=PortraitTarget('docker:portrait-alias',image_endpoint=endpoint)
        request=PortraitRequest(target.target_identity,9001,128,1,object())
        result=PortraitProvider(target,cache_dir=tmp_path/'cache').load(request)
        assert paths==['/Character/9001_128.jpg']
    assert result.source=='http'
    assert (result.image.width(),result.image.height())==(128,128)
    assert result.image.pixelColor(64,64).blue()>200
    def offline(*_args,**_kwargs):raise AssertionError('A valid disk hit must not use the network')
    cached=PortraitProvider(target,cache_dir=tmp_path/'cache',http_open=offline).load(request)
    assert cached.source=='disk' and cached.image==result.image


@pytest.mark.parametrize('location,size',[
    ('_local/gameStore/images/Character',1024),
    ('server/src/gameStore/images/Character',128),
    ('server/src/_secondary/image/generated/Character',32),
])
def test_native_portrait_covers_server_store_layout_and_all_written_sizes(tmp_path,location,size):
    root=tmp_path/'runtime'
    image=root/location/f'9001_{size}.jpg'
    image.parent.mkdir(parents=True)
    image.write_bytes(_portrait(size))
    target=PortraitTarget('native:portrait-layout',native_root=root)
    request=PortraitRequest(target.target_identity,9001,128,1,object())
    result=PortraitProvider(target,cache_dir=tmp_path/'cache').load(request)
    assert result.source=='native'
    assert (result.image.width(),result.image.height())==(128,128)


def test_real_http_worker_displays_portrait_on_card_and_selected_detail(qapp,tmp_path,monkeypatch):
    import time
    from PyQt6.QtCore import QEvent
    from PyQt6.QtTest import QTest
    from src import config
    from src.core.db import Account,Character
    from src.core.process_tracker import ProcessTracker
    from src.pages.characters_page import CharactersPage
    from src.utils.cache import PortraitCache
    monkeypatch.setattr(config,'CONFIG_DIR',tmp_path/'launcher-state')
    PortraitCache.clear()
    page=CharactersPage()
    try:
        with _image_server(_portrait(512)) as (endpoint,_paths):
            target=PortraitTarget('docker:portrait-real-worker',image_endpoint=endpoint)
            accounts=[Account('fixture-account',1,'0',False,[Character(9001,'Fixture Pilot')])]
            page.refresh(accounts,[],ProcessTracker(),portrait_target=target)
            page._on_card_selected('fixture-account','Fixture Pilot',9001)
            card=page._cards[('fixture-account',9001)]
            deadline=time.monotonic()+5
            while card._portrait_pixmap is None or page.portrait_loads_active():
                assert time.monotonic()<deadline,'Real portrait worker did not finish'
                qapp.processEvents();QTest.qWait(5)
            assert not card._portrait_pixmap.isNull()
            assert card._portrait_pixmap.toImage().pixelColor(64,64).blue()>200
            assert page.detail_panel._portrait_pixmap.cacheKey()==card._portrait_pixmap.cacheKey()
            assert page.detail_panel._portrait._pixmap.cacheKey()==card._portrait_pixmap.cacheKey()
    finally:
        page.cancel_portrait_loads(invalidate=True)
        page.close();page.deleteLater()
        qapp.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        PortraitCache.clear()
