"""Standalone support runtime: shared production validators, no Qt application."""
import os
from pathlib import Path
from build_support import reject_contaminated_binaries, sanitize_build_path

os.environ["PATH"], _ = sanitize_build_path(os.environ.get("PATH", ""))
a = Analysis(
    [str(Path(SPECPATH) / "collector_entry.py")],
    pathex=[str(Path(SPECPATH).resolve().parents[1])], binaries=[], datas=[],
    hiddenimports=[], hookspath=[], runtime_hooks=[],
    excludes=["PyQt6", "PyQt5", "numpy", "pandas", "pygame", "uncompyle6"],
    noarchive=False,
)
reject_contaminated_binaries(a.binaries)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name="EveJS-Roster-Collector", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="EveJS-Roster-Collector", upx=False)
