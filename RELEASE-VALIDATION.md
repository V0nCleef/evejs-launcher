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
