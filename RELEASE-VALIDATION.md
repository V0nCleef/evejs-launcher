# Launcher 1.0.76 release validation

Character startup no longer installs or verifies DLSS5, reads its installation
receipt, or requires a server attachment. Installed renderer detection uses the
physical client only. Private graphics preferences use a stable profile folder
across server upgrades; existing preferences and unrelated addons are retained.

- The user confirmed character startup and working DLSS with the tested 1.0.76
  executable and local DLSS lifecycle fixes, then authorized both releases.
- The public release retains that exact executable. Its entry point and all 177
  bundled application modules match the public release sources after excluding
  compilation-path metadata. No renderer or client binary is bundled or changed.
- Focused launch, public-mod integration, profile settings, graphics migration,
  worker, queue and shared-drone checks passed. Foundation smoke, compilation,
  dependency consistency and bundled-version checks passed.
- Native source and frozen launcher windows opened successfully with isolated
  settings. The source smoke closed normally; the frozen smoke exited after a
  normal close. The user's subsequent test provides the live character/DLSS proof.
- The complete test collection was also attempted. A shared-process Qt access
  violation required per-file process isolation. Those 194 file runs recorded
  3,734 passing tests, seven skips and ten failures; another Qt file crashed.
  All eight failing/crashing files reproduced the same outcomes on untouched
  1.0.75 source. These pre-existing fixture, expectation and Qt failures remain
  open; this release does not claim a green full suite or change their code.
- Release ZIPs are checked for CRC, version, top-level layout and executable
  identity. Published assets are downloaded again and compared with local hashes.

DLSS5 0.5.12 separately fixes physical-client verification and global removal
without requiring the selected server to be attached or the old server to exist.
Its renderer payload remains 0.5.11. No new GPU compatibility matrix, Docker
gameplay test or additional live auto-login test is claimed for this release.