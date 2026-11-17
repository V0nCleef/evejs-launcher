"""Run each Windows test module in a fresh Qt/Python process.

The baseline's long single-process Qt run can crash inside translation widget
registration. Isolation still executes every test and preserves exact failures.
"""
from __future__ import annotations
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument('--module', nargs='+', help='Optional test module names without .py')
    parser.add_argument('--isolate-cases', nargs='+', default=[], help='Modules with verified baseline native crashes; isolate each case')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    start = time.monotonic()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
               APPDATA=str(output / "appdata"), LOCALAPPDATA=str(output / "localappdata"),
               TMP=str(output / 'scratch'), TEMP=str(output / 'scratch'))
    for name in ('APPDATA', 'LOCALAPPDATA', 'TEMP'):
        Path(env[name]).mkdir(parents=True, exist_ok=True)
    paths = sorted((root / 'tests').glob('test_*.py'))
    if args.module:
        available = {path.stem: path for path in paths}
        if set(args.module) - available.keys():
            parser.error('Unknown test module selected')
        paths = [available[name] for name in args.module]
    for path in paths:
        name = path.stem
        if name in args.isolate_cases:
            collection = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', str(path), '-p', 'no:cacheprovider'],
                cwd=root, env=env, capture_output=True, text=True, timeout=60)
            node_ids = [line.strip() for line in collection.stdout.splitlines() if '::' in line and line.strip().startswith('tests/')]
            if collection.returncode or not node_ids:
                raise RuntimeError('Could not collect isolated cases for ' + name)
            folder = output / name
            folder.mkdir(exist_ok=True)
            cases = []
            passed = skipped = 0
            for index, node_id in enumerate(node_ids, 1):
                stdout = folder / f'case-{index:03}.log'
                with stdout.open('wb') as stream:
                    process = subprocess.run([sys.executable, '-m', 'pytest', '-q', node_id, '-p', 'no:cacheprovider',
                                              '--basetemp', str(output / 'temp')],
                        cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
                lines = stdout.read_text(encoding='utf-8', errors='replace').splitlines()
                summary = next((line for line in reversed(lines) if ' in ' in line), 'no summary')
                cases.append({'node': node_id, 'returncode': process.returncode, 'summary': summary})
                for label in ('passed', 'skipped'):
                    match = re.search(r'(\d+) ' + label, summary)
                    if match and label == 'passed': passed += int(match[1])
                    if match and label == 'skipped': skipped += int(match[1])
            row = {'module': name, 'strategy': 'isolated_cases', 'returncode': int(any(case['returncode'] for case in cases)),
                   'summary': f'{passed} passed, {skipped} skipped; {len(cases)} separate processes', 'cases': cases}
            rows.append(row)
            (output / 'results.json').write_text(json.dumps({'elapsed':time.monotonic()-start,'modules':rows}, indent=2))
            print(f"{len(rows)} {name}: {row['returncode']} {row['summary']}", flush=True)
            continue
        stdout = output / (name + ".log")
        with stdout.open("wb") as stream:
            process = subprocess.run([sys.executable, "-m", "pytest", "-q", str(path), "-p", "no:cacheprovider",
                                      "--basetemp", str(output / "temp")],
                                     cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
        lines = stdout.read_text(encoding="utf-8", errors="replace").splitlines()
        row = {"module": name, "returncode": process.returncode, "summary": next((line for line in reversed(lines) if " in " in line), "no summary")}
        rows.append(row)
        (output / "results.json").write_text(json.dumps({"elapsed": time.monotonic()-start,"modules":rows}, indent=2))
        print(f"{len(rows)} {name}: {row['returncode']} {row['summary']}", flush=True)
    return int(any(row["returncode"] != 0 for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
