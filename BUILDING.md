# Build Launcher 1.0.84

Use Windows x64, CPython 3.12.14 and requirements-build.txt. The recorded builder
used PyInstaller 6.21.0. In a fresh environment, install requirements-build.txt:

    python -m PyInstaller build.spec --noconfirm --distpath dist --workpath build

Distribute the entire EveJS-Launcher-V1 folder: its executable and `_internal`.
The build does not launch the application. Test against isolated settings.
SOURCE-MANIFEST.json records source/build inputs and the packaged EXE hash;
it does not promise reproducible EXE bytes across toolchains. See
RELEASE-VALIDATION.md for release validation.

## Isolated startup and close checks

The source entry point and packaged executable accept an explicit diagnostic:

```powershell
.\EveJS-Launcher-V1.exe --headless-check C:\Launcher-QA --check-mode immediate
```

Use an absolute writable directory without junctions or symlinks. Each invocation
creates a fresh `run-<id>` subdirectory with private APPDATA, LOCALAPPDATA, temporary
files and empty synthetic installation roots. Automatic updates, audio and service
startup are disabled. The diagnostic uses Qt's offscreen platform and never sends
desktop input. It does not reuse an existing launcher configuration or installation.

Modes are `immediate`, `normal`, `profiles`, `updater`, `wizard`, `roster` and `ownership`. They exercise
the common entry point and close handling, a two-profile round trip, a deliberately
delayed update-check response, and cancellation of the first-run wizard. The updater
mode substitutes metadata locally; it does not contact GitHub or install an update.
The roster mode verifies case-distinct accounts through the real data worker and
Characters page, including unavailable statistics and recovery, using local fixtures.
Read `run-<id>\result.json` and require both `status: passed` and process exit code 0.
Failures or a 35-second watchdog return a nonzero exit code. Invalid arguments fail
before normal launcher logging. Remove only the diagnostic directories you created
when their reports are no longer needed.

These checks cover the actual frozen Qt application without a desktop window.
They do not claim native window-manager, rendering or retail-gameplay coverage.

The roster mode also checks the degraded-mod Home warning and client-context guard
with local fixtures. It does not require Docker or control the desktop.

The ownership mode seeds duplicate records only in its private fixture. It verifies
the real repair confirmation, background worker, exact backup, preserved package
files and recovered ownership UI, then closes with no running worker threads.
