# Build Launcher 1.0.63

Use Windows x64, CPython 3.11.15 and requirements-build.txt. The recorded builder
used PyInstaller 6.21.0. In a fresh environment, install requirements-build.txt:

    python -m PyInstaller build.spec --noconfirm --distpath dist --workpath build

Distribute the entire EveJS-Launcher-V1 folder: its executable and `_internal`.
The build does not launch the application. Test against isolated settings.
SOURCE-MANIFEST.json records source/build inputs and the packaged EXE hash;
it does not promise reproducible EXE bytes across toolchains. See
RELEASE-VALIDATION.md for release validation.
