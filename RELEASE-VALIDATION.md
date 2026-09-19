# Release acceptance — 2026-09-19

The user installed the final Launcher 1.0.61 and AutoMining 1.0.7 pair and
confirmed it working in the Native game. Earlier checks covered the settings
window, saved preferences, locking/mining, survey after warp and Stop.
This confirmation applies to the exact package hashes below. The ZIPs were
not rebuilt after acceptance. Bundled candidate/verification wording records
the earlier packaging checkpoint; this record and the release notes supersede
that checkpoint's pending-release status.

Automated validation: 28 synthetic helper/Launcher transition cases, 83
AutoMining Node checks, 11 authored Python lifecycle tests and 15 generic
client-preparation tests passed, alongside focused Launcher, guide and updater
checks. Synthetic backend switching is covered; live Docker gameplay was not
tested for this release. The in-game window starter is an integration example,
not a separately live-tested mod.

- `EveJS-Launcher-V1.zip`: `ef9798de056d0372ad4af75b418cf09b270117217f01c64d650cb3f4725cb13e`
- `EveJS-Launcher-Source-1.0.61.zip`: `ba047239aed53d5ffabe230f67dae8be6d4cbdec1a2f277bf290175be03a461a`
- `AutoMining-1.0.7.zip`: `81981285a4e5089b3a1c97c3ea0b7de4778a41f255500cd081e747fbc2d31d7a`
