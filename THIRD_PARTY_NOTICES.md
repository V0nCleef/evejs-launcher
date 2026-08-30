# Third-party notices

EveJS Launcher is distributed under the GNU General Public License version 3. See [LICENSE](LICENSE).

The Windows release includes or links the following third-party software. Copyright remains with the respective authors. The corresponding license texts are included in the release under `licenses/`, except where the project's main GPLv3 text in `LICENSE` is the applicable text.

| Component | Version used for v1.0.38 | License | License text / source |
|---|---:|---|---|
| Python | 3.11.15 | Python Software Foundation License 2.0 | [`licenses/PYTHON-3.11.txt`](licenses/PYTHON-3.11.txt) · [Source](https://github.com/python/cpython) |
| PyQt6 | 6.11.0 | GPL v3 | [`LICENSE`](LICENSE) · [Source](https://www.riverbankcomputing.com/software/pyqt/) |
| Qt | 6.11.1 | LGPL v3 | [`licenses/QT-LGPL-3.0.txt`](licenses/QT-LGPL-3.0.txt) · [Source](https://code.qt.io/cgit/qt/qt5.git/) |
| PyQt6-sip | 13.11.1 | BSD 2-Clause | [`licenses/PYQT6-SIP-BSD-2-CLAUSE.txt`](licenses/PYQT6-SIP-BSD-2-CLAUSE.txt) · [Source](https://pypi.org/project/PyQt6-sip/) |
| uncompyle6 | 3.9.3 | GPL v3 or later; some files under MIT | [`LICENSE`](LICENSE) · [Source](https://github.com/rocky/python-uncompyle6) |
| xdis | 6.1.8 | GPL v2 or later | [`licenses/XDIS-GPL-2.0.txt`](licenses/XDIS-GPL-2.0.txt) · [Source](https://github.com/rocky/python-xdis) |
| spark-parser | 1.9.0 | MIT | [`licenses/SPARK-PARSER-MIT.txt`](licenses/SPARK-PARSER-MIT.txt) · [Source](https://github.com/rocky/python-spark) |
| PyInstaller | 6.21.0 | GPL v2 or later with a distribution exception; selected files under Apache-2.0 | [`licenses/PYINSTALLER.txt`](licenses/PYINSTALLER.txt) · [Source](https://github.com/pyinstaller/pyinstaller) |

The release archive also contains the license notices shipped by Qt and Python. The project repository provides the complete corresponding launcher source and build configuration for each published release tag.

The Deep Signal orbital background in `assets/deep_signal/operations_orbital.png`
was generated specifically for this project with OpenAI's image-generation
tool on 2026-08-12. It depicts an original, franchise-neutral orbital facility;
no CCP game asset, screenshot, soundtrack, or character voice is bundled.

The Celestial Transit music track in
`assets/audio/music/celestial_transit.wav` is an original composition
synthesized specifically for this project from pitched additive oscillators
and fixed tonal delay taps. It contains no samples, random/noise sources, EVE
Online music, or game audio. Its deterministic source is
`scripts/celestial_transit_synthesis.py`; the project-facing renderer is
`scripts/generate_celestial_transit.py`. The approved WAV has SHA-256
`451978321c360315010e8871945cdb99a92e05bee2d81148deb0600e64d66fc9`.

Personal music paths configured in the launcher are local user preferences.
Those files are referenced in place and are never copied into this repository,
uploaded, or included in a launcher release.

The fixed LYRA operational voice clips in `assets/audio/voice/lyra/` were
created specifically for this project from the approved finite catalog in
`src/audio/events.py`. Raw generation used Piper 1.3.0 with the
`en_GB-cori-high` voice model; the Cori model card identifies its source as
public-domain LibriVox recordings. The reviewed raw recordings were then
rendered with the deterministic `LYRA Balanced Lift` profile. Four phrases are
direct extracts from the approved option-2 review master; the remaining fixed
phrases use the documented matching filter, pacing, loudness, and limiter
profile. Piper, the voice model, FFmpeg, Rubber Band, and review inputs are
build-time tools or sources and are not included in the launcher. Exact input
hashes, processing parameters, line text, and output hashes are recorded in
`assets/audio/voice/lyra/manifest.json`; the local-only scripts are
`scripts/generate_lyra_voice_catalog.py` and
`scripts/process_lyra_voice_catalog.py`. No EVE/CCP voice or game audio is used,
and runtime character or account names are never synthesized.

EVE Online, EVE, and related marks are trademarks of CCP hf. EveJS Launcher is an independent community project and is not affiliated with CCP Games.
