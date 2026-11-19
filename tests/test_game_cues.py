"""Cache identity, optional decoding, concurrent voices and nonblocking loading."""
from array import array
import hashlib
import struct
import threading
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtTest import QTest

from src.audio import game_cues as cues
from src.audio.game_feedback import GameCueController
from src.audio.game_pcm import _PcmMixer


def _chunk(name, body):
    return name + struct.pack('<I', len(body)) + body


def test_didx_media_stays_inside_data_and_rejects_duplicate_claims(tmp_path):
    data = _chunk(b'DIDX', struct.pack('<III', 19, 2, 4)) + _chunk(b'DATA', b'abcdefgh')
    span = cues._bank_media(tmp_path/'cache', data, 19)
    assert data[span.offset:span.offset + span.size] == b'cdef'
    for index in (struct.pack('<III', 19, 7, 4), struct.pack('<III', 19, 2, 4) * 2, b'x'):
        with pytest.raises(cues.CueUnavailable):
            cues._bank_media(tmp_path/'cache', _chunk(b'DIDX', index) + _chunk(b'DATA', b'abcdefgh'), 19)
    with pytest.raises(cues.CueUnavailable):
        cues._chunks(b'DATA' + struct.pack('<I', 99) + b'truncated')


@pytest.mark.parametrize('backend', ['native', 'docker_compose'])
def test_linux_selection_reads_recorded_separate_cache(qapp, monkeypatch, tmp_path, backend):
    client, cache = tmp_path/'copied-client/tq', tmp_path/'separate-assets'
    client.mkdir(parents=True)
    target = cache/'ResFiles/aa/sound'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'verified audio fixture')
    digest = hashlib.md5(target.read_bytes()).hexdigest()
    (client/'resfileindex.txt').write_text(f'res:/audio/fixture,aa/sound,{digest},{target.stat().st_size},0\n')
    monkeypatch.setattr(cues, '_RESOURCES', {'fixture': (digest, target.stat().st_size)})
    monkeypatch.setattr(cues, 'resolve_client_tq_path', lambda _: client)
    from src.core.linux_installation import LinuxInstallation
    seen = []
    def load(root):
        seen.append(root)
        return SimpleNamespace(client=client, cache=cache)
    monkeypatch.setattr(LinuxInstallation, 'load', load)
    selection = cues.CueSelection(str(client), str(tmp_path/backend), True)
    found = cues._checked_resources(selection)
    assert found['fixture'][0] == target
    assert seen == [selection.setup_root]
    # A sibling cache cannot accidentally substitute for the selected cache.
    target.write_bytes(b'changed audio contents')
    with pytest.raises(cues.CueUnavailable):
        cues._checked_resources(selection)


def test_index_path_cannot_escape_selected_cache(monkeypatch, tmp_path):
    client = tmp_path/'client/tq'
    client.mkdir(parents=True)
    (client.parent/'ResFiles').mkdir()
    digest = hashlib.md5(b'outside').hexdigest()
    monkeypatch.setattr(cues, '_RESOURCES', {'fixture': (digest, 7)})
    monkeypatch.setattr(cues, 'resolve_client_tq_path', lambda _: client)
    for unsafe in ('../../outside', str(tmp_path/'outside')):
        (client/'resfileindex.txt').write_text(f'res:/audio/fixture,{unsafe},{digest},7,0\n')
        with pytest.raises(cues.CueUnavailable):
            cues._checked_resources(str(client))


def test_four_voices_finish_independently_with_mix_headroom():
    mixer = _PcmMixer()
    for count in (4, 8, 12, 16):
        mixer.play(array('h', [20000] * count).tobytes())
    remaining = []
    for _ in range(4):
        block = array('h')
        block.frombytes(mixer.render(8))
        assert max(block) <= mixer.headroom
        mixer.advance(8)
        remaining.append(len(mixer._voices))
    assert remaining == [3, 2, 1, 0]
    assert mixer.render(8) == bytes(8)


class _Audio(QObject):
    master_muted_changed = pyqtSignal(bool)
    master_muted = False


class _Player:
    def __init__(self, _owner, *, volume):
        self.volume = volume
        self.played, self.stops = [], 0
        self.closed = False
    def play(self, clip): self.played.append(clip)
    def stop(self): self.stops += 1
    def set_volume(self, value): self.volume = value
    def shutdown(self): self.closed = True


def _wait(predicate):
    for _ in range(200):
        if predicate(): return
        QTest.qWait(5)
    assert predicate(), 'Background audio result did not retire'


