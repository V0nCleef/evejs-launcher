# Building this launcher source snapshot

This is the source/build-input bundle for the launcher identified in
`SOURCE-MANIFEST.json`. That manifest binds every included input to a SHA-256
and identifies the matching published/test candidate executable by hash. All
paths in it are relative; no developer checkout, older launcher executable,
private client files, or original build machine is required.

The launcher remains GPLv3; see `LICENSE` and `THIRD_PARTY_NOTICES.md`.
The separately distributed EveJS-DLSS5 project licence does not relicense this
launcher or its dependencies. Build dependencies retain their own terms.

## Windows x64 build

The recorded toolchain is CPython 3.11.15 x64, PyInstaller 6.21.0, and the exact
package versions in `requirements-build.txt`. Use a trusted Windows x64
CPython distribution. This document does not claim there is an official
Windows installer for every CPython maintenance version. A different Python
patch/build or package version may produce different bytecode or native files.

Extract this source ZIP into a new directory. In PowerShell, from its root,
with the intended interpreter selected as `python`:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m pip check
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYINSTALLER_CONFIG_DIR = Join-Path (Get-Location) '.pyinstaller-cache'
.\.venv\Scripts\python.exe -B -m PyInstaller --noconfirm --distpath dist --workpath build build.spec
```

Use a new extraction/output directory for each build. The commands do not
delete a previous build or installation. Avoid foreign Qt/ICU/OpenSSL toolchain
directories on PATH; `build_support.py` retains the existing contamination
checks. Preserve `upx=False` in the supplied spec.

The result is an onedir application:
`dist/EveJS-Launcher-V1/EveJS-Launcher-V1.exe` together with its `_internal`
directory. Distribute that complete pair, not only the executable. No launcher
or game is started by the build command.

To run this source intentionally, use `.\.venv\Scripts\python.exe main.py`.
That starts the normal launcher and may use your normal launcher settings;
running it is not part of an unattended source-integrity check.

## What the source bundle proves

The packaging gate verifies the final build snapshot's 180 inputs, 121 app
modules, 55 packaged data inputs and exact final executable hash. It includes
the same Python source, JS helpers, templates, reviewed artwork/audio, licences,
documentation and build spec used by that build. The extra build documentation,
version lock and four audio scripts are source-only additions; they are not
extra runtime files injected into the launcher.

This is a matching-input claim, not a promise of byte-identical PE rebuilding
across machines. Compiler/packager metadata, timestamps, dependency wheel
provenance and interpreter builds can differ. The source inventory is not a
digital signature or a substitute for trusted distribution.

The launcher source snapshot is not the entire development repository and does
not contain the full regression suite. Follow this file for building this
archive; do not assume repository-only test commands have their inputs here.

## Reviewed audio sources

The four original project scripts referenced in the third-party notices are
included unchanged:

- `scripts/celestial_transit_synthesis.py`
- `scripts/generate_celestial_transit.py`
- `scripts/generate_lyra_voice_catalog.py`
- `scripts/process_lyra_voice_catalog.py`

They were read before inclusion and contain no developer-specific absolute
paths, credentials or private recordings. They are not executed by the build.
The approved PNG/ICO/WAV assets already included in the snapshot are the exact
inputs used to package this launcher; rebuilding does not regenerate them.

Optional Celestial Transit synthesis uses NumPy, which is not a launcher build
dependency. Optional LYRA generation requires a separately supplied Piper
runtime/model/config. Its processing script also requires the reviewed raw
catalogue, the hash-pinned approved reference recording, and FFmpeg with Rubber
Band. Those historical reference inputs, models and tool binaries are **not**
included. The scripts disclose their parameters and expected reference hashes,
but this archive does not claim it can regenerate the historic voice catalogue
from nothing. The delivered WAVs can be inspected and modified directly; changes
would produce a distinct build needing its own validation.

## Publication boundary

Provide this matching source archive and clear access directions alongside the
corresponding binary release. Keep the dependency source/licence directions in
`THIRD_PARTY_NOTICES.md`. This document is not a legal-clearance statement.
Any changed final package requires the owner's fresh manual acceptance before
publication; passing tests alone does not authorize publishing.
