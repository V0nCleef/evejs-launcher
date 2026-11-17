"""Read-only native Python 2 verification of the portable overview bytecode."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import sys
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.core.overview_patch import _compile_with_client_python, _PY27_MAGIC, TARGET_ENTRY, SUPPORTED_CODE_SHA256
from src.core.portable_overview import graft_overview


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python-dll', type=Path, required=True)
    parser.add_argument('--client-archive', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    with args.client_archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest().upper() != SUPPORTED_CODE_SHA256:
            raise ValueError('Select the supported, unchanged client archive.')
    with zipfile.ZipFile(args.client_archive) as archive:
        original = zlib.decompress(archive.read(TARGET_ENTRY))
    hybrid = graft_overview(original)
    source = r'''class OverviewPresetSvc(object):
    def Run(self, *args, **kwargs):
        self.original_called = True
        return (args, kwargs)
    def Preserved(self):
        return (u'\u96ea', '\xff', 1L, 0L)
'''
    fixture = _compile_with_client_python(source, args.python_dll, _PY27_MAGIC + b'\0' * 4)
    portable_fixture = graft_overview(fixture)
    script = r'''import marshal, sys
def load(value):
    return marshal.loads(str(bytearray.fromhex(value))[8:])
def named(code, name):
    values = [item for item in code.co_consts if isinstance(item, code_type) and item.co_name == name]
    assert len(values) == 1
    return values[0]
def exact(a, b):
    assert type(a) is type(b), (type(a), type(b))
    if isinstance(a, code_type):
        for name in ('co_argcount','co_nlocals','co_stacksize','co_flags','co_code','co_consts','co_names','co_varnames','co_freevars','co_cellvars','co_filename','co_name','co_firstlineno','co_lnotab'):
            exact(getattr(a, name), getattr(b, name))
    elif isinstance(a, (tuple, list)):
        assert len(a) == len(b)
        for left, right in zip(a,b): exact(left,right)
    else:
        assert a == b
code_type = type(load('__ORIGINAL__'))
old = named(load('__ORIGINAL__'), 'OverviewPresetSvc')
new = named(load('__HYBRID__'), 'OverviewPresetSvc')
for left, right in zip(old.co_consts, new.co_consts): exact(left, right)
methods = len([value for value in old.co_consts if isinstance(value, code_type)])
uthread = type(sys)('uthread2')
sys.modules['os'] = type(sys)('os')
calls = []
uthread.StartTasklet = lambda callback: calls.append(callback)
sys.modules['uthread2'] = uthread
namespace = {}
exec load('__FIXTURE__') in namespace
instance = namespace['OverviewPresetSvc']()
assert instance.Run(7, named=9) == ((7,), {'named': 9})
assert instance.original_called and len(calls) == 1 and instance._evejsOverviewBridgeDone is False
assert tuple(type(value).__name__ for value in instance.Preserved()) == ('unicode', 'str', 'long', 'long')
path = unicode(str(bytearray.fromhex('__REPORT__')), 'utf-8')
with open(path, 'wb') as output:
    output.write('{"nativePython27":true,"originalClientMethodsPreserved":%s,"wrapperExecuted":true,"originalRunExecuted":true,"taskletScheduledOnce":true,"byteUnicodeLongTypesPreserved":true,"clientCodeExecuted":false}' % methods)
'''
    script = script.replace('__ORIGINAL__', original.hex()).replace('__HYBRID__', hybrid.hex()).replace('__FIXTURE__', portable_fixture.hex()).replace('__REPORT__', str(args.report.resolve()).encode().hex())
    library = ctypes.WinDLL(str(args.python_dll))
    library.PyRun_SimpleString.argtypes = [ctypes.c_char_p]
    library.PyRun_SimpleString.restype = ctypes.c_int
    result = library.PyRun_SimpleString(script.encode('ascii'))
    if result != 0 or not args.report.is_file():
        raise ValueError('Native Python 2 overview verification failed.')
    print(args.report.read_text())


if __name__ == '__main__':
    main()