def test_decode_stays_off_gui_thread_and_old_client_results_do_not_leak(qapp):
    audio = _Audio()
    entered, release = threading.Event(), threading.Event()
    calls = []
    def loader(selection, _scratch):
        calls.append((selection.client, threading.get_ident()))
        entered.set()
        assert release.wait(3)
        return {'arrival': selection.client.encode(), 'departure': b'departure'}
    controller = GameCueController(audio, {'client_path': 'first'}, loader=loader, player_factory=_Player)
    ticks = []
    timer = QTimer(interval=5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        _wait(entered.is_set)
        QTest.qWait(30)
        assert len(ticks) >= 2
        assert calls[0][1] != threading.get_ident()
        controller.apply_settings({'client_path': 'second'})
        assert not controller.play('arrival')
        release.set()
        _wait(lambda: controller._clips.get('arrival') == b'second')
        assert [client for client, _thread in calls] == ['first', 'second']
        for cue in ('arrival', 'departure', 'arrival', 'departure'):
            assert controller.play(cue)
        assert len(controller._player.played) == 4
        audio.master_muted = True
        audio.master_muted_changed.emit(True)
        assert not controller.play('arrival') and controller._player.stops
        audio.master_muted = False
        controller.apply_settings({'audio_sound_effects_enabled': False})
        assert not controller.play('arrival')
    finally:
        release.set()
        timer.stop()
        controller.shutdown()
        assert controller._player is None or controller._player.closed


def test_missing_decoder_is_optional_and_never_queues_a_late_cue(qapp):
    audio = _Audio()
    def loader(*_args):
        raise cues.CueUnavailable('not available')
    controller = GameCueController(audio, {'client_path': 'fixture'}, loader=loader, player_factory=_Player)
    try:
        assert not controller.play('arrival')
        _wait(lambda: controller._future is None)
        assert controller.error == 'not available'
        assert not controller.play('arrival') and controller._player is None
    finally:
        controller.shutdown()


def test_short_audio_writes_preserve_pending_pcm_and_new_voice(qapp):
    from src.audio.game_pcm import _AudioStream, QAudio
    stream = _AudioStream(1.)
    stream.buffer_bytes = 8
    stream._sink = SimpleNamespace(state=lambda: QAudio.State.ActiveState,
                                   bytesFree=lambda: 8, bufferSize=lambda: 8)
    class Device:
        data = b''
        def write(self, data):
            count = min(3, len(data))
            self.data += data[:count]
            return count
    device = stream._device = Device()
    stream.play(array('h', [10, 20, 30, 40]).tobytes())
    stream._fill()
    stream.play(array('h', [50, 60, 70, 80]).tobytes())
    for _ in range(5): stream._fill()
    result = array('h')
    result.frombytes(device.data)
    assert list(result) == [10, 20, 30, 40, 50, 60, 70, 80]


def test_sound_effect_preferences_persist_as_visible_settings(qapp, monkeypatch, tmp_path):
    from src import config
    from src.pages.settings_page import SettingsPage
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config, 'CONFIG_FILE', tmp_path/'config.json')
    page = SettingsPage()
    try:
        page.sound_effects_toggle.setChecked(False)
        page.sound_effects_volume_slider.setValue(27)
        draft = page._collect_settings()
        config.save(draft)
        loaded = config.load()
        assert loaded['audio_sound_effects_enabled'] is False
        assert loaded['audio_sound_effects_volume'] == 27
        assert not page.sound_effects_volume_slider.isEnabled()
    finally:
        page.close()
        page.deleteLater()


def test_decoder_staging_is_hash_checked_and_platform_specific(tmp_path):
    import json
    from decoder_build_support import decoder_build_inputs
    assert decoder_build_inputs('linux', tmp_path) == ([], [])
    folder = tmp_path/'assets/decoders/linux'
    folder.mkdir(parents=True)
    binary = folder/'vgmstream-cli'
    binary.write_bytes(b'fixture binary')
    (folder/'decoder-manifest.json').write_text(json.dumps({'platform': 'linux', 'release': 'r2168',
        'files': {'vgmstream-cli': hashlib.sha256(binary.read_bytes()).hexdigest()}}))
    datas, binaries = decoder_build_inputs('linux', tmp_path)
    assert len(datas) == len(binaries) == 1
    binary.write_bytes(b'modified')
    with pytest.raises(ValueError, match='changed'):
        decoder_build_inputs('linux', tmp_path)
