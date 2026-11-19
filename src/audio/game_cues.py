"""Resolve reviewed cues from the selected client cache without copying game media.

Unknown cache revisions stay silent. Only text decoder pointers touch temporary
storage; the game's media and decoded PCM are never packaged or written there.
All calls here belong to a background worker, never a GUI event handler.
"""
from __future__ import annotations

from array import array
import csv
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import wave

from src.core.discovery import resolve_client_tq_path
from src.core.platform import get_hidden_process_flags

SAMPLE_RATE = 48000
WARP_APPROACH_MS = 580
WARP_LANDING_OFFSET_MS = 480

# The event-to-media bindings below were reviewed against build 3396210's
# Common HIRC Play actions and ShipEffects/Effects Sound sources. These content
# hashes are a compatibility boundary, not a build-number guess.
_RESOURCES = {
    'soundprioritizationmetadata.json': ('085d8073d933ef4cb1ec09cffa1e11a5', 4461055),
    'essential_media/common.bnk': ('e1f97b1972700499d79d83a27909e5dd', 557893),
    'essential_media/shipeffects.bnk': ('58330b7edd13dab4d43ac0bb63572404', 7960568),
    'essential_media/effects.bnk': ('c743e0623791d8dfabe4e485c76a12ca', 36909619),
    'media/360126372.wem': ('984248498fd4c1e64967ba0fdcf74dfd', 616893),
}


class CueUnavailable(ValueError):
    """Optional game feedback cannot be verified for this client."""


@dataclass(frozen=True)
class MediaSpan:
    path: Path
    offset: int
    size: int


@dataclass(frozen=True)
class CueSelection:
    client: str
    setup_root: str = ''
    linux: bool = False

    @classmethod
    def from_mapping(cls, settings):
        linux = sys.platform == 'linux'
        root = settings.get('evejs_root', '')
        if linux and settings.get('runtime_backend') == 'docker_compose':
            root = settings.get('linux_setup_root') or root
        return cls(str(settings.get('client_path') or ''), str(root or '') if linux else '', linux)


def find_decoder() -> Path | None:
    name = 'vgmstream-cli.exe' if sys.platform == 'win32' else 'vgmstream-cli'
    bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    candidate = bundle / 'assets' / 'decoders' / ('windows' if sys.platform == 'win32' else 'linux') / name
    if candidate.is_file():
        return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def _checked_resources(client: CueSelection | str) -> dict[str, tuple[Path, bytes]]:
    selection = client if isinstance(client, CueSelection) else CueSelection(client)
    tq = resolve_client_tq_path(selection.client)
    if tq is None:
        raise CueUnavailable('A copied EVE client is required for game sound effects.')
    cache_root = tq.parent
    if selection.linux:
        from src.core.linux_installation import LinuxInstallation
        installation = LinuxInstallation.load(selection.setup_root)
        if tq.resolve() != installation.client:
            raise CueUnavailable('The selected client differs from the prepared Linux installation.')
        cache_root = installation.cache
    cache = (cache_root / 'ResFiles').resolve(strict=True)
    index = tq / 'resfileindex.txt'
    if not index.is_file() or index.stat().st_size > 128 * 1024 * 1024:
        raise CueUnavailable('The selected client resource index is unavailable.')
    wanted = {'res:/audio/' + name: name for name in _RESOURCES}
    found = {}
    with index.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.reader(stream):
            if not row or row[0] not in wanted:
                continue
            name = wanted[row[0]]
            digest, size = _RESOURCES[name]
            if name in found or len(row) < 4 or row[2].lower() != digest or row[3] != str(size):
                raise CueUnavailable('The installed audio revision is not reviewed.')
            relative = Path(row[1].replace('\\', '/'))
            if relative.is_absolute() or '..' in relative.parts:
                raise CueUnavailable('The client resource index contains an unsafe path.')
            path = (cache / relative).resolve(strict=True)
            if not path.is_relative_to(cache) or not path.is_file() or path.stat().st_size != size:
                raise CueUnavailable('The selected audio cache is incomplete.')
            data = path.read_bytes()
            if len(data) != size or hashlib.md5(data, usedforsecurity=False).hexdigest() != digest:
                raise CueUnavailable('The cached audio does not match its resource index.')
            found[name] = (path, data)
    if found.keys() != _RESOURCES.keys():
        raise CueUnavailable('The selected client does not contain the reviewed sounds.')
    return found


