"""Developer tool: compile only launcher-owned bridge methods with client Python 2.7.

The generated JSON contains no CCP methods or archive data. Linux users need
neither this generator nor a Python 2 installation.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.core.overview_patch import _BRIDGE_METHODS, _compile_with_client_python, _PY27_MAGIC


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python-dll', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('src/core/client/overview_bridge_py27.json'))
    args = parser.parse_args()
    source = ('class OverviewBridge:\n'
        '    def Run(self, *args, **kwargs):\n'
        '        result = self._EveJSOriginalOverviewRun(*args, **kwargs)\n'
        '        self._evejsOverviewBridgeDone = False\n'
        '        uthread2.StartTasklet(self._EveJSOverviewBridgeWaitForSession)\n'
        '        return result\n' + _BRIDGE_METHODS)
    compiled = _compile_with_client_python(source, args.python_dll, _PY27_MAGIC + b'\0' * 4)
    result = {'schema': 1, 'sourceSHA256': hashlib.sha256(source.encode()).hexdigest(),
        'compilerSHA256': hashlib.sha256(args.python_dll.read_bytes()).hexdigest(),
        'bytecodeSHA256': hashlib.sha256(compiled).hexdigest(), 'bytecode': base64.b64encode(compiled).decode()}
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'bytecode'}))


if __name__ == '__main__':
    main()
