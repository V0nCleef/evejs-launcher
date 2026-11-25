"""Add launcher-owned Python 2.7 bridge methods without a host Python 2 runtime.

The small marshal-v2 codec preserves Python 2 byte strings versus Unicode. It
never executes client code. Original method bytecode and constants are retained.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, fields, replace
import hashlib
import json
from pathlib import Path
import struct

MAGIC = b'\x03\xf3\r\n'
BRIDGE_SHA256 = '758cfc22efd63f2831f23d5aad67626da369177376122006748aaae68edb32ad'
_NULL = object()


class Long(int):
    """Python 2 long constants keep their type even when they fit a C int."""


@dataclass(frozen=True)
class Code:
    argcount: int
    nlocals: int
    stacksize: int
    flags: int
    code: bytes
    consts: tuple
    names: tuple
    varnames: tuple
    freevars: tuple
    cellvars: tuple
    filename: bytes
    name: bytes
    firstlineno: int
    lnotab: bytes


class Reader:
    def __init__(self, content):
        if len(content) > 16 * 1024**2:
            raise ValueError('Overview bytecode exceeds its size limit.')
        self.content = content
        self.offset = self.count = 0
        self.interned = []

    def take(self, size):
        if size < 0 or self.offset + size > len(self.content):
            raise ValueError('Truncated overview bytecode.')
        value = self.content[self.offset:self.offset + size]
        self.offset += size
        return value

    def integer(self):
        return struct.unpack('<i', self.take(4))[0]

    def sequence(self, depth):
        size = self.integer()
        if not 0 <= size <= 200000:
            raise ValueError('Oversized overview bytecode sequence.')
        return tuple(self.read(depth + 1) for _ in range(size))

    def read(self, depth=0):
        self.count += 1
        if depth > 100 or self.count > 200000:
            raise ValueError('Overview bytecode exceeds its object limit.')
        tag = self.take(1)
        simple = {b'0': _NULL, b'N': None, b'F': False, b'T': True, b'S': StopIteration, b'.': Ellipsis}
        if tag in simple:
            return simple[tag]
        if tag == b'i':
            return self.integer()
        if tag == b'I':
            return Long(struct.unpack('<q', self.take(8))[0])
        if tag == b'l':
            count = self.integer()
            if abs(count) > 100000:
                raise ValueError('Oversized overview integer.')
            value = sum(struct.unpack('<H', self.take(2))[0] << (15 * index) for index in range(abs(count)))
            return Long(-value if count < 0 else value)
        if tag in (b'f', b'g'):
            return float(self.take(self.take(1)[0])) if tag == b'f' else struct.unpack('<d', self.take(8))[0]
        if tag in (b'x', b'y'):
            if tag == b'y':
                real, imaginary = struct.unpack('<dd', self.take(16))
            else:
                real = float(self.take(self.take(1)[0]))
                imaginary = float(self.take(self.take(1)[0]))
            return complex(real, imaginary)
        if tag in (b's', b't', b'u'):
            value = self.take(self.integer())
            if tag == b't':
                self.interned.append(value)
            return value.decode('utf-8') if tag == b'u' else value
        if tag == b'R':
            index = self.integer()
            if not 0 <= index < len(self.interned):
                raise ValueError('Invalid overview bytecode string reference.')
            return self.interned[index]
        if tag in (b'(', b'[', b'<', b'>'):
            value = self.sequence(depth)
            return {b'(': tuple, b'[': list, b'<': set, b'>': frozenset}[tag](value)
        if tag == b'{':
            value = {}
            while True:
                key = self.read(depth + 1)
                if key is _NULL:
                    return value
                value[key] = self.read(depth + 1)
        if tag == b'c':
            numbers = [self.integer() for _ in range(4)]
            values = [self.read(depth + 1) for _ in range(8)]
            line = self.integer()
            table = self.read(depth + 1)
            result = Code(*numbers, *values, line, table)
            if (any(type(getattr(result, name)) is not tuple for name in ('consts', 'names', 'varnames', 'freevars', 'cellvars'))
                    or any(type(getattr(result, name)) is not bytes for name in ('code', 'filename', 'name', 'lnotab'))):
                raise ValueError('Invalid overview Python 2 code object.')
            return result
        raise ValueError('Unsupported overview marshal object.')


def dump(value):
    """Canonical marshal-v2; avoid intern references so added methods cannot drift them."""
    def integer(number):
        return struct.pack('<i', number)
    if isinstance(value, Code):
        parts = [b'c']
        for field in fields(value):
            item = getattr(value, field.name)
            parts.append(integer(item) if field.name in ('argcount', 'nlocals', 'stacksize', 'flags', 'firstlineno') else dump(item))
        return b''.join(parts)
    if value is None: return b'N'
    if value is True: return b'T'
    if value is False: return b'F'
    if value is StopIteration: return b'S'
    if value is Ellipsis: return b'.'
    if type(value) in (int, Long):
        if type(value) is int and -(2**31) <= value < 2**31:
            return b'i' + integer(value)
        digits = []
        number = abs(value)
        while number:
            digits.append(struct.pack('<H', number & 32767))
            number >>= 15
        return b'l' + integer(-len(digits) if value < 0 else len(digits)) + b''.join(digits)
    if type(value) is float: return b'g' + struct.pack('<d', value)
    if type(value) is complex: return b'y' + struct.pack('<dd', value.real, value.imag)
    if type(value) in (bytes, str):
        content = value.encode('utf-8') if type(value) is str else value
        return (b'u' if type(value) is str else b's') + integer(len(content)) + content
    if type(value) in (tuple, list, set, frozenset):
        tag = {tuple: b'(', list: b'[', set: b'<', frozenset: b'>'}[type(value)]
        return tag + integer(len(value)) + b''.join(dump(item) for item in value)
    if type(value) is dict:
        return b'{' + b''.join(dump(key) + dump(item) for key, item in value.items()) + b'0'
    raise ValueError('Unsupported overview marshal value.')


def load_pyc(content):
    if content[:4] != MAGIC or len(content) < 9:
        raise ValueError('The overview bytecode is not Python 2.7.')
    reader = Reader(content[8:])
    result = reader.read()
    if not isinstance(result, Code) or reader.offset != len(reader.content):
        raise ValueError('Invalid overview bytecode module.')
    return result


def _one(code, name):
    matches = [item for item in code.consts if isinstance(item, Code) and item.name == name]
    if len(matches) != 1:
        raise ValueError('The overview code object is missing or ambiguous.')
    return matches[0]


def graft_overview(original):
    payload = json.loads((Path(__file__).parent / 'client/overview_bridge_py27.json').read_bytes())
    compiled = base64.b64decode(payload['bytecode'], validate=True)
    if hashlib.sha256(compiled).hexdigest() != BRIDGE_SHA256:
        raise ValueError('The bundled overview bridge failed verification.')
    owned = _one(load_pyc(compiled), b'OverviewBridge')
    module = load_pyc(original)
    old_class = _one(module, b'OverviewPresetSvc')
    if old_class.code[-2:] != b'\x52\x53' or module.code[-4] != 100 or module.code[-1] != 83:
        raise ValueError('The supported overview code tail changed.')
    expected = {b'Run', b'_EveJSOverviewBridgeWaitForSession', b'_EveJSOverviewBridgeWriteAck', b'_EveJSOverviewBridgeTryRun'}
    methods = [item for item in owned.consts if isinstance(item, Code)]
    if {item.name for item in methods} != expected or b'_EveJSOriginalOverviewRun' in old_class.names:
        raise ValueError('The overview bridge method set is invalid or already installed.')
    _one(old_class, b'Run')
    names = list(old_class.names)
    constants = list(old_class.consts)

    def instruction(opcode, index):
        if not 0 <= index < 65536:
            raise ValueError('The overview method table is too large.')
        return bytes([opcode]) + struct.pack('<H', index)

    def name_index(table, value):
        if value not in table:
            table.append(value)
        return table.index(value)

    extra = instruction(101, name_index(names, b'Run')) + instruction(90, name_index(names, b'_EveJSOriginalOverviewRun'))
    for method in methods:
        constants.append(method)
        extra += (instruction(100, len(constants) - 1) + instruction(132, 0)
                  + instruction(90, name_index(names, method.name)))
    hybrid = replace(old_class, code=old_class.code[:-2] + extra + old_class.code[-2:],
                     consts=tuple(constants), names=tuple(names), stacksize=max(4, old_class.stacksize))
    module_constants = [hybrid if item is old_class else item for item in module.consts]
    module_names = list(module.names)
    imports = b''
    for name in (b'os', b'uthread2'):
        imports += (instruction(100, name_index(module_constants, -1)) + instruction(100, name_index(module_constants, None))
                    + instruction(108, name_index(module_names, name)) + instruction(90, module_names.index(name)))
    result = replace(module, code=module.code[:-4] + imports + module.code[-4:],
                     consts=tuple(module_constants), names=tuple(module_names), stacksize=max(4, module.stacksize))
    output = original[:8] + dump(result)
    if load_pyc(output) != result:
        raise ValueError('The portable overview bytecode failed its round-trip check.')
    return output