def _chunks(data: bytes) -> dict[bytes, tuple[int, int]]:
    chunks = {}
    cursor = 0
    while cursor < len(data):
        if cursor + 8 > len(data):
            raise CueUnavailable('Truncated sound bank header.')
        name, size = struct.unpack_from('<4sI', data, cursor)
        cursor += 8
        if cursor + size > len(data) or name in chunks:
            raise CueUnavailable('Invalid sound bank chunk.')
        chunks[name] = (cursor, size)
        cursor += size
    return chunks


def _objects(data: bytes) -> dict[int, tuple[int, bytes]]:
    start, size = _chunks(data).get(b'HIRC', (0, 0))
    if size < 4:
        raise CueUnavailable('Sound bank object table is unavailable.')
    count = struct.unpack_from('<I', data, start)[0]
    cursor, end, result = start + 4, start + size, {}
    for _ in range(count):
        if cursor + 5 > end:
            raise CueUnavailable('Truncated sound object table.')
        kind, length = struct.unpack_from('<BI', data, cursor)
        cursor += 5
        if length < 4 or cursor + length > end:
            raise CueUnavailable('Invalid sound object span.')
        payload = data[cursor:cursor + length]
        identity = struct.unpack_from('<I', payload)[0]
        if identity in result:
            raise CueUnavailable('Ambiguous sound object identity.')
        result[identity] = kind, payload
        cursor += length
    if cursor != end:
        raise CueUnavailable('Invalid sound object count.')
    return result


def _event_id(name: str) -> int:
    value = 2166136261
    for byte in name.lower().encode('ascii'):
        value = ((value * 16777619) ^ byte) & 0xffffffff
    return value


def _play_target(objects, metadata, event: str) -> int:
    identity = _event_id(event)
    if metadata.get('Events', {}).get(event, {}).get('eventID') != identity:
        raise CueUnavailable('The event metadata does not match the installed bank.')
    kind, payload = objects.get(identity, (None, b''))
    if kind != 4 or len(payload) < 9 or payload[4] < 1 or len(payload) != 5 + 4 * payload[4]:
        raise CueUnavailable('The cue event cannot be verified.')
    action = struct.unpack_from('<I', payload, 5)[0]
    kind, payload = objects.get(action, (None, b''))
    if kind != 3 or len(payload) < 10 or payload[5] != 4:
        raise CueUnavailable('The first cue action is not Play.')
    return struct.unpack_from('<I', payload, 6)[0]


def _bank_media(path: Path, data: bytes, media_id: int) -> MediaSpan:
    chunks = _chunks(data)
    start, size = chunks.get(b'DIDX', (0, 0))
    body, body_size = chunks.get(b'DATA', (0, 0))
    if size % 12 or not size or not body_size:
        raise CueUnavailable('The bank media index is unavailable.')
    matches = []
    for cursor in range(start, start + size, 12):
        identity, offset, length = struct.unpack_from('<III', data, cursor)
        if identity == media_id:
            if not length or offset + length > body_size:
                raise CueUnavailable('The indexed cue leaves the bank DATA span.')
            matches.append(MediaSpan(path, body + offset, length))
    if len(matches) != 1:
        raise CueUnavailable('The requested bank media is missing or ambiguous.')
    return matches[0]


