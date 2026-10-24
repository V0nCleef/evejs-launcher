"""Bounded, data-only editing of native EVE settings; never execute objects.

The supported build stores groups in settings.user.ui.droneBlah2 and favourites
in drones_favoriteGroupID_<ship type>. Preserve these leaves, not the layout.
Scalar encodings and object headers stay opaque. Sharing is expanded when
writing; unsupported/cyclic streams fail before any existing file is changed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import struct

MAX_BYTES = 16 * 1024 * 1024
MAX_NODES = 200_000
MAX_DEPTH = 100
GROUPS = "droneBlah2"
FAVOURITES = "drones_favoriteGroupID_"


class SettingsFormatError(ValueError):
    pass


@dataclass(eq=False)
class Value:
    op: int
    raw: bytes = b""
    children: list[Value] = field(default_factory=list)
    first_count: int = 0

    def text(self) -> str | None:
        if self.op in {0x0e, 0x28}:
            return ""
        if self.op in {0x02, 0x0f, 0x10, 0x13, 0x2e}:
            return self.raw.decode("utf-8", errors="strict")
        if self.op in {0x12, 0x29}:
            return self.raw.decode("utf-16-le", errors="strict")
        return None


def _size(count: int) -> bytes:
    return bytes([count]) if count < 255 else b"\xff" + struct.pack("<I", count)


def loads(data: bytes) -> Value:
    if not 7 <= len(data) <= MAX_BYTES or data[0] != 0x7e:
        raise SettingsFormatError("Unsupported settings stream")
    count, = struct.unpack_from("<I", data, 1)
    end = len(data) - count * 4
    if count > MAX_NODES or end < 6:
        raise SettingsFormatError("Invalid settings sharing table")
    ids = list(struct.unpack_from(f"<{count}I", data, end)) if count else []
    if len(set(ids)) != count or any(not 1 <= i <= count for i in ids):
        raise SettingsFormatError("Invalid settings sharing IDs")
    pos, saved, nodes, used_ids = 5, {}, 0, 0

    def read(length):
        nonlocal pos
        if length < 0 or pos + length > end:
            raise SettingsFormatError("Truncated settings stream")
        value = data[pos:pos + length]
        pos += length
        return value

    def size():
        first = read(1)[0]
        return struct.unpack("<I", read(4))[0] if first == 255 else first

    def value(depth=0):
        nonlocal nodes, used_ids, pos
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise SettingsFormatError("Settings stream exceeds limits")
        opcode = read(1)[0]
        if opcode & 0x80:
            raise SettingsFormatError("Unsupported settings flags")
        op = opcode & 0x3f
        save = None
        if opcode & 0x40:
            if used_ids >= count:
                raise SettingsFormatError("Missing settings sharing ID")
            save = ids[used_ids]
            used_ids += 1
        if op == 0x1b:
            index = size()
            if save is not None or index not in saved:
                raise SettingsFormatError("Invalid settings reference")
            return saved[index]
        result = Value(op)
        if save is not None:
            saved[save] = result
        if op in {0x01, 0x07, 0x08, 0x09, 0x0b, 0x0e, 0x1f, 0x20, 0x24, 0x26, 0x28}:
            pass
        elif op in {0x03, 0x04, 0x05, 0x06, 0x0a, 0x0f, 0x11, 0x29}:
            result.raw = read({0x03: 8, 0x04: 4, 0x05: 2, 0x06: 1, 0x0a: 8, 0x0f: 1, 0x11: 1, 0x29: 2}[op])
        elif op in {0x02, 0x0d, 0x10, 0x12, 0x13, 0x2b, 0x2e, 0x2f}:
            length = read(1)[0] if op == 0x10 else size()
            result.raw = read(length * (2 if op == 0x12 else 1))
        elif op in {0x14, 0x15, 0x16, 0x25, 0x27, 0x2c}:
            length = 1 if op in {0x25, 0x27} else 2 if op == 0x2c else size()
            length *= 2 if op == 0x16 else 1
            if length > MAX_NODES:
                raise SettingsFormatError("Settings container exceeds limits")
            result.children = [value(depth + 1) for _ in range(length)]
        elif op in {0x17, 0x19}:
            result.children = [value(depth + 1) for _ in range(2 if op == 0x17 else 1)]
        elif op in {0x22, 0x23}:
            result.children.append(value(depth + 1))
            for section in range(2):
                start = len(result.children)
                while True:
                    if pos >= end:
                        raise SettingsFormatError("Missing object terminator")
                    if data[pos] == 0x2d:
                        pos += 1
                        break
                    result.children.append(value(depth + 1))
                if section == 0:
                    result.first_count = len(result.children) - start
                elif (len(result.children) - start) % 2:
                    raise SettingsFormatError("Invalid object dictionary")
        else:
            # Packed DB rows and pickle programs are not UI preference data.
            raise SettingsFormatError(f"Unsupported settings opcode {op:#x}")
        return result

    result = value()
    if pos != end or used_ids != count or result.op != 0x16:
        raise SettingsFormatError("Incomplete settings dictionary")
    return result


def dumps(root: Value) -> bytes:
    chunks, length, nodes, active = [b"\x7e\0\0\0\0"], 5, 0, set()

    def emit(data):
        nonlocal length
        length += len(data)
        if length > MAX_BYTES:
            raise SettingsFormatError("Expanded settings stream exceeds limits")
        chunks.append(data)

    def write(node, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH or id(node) in active:
            raise SettingsFormatError("Cyclic or oversized settings stream")
        active.add(id(node))
        op = node.op
        emit(bytes([op]))
        if op in {0x02, 0x0d, 0x12, 0x13, 0x2b, 0x2e, 0x2f}:
            emit(_size(len(node.raw) // (2 if op == 0x12 else 1)))
        elif op == 0x10:
            emit(bytes([len(node.raw)]))
        elif op in {0x14, 0x15, 0x16}:
            emit(_size(len(node.children) // (2 if op == 0x16 else 1)))
        emit(node.raw)
        if op in {0x22, 0x23}:
            write(node.children[0], depth + 1)
            for child in node.children[1:1 + node.first_count]:
                write(child, depth + 1)
            emit(b"\x2d")
            for child in node.children[1 + node.first_count:]:
                write(child, depth + 1)
            emit(b"\x2d")
        else:
            for child in node.children:
                write(child, depth + 1)
        active.remove(id(node))

    write(root)
    return b"".join(chunks)


def entries(value: Value):
    if value.op != 0x16:
        raise SettingsFormatError("Expected settings dictionary")
    # Native marshal dictionaries store each value before its key.
    return [(value.children[i + 1], value.children[i]) for i in range(0, len(value.children), 2)]


def get(value: Value, name: str) -> Value | None:
    matches = [v for k, v in entries(value) if k.text() == name]
    if len(matches) > 1:
        raise SettingsFormatError("Duplicate settings key")
    return matches[0] if matches else None


def _personal(name: str | None) -> bool:
    return name == GROUPS or bool(name and name.startswith(FAVOURITES))


def _leaves(data: bytes) -> list[tuple[Value, Value]]:
    ui = get(loads(data), "ui")
    return [(k, v) for k, v in entries(ui) if _personal(k.text())] if ui is not None else []


def has_drone_settings(data: bytes) -> bool:
    # The supported client's two keys are inline ASCII, not string-table IDs.
    return GROUPS.encode() in data or FAVOURITES.encode() in data


def signature(data: bytes) -> str:
    """Ignore access timestamps, dictionary order and drone set-list order."""
    def normalized(node, name=None):
        if node.op == 0x16:
            parts = sorted((normalized(k), normalized(v, k.text())) for k, v in entries(node))
            return b"D" + b"".join(_size(len(k)) + k + _size(len(v)) + v for k, v in parts)
        parts = [normalized(child) for child in node.children]
        if name == "droneIDs":
            parts.sort()
        return bytes([node.op]) + _size(len(node.raw)) + node.raw + b"".join(_size(len(p)) + p for p in parts)
    parts = []
    for key, setting in _leaves(data):
        if setting.op not in {0x14, 0x2c} or len(setting.children) != 2:
            raise SettingsFormatError("Invalid timestamped drone setting")
        parts.append((key.text(), normalized(setting.children[1])))
    return hashlib.sha256(repr(sorted(parts)).encode("utf-8")).hexdigest()


def merge(template: bytes, personal: bytes | None) -> bytes:
    if personal is None or not has_drone_settings(personal):
        return template
    leaves = _leaves(personal)
    if not leaves:
        return template
    if signature(template) == signature(personal):
        return template
    root = loads(template)
    ui = get(root, "ui")
    if ui is None:
        ui = Value(0x16)
        root.children.extend([ui, Value(0x10, b"ui")])
    ui.children = [node for k, v in entries(ui) if not _personal(k.text()) for node in (v, k)]
    for key, setting in leaves:
        ui.children.extend([setting, key])
    result = dumps(root)
    if signature(result) != signature(personal):
        raise SettingsFormatError("Drone settings did not survive merge")
    return result
