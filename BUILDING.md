# Build the 1.0.61 candidate

SOURCE-MANIFEST.json identifies this candidate executable and hashes every file
in this source archive. Use Windows x64, CPython 3.11.15 and the versions in
requirements-build.txt. The recorded builder used PyInstaller 6.21.0.

In a fresh environment, install requirements-build.txt and run:

    python -m PyInstaller build.spec --noconfirm --distpath dist --workpath build

Distribute the whole EveJS-Launcher-V1 folder, including its executable and
_internal directory. A build does not launch the application. Do not run main.py
or the executable unattended against normal Launcher settings.

Hashes bind this source snapshot to the tested packaging candidate; they do not
promise bit-identical executables on another compiler/toolchain. GPLv3 and the
third-party notices remain included. The final packaged pair is awaiting manual
in-game acceptance; no live Docker acceptance is claimed.