def resolve_cues(client: CueSelection | str) -> dict[str, MediaSpan]:
    resources = _checked_resources(client)
    metadata = json.loads(resources['soundprioritizationmetadata.json'][1])
    objects = {}
    for name in ('essential_media/common.bnk', 'essential_media/shipeffects.bnk', 'essential_media/effects.bnk'):
        current = _objects(resources[name][1])
        if any(objects[identity] != current[identity] for identity in objects.keys() & current.keys()):
            raise CueUnavailable('Sound banks contain ambiguous object identities.')
        objects.update(current)
    result = {}
    for cue, event, media, bank in (
        ('landing', 'warp_in_frig_play', 1040812498, 'essential_media/shipeffects.bnk'),
        ('departure', 'dissolve_asteroid_play', 981147460, 'essential_media/effects.bnk'),
    ):
        target = _play_target(objects, metadata, event)
        kind, payload = objects.get(target, (None, b''))
        if kind != 2 or len(payload) < 13 or struct.unpack_from('<I', payload, 9)[0] != media:
            raise CueUnavailable('The event Play source does not match the reviewed media.')
        result[cue] = _bank_media(resources[bank][0], resources[bank][1], media)
    target = _play_target(objects, metadata, 'ship_warp_wind_play')
    kind, container = objects.get(target, (None, b''))
    sounds = [identity for identity, (kind, payload) in objects.items()
              if kind == 2 and len(payload) >= 13 and struct.unpack_from('<I', payload, 9)[0] == 360126372]
    if kind != 5 or not any(struct.pack('<I', identity) in container[4:] for identity in sounds):
        raise CueUnavailable('The warp-wind Play container cannot be verified.')
    wind, data = resources['media/360126372.wem']
    # This is the complete resource; its embedded bank entry is only a prefetch.
    result['wind'] = MediaSpan(wind, 0, len(data))
    return result


def _decode(decoder: Path, media: MediaSpan, pointer: Path) -> array:
    body = str(media.path)
    if any(character in body for character in '\n\r'):
        raise CueUnavailable('The client cache path cannot be represented safely.')
    pointer.write_text(f'body_file = {body}\nsubfile_extension = wem\nsubfile_offset = {media.offset}\nsubfile_size = {media.size}\n', encoding='utf-8')
    result = subprocess.run([str(decoder), '-i', '-p', str(pointer)],
                            stdin=subprocess.DEVNULL, capture_output=True,
                            check=True, timeout=5, **get_hidden_process_flags())
    with wave.open(BytesIO(result.stdout), 'rb') as clip:
        channels, rate, count = clip.getnchannels(), clip.getframerate(), clip.getnframes()
        if channels not in (1, 2) or rate != SAMPLE_RATE or clip.getsampwidth() != 2 or not 0 < count <= rate * 60:
            raise CueUnavailable('The decoded cue format is unsupported.')
        data = clip.readframes(count)
        if len(data) != count * channels * 2:
            raise CueUnavailable('The decoded cue is incomplete.')
    pcm = array('h')
    pcm.frombytes(data)
    if sys.byteorder != 'little':
        pcm.byteswap()
    return array('h', ((left + right) // 2 for left, right in zip(pcm[::2], pcm[1::2]))) if channels == 2 else pcm


def prepare_cues(client: CueSelection | str, scratch: Path) -> dict[str, bytes]:
    decoder = find_decoder()
    if decoder is None:
        raise CueUnavailable('The optional game audio decoder is unavailable.')
    media = resolve_cues(client)
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='cue-pointers-', dir=scratch) as folder:
        root = Path(folder)
        landing = _decode(decoder, media['landing'], root / 'landing.txth')
        wind = _decode(decoder, media['wind'], root / 'wind.txth')
        departure = _decode(decoder, media['departure'], root / 'departure.txth')
    frames = array('h', [0]) * (SAMPLE_RATE * WARP_LANDING_OFFSET_MS // 1000)
    frames.extend(round(sample * .29) for sample in landing)
    end, offset = SAMPLE_RATE * WARP_APPROACH_MS // 1000, SAMPLE_RATE
    if len(wind) < offset + end or len(frames) < end:
        raise CueUnavailable('The reviewed warp cue is incomplete.')
    for index, sample in enumerate(wind[offset:offset + end]):
        gain = min(1., index / 2640, (end - index) / 2880)
        frames[index] = max(-30000, min(30000, frames[index] + round(sample * (.08 + .36 * index / end) * gain)))
    departure = array('h', (round(sample * .31) for sample in departure))
    if sys.byteorder != 'little':
        frames.byteswap()
        departure.byteswap()
    return {'arrival': frames.tobytes(), 'departure': departure.tobytes()}
