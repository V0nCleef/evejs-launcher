"""Installation-scoped card positions; never changes launch-group membership."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from src.config import CONFIG_DIR

ORDER_FILE = CONFIG_DIR / 'character-order.json'
MAX_CHARACTERS = 10_000
MAX_STORE_BYTES = 4 * 1024 * 1024
CharacterKey = tuple[int, int]  # immutable account ID, character ID


class CharacterOrderError(ValueError):
    """An invalid order or unsupported store must not replace saved choices."""


def validate_order(value: object) -> tuple[CharacterKey, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_CHARACTERS:
        raise CharacterOrderError('Invalid character order.')
    result = []
    seen = set()
    for item in value:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or any(type(part) is not int or not 0 <= part < 2**63 for part in item)
                or item[1] == 0):
            raise CharacterOrderError('Invalid character identity in saved order.')
        key = (item[0], item[1])
        if key in seen:
            raise CharacterOrderError('Duplicate character identity in saved order.')
        seen.add(key)
        result.append(key)
    return tuple(result)


def ordered_keys(saved: object, current: object) -> tuple[CharacterKey, ...]:
    """Apply saved ranks while appending new characters in their existing order."""
    saved, current = validate_order(saved), validate_order(current)
    present, ranked = set(current), set(saved)
    return tuple(key for key in saved if key in present) + tuple(key for key in current if key not in ranked)


def move_character(saved: object, current: object, visible: object,
                   source: CharacterKey, target: CharacterKey, *, after: bool) -> tuple[CharacterKey, ...]:
    """Move within visible slots, retaining hidden and temporarily absent pilots."""
    saved, current, visible = validate_order(saved), validate_order(current), validate_order(visible)
    shown = set(visible)
    if not shown <= set(current) or source not in shown or target not in shown:
        raise CharacterOrderError('The moved characters are no longer visible.')
    ranked = set(saved)
    full = saved + tuple(key for key in current if key not in ranked)
    if source == target:
        return full
    positions = [key for key in full if key in shown]
    positions.remove(source)
    positions.insert(positions.index(target) + int(after), source)
    replacement = iter(positions)
    return tuple(next(replacement) if key in shown else key for key in full)


def _scope(identity: str) -> None:
    if not isinstance(identity, str) or not identity or len(identity) > 512:
        raise CharacterOrderError('Character order needs a current EveJS installation.')


def _document(path: Path) -> dict:
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_STORE_BYTES + 1)
    except FileNotFoundError:
        return {'schema_version': 1, 'targets': {}}
    if len(raw) > MAX_STORE_BYTES:
        raise CharacterOrderError('The character order store exceeds its size limit.')
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise CharacterOrderError('The character order store could not be read safely.') from error
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document['schema_version'] != 1 or not isinstance(document.get('targets'), dict)):
        raise CharacterOrderError('The character order store uses an unsupported format.')
    return document


def load_character_order(identity: str, *, path: Path | None = None) -> tuple[CharacterKey, ...]:
    _scope(identity)
    document = _document(ORDER_FILE if path is None else path)
    return validate_order(document['targets'].get(identity, ()))


def save_character_order(identity: str, order: object, *, path: Path | None = None) -> None:
    """Replace one scope atomically; corrupt/newer data is preserved untouched."""
    _scope(identity)
    validated = validate_order(order)
    path = ORDER_FILE if path is None else Path(path)
    document = _document(path)
    document['targets'][identity] = validated
    payload = json.dumps(document, indent=2).encode('utf-8') + b'\n'
    if len(payload) > MAX_STORE_BYTES:
        raise CharacterOrderError('The character order store exceeds its size limit.')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.character-order-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
