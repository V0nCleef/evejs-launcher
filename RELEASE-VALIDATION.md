# Launcher 1.0.75 release validation

This release fixes personal drone groups being overwritten when a follower
character relaunches using a shared settings profile.

- The user confirmed the installed candidate preserves drone groups in the
  real EVE client and approved public release.
- 34 focused shared-profile and native-settings checks passed before that
  manual test. Copied real settings were also decoded independently to confirm
  that only drone groups and per-ship favourites differ from the source profile.
- Frozen module comparison found exactly one changed existing module
  (`src.core.shared_settings_profile`) and one new module
  (`src.core.shared_drone_settings`). The entry point and all other application
  modules remain equivalent to 1.0.74. No DLL or PYD dependency was added or changed.
- The candidate started with isolated settings without launching a server or
  EVE client, and closed without a fatal startup error.
- Release packaging retains the exact manually tested executable. Only bundled
  VERSION, README and CHANGELOG data are updated for the public release.
- 95 focused shared-settings, build and updater checks passed from the public
  release checkout. The complete onedir ZIP is checked for CRC, version, layout
  and source-to-frozen module equivalence before publication. Hosted assets are
  downloaded and their hashes checked after publication.

Limits: no additional full live updater handoff or Docker gameplay test is run
for this Windows settings-only fix. Historical groups can only be recovered
while a matching saved local session remains available. Exact artifact hashes
are published in SHA256SUMS.txt.
