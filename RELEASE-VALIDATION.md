# Launcher 1.0.82 candidate validation (unreleased)

This candidate selectively adopts Tokeiito's PR #4 at upstream commit
a3dc00265e27d77a8f80a9f67f33b00b785183fd, based on published 1.0.81.
It has not been published or installed into the maintainer's live setup.

The final complete regression pass is **4,139 passed, zero failed, 7 skipped
across 215 test files**, each in a fresh process with private application state.
The 27 new controls cover the selected contribution and its safety adaptations.
Adjacent regressions, the separate bundled-image check, Foundation smoke,
Python compilation, dependency checks, and version agreement also passed.

The actual packaged application passes roster, normal startup and immediate
close checks using Qt's offscreen platform. It keeps case-distinct pilots through
statistics failure/recovery, renders and clears the degraded-mod warning, rejects
client context from that degraded state, exits zero and retires its windows and
threads. A minimum Home viewport check caught clipped warning text; the corrected
533-pixel line fits its 708-pixel area at the 756-pixel viewport. The final suite,
build, parity and offscreen checks follow that correction.

All 199 application modules plus main, 169 public data files and 67 dependency
binaries match the reviewed source and approved build roots. The candidate ZIP
contains the complete onedir runtime; CRC, version, layout and every-file hashes
are checked. SOURCE-MANIFEST.json records the matching source and final executable.

## Selective adoption and limits

Kept: typed mod-setup diagnostics, a non-blocking warning in Test Docker setup,
missing/relocated-folder notices, read-only status fallback and repair recovery.
Dropped: arbitrary unexpected exception-text logging and its required positive
logging test. Regression controls prove synthetic account/password JSON does not
enter the new diagnostics. The production data worker remains unchanged.

Adapted: detect drift after startup, keep fallback status separate from mod/client/
data authority, suppress deferred mod verification from degraded observations,
make Home display Mods need attention, and preserve explicit Stop for an observed
online service. Existing Connect-only and fresh-preflight mutation guards pass.
Folder notices are bounded, cover legacy IDs that change after rename, and never
guess a new owner, rewrite a registry or repair files automatically.

New Docker coverage uses controlled inspectors and real disposable filesystem
overrides/registries. It proves launcher behavior and command boundaries, not a
new real-Docker or retail-client run. Broken mod attachment still blocks character
data until repaired; status fallback does not bypass that requirement.

The maintainer's live installation, settings, mods, clients and services remain
untouched. No computer use or desktop input was performed.

## Published baseline evidence

The following records remain historical evidence for the published baseline.

# Launcher 1.0.81 validation

Validated on Windows x64 against the published 1.0.80 source. The final complete
suite ran every test file in its own process with private APPDATA and LOCALAPPDATA:
**4,112 passed, zero failed, 7 skipped across 214 files**. This is one complete
pass, without substituting results from earlier reruns. The existing Windows
file/symlink skips remain explicit. Three earlier Qt failure cases passed five
consecutive isolated rounds before this final suite (27 checks in each round).
Foundation smoke, the separate bundled-image check, Python compilation,
dependency checks and version agreement also passed. Earlier restricted-process and disposable-fixture setup failures
are retained separately from this final result.

## Roster correction and diagnostics

The Docker validator previously trimmed and case-folded distinct server account
keys. EveJS uses exact account keys. Regression controls preserve case-distinct,
Unicode-distinct and whitespace-distinct identities while still rejecting true
duplicate IDs/names and inconsistent character membership. An 11-account,
12-character SQLite fixture exercises the actual read-only Node projection and
matches the Native reader without changing the database bytes. Windows-aliased
accounts receive separate stable client-profile roots.

Optional roster statistics and display enrichment can fail without erasing valid
accounts or characters. The packaged offscreen roster mode exercises the real
worker and Characters page, keeps both case-distinct accounts, shows a bounded
warning and clears it on recovery. Normal startup and immediate close checks
also exit zero, retire their widgets and leave no running QThreads.

The collector uses the production validators and allowlisted read-only Docker
queries. Six controlled real Windows CLI-process cases cover healthy data,
case-distinct accounts, invalid statistics, invalid display data, invalid
identity and a stopped server. They verify that private sentinel values and
container identities stay out of the shareable JSON and that normal Launcher
configuration/logging is untouched. These controls use a local Docker CLI fixture;
they do not claim a new real-daemon or retail-client run for 1.0.81.

All 199 packaged application modules plus the main entry point, 169 public data
files and 67 dependency binaries match the reviewed source and approved build
roots. The complete onedir ZIP is checked for CRC, version, layout and every-file
hash equality. SOURCE-MANIFEST.json records the corresponding public inputs and
final executable. Publishing includes a fresh download and verification of the
GitHub asset, latest-release metadata and tag/commit; upload alone is insufficient.

JcKalck's first anonymous report identifies the rejected roster stage and account
identity field. No second report from his installation has been collected; the
maintainer chose to release based on the reproduced regression and verified fix.
This release does not claim confirmation on that installation.

The maintainer's live installation, ordinary Launcher settings and running
services were not changed. No desktop input or computer use was performed.

## Previous release evidence

The following 1.0.80 record remains historical evidence for unchanged components.
Its test counts and integration coverage are not new 1.0.81 runtime claims.

# Launcher 1.0.80 validation

Validated on Windows x64 against clean 1.0.79 source. The final complete suite
ran each test file in its own process with private APPDATA and LOCALAPPDATA:
**4,066 passed, zero failed, 7 skipped across 211 files**.
The skipped file/symlink controls require Windows privileges unavailable to the
test account. Real directory-junction controls passed with normal Windows access.
The separate bundled-image check, Foundation smoke, Python compilation, Node
helper/template syntax, dependency checks and version agreement also passed.

The final onedir executable passed 19 serial offscreen runs: ten immediate
closes, two normal closes, a profile round trip, a deliberately busy updater and
five first-run wizard cancellations. Every process exited zero, retired its
widgets and retained no descendants or running QThreads. The three earlier Qt
failure cases each passed five consecutive isolated repetitions after correction.
The first restricted run and negative teardown controls remain historical evidence.
This is a fresh complete pass, not a sum of selected reruns from that attempt.

All 197 packaged application modules plus the main entry point,
168 public data files and 67 dependency binaries were checked against
the reviewed source and approved build roots. The complete onedir archive passed
CRC, version, layout and every-file hash checks. SOURCE-MANIFEST.json identifies
the corresponding public source and executable without promising identical
rebuilt PE bytes across toolchains.

## Runtime and support boundary

Earlier isolated integration exercised actual EveJS 261108.1 Linux containers
and Windows retail client 3396210. That evidence covers Simple online creation,
Advanced creation and persistence, bind and parent-bind rosters, independent
worlds, source/Cargo mod lifecycles, login/menu readiness and reconnect. Native
261108.1 Game/Market startup and shutdown were also exercised. Final module
comparison retains this evidence for unchanged runtime components; the current
artifact additionally receives the offscreen and full-regression checks above.

| Area | Qualified behavior and limits |
| --- | --- |
| Docker startup | Delayed readiness, inspection failure and late Retry controls preserve running containers. Real healthy containers survived injected inspection/deadline failure. This does not reproduce every reported slow start. |
| Character data | Read-only account-safe rosters preserve unmatched records and empty accounts. Real bind/parent-bind stores passed. Named-volume projection has focused controls. |
| Advanced creation | Exact reviewed source contracts cover 0.12.5, 0.12.6, 0.12.9 and 261108.1. New real Docker creation was 261108.1. Fault rollback and restoration were controlled helper tests. Unknown layouts fail closed. |
| Portable mods | Generic helpers and one image contribution per service role. Mod authors must supply compatible Linux/server/client implementations. |
| Shared login | Native codec, synthetic additive load orders and actual AutoMining/Buyers retail UI acknowledgements passed. A delivery receipt does not substitute for a mod's gameplay-ready acknowledgement. |
| Sorter and profiles | Stable declared-dependency preview/save, stale/cycle/conflict guards, disabled-state preservation, registration-only removal, isolation and resource-collision controls. Sorting cannot repair incompatible source replacements. |
| Policy | Native regression controls and Connect-only mutation denial. Arbitrary remote Compose deployments remain outside the reviewed local contract. |

Updated mods are separate releases. Published Native-only packages are not
converted by changing their support declaration. AutoMining's delivery fix needs
its matching update; the Launcher alone cannot remove its old source-hash guard.
The Config Editor exporter repair is a separate source/version-bound companion;
Launcher roster retrieval does not depend on installing it.

No fresh desktop input, native window-manager close test or retail gameplay was
performed in the final pass. Earlier retail proof is limited to login, UI,
creation and reconnect. Older/native retail routes, the AutoMining 0.12.8 adapter,
unavailable exact reporter packages, full mod gameplay, retail purchase clicks
and long soak/scale remain unclaimed. Optional versioned Solo/Buyers packages
still need their own post-version Linux lifecycle run before standalone mod
certification. These limits do not represent failed Launcher regression tests.

The live installation, client and ordinary Launcher settings were not modified.
All six recorded file hashes match. This document records local preparation;
publication and live installation are separate actions.
