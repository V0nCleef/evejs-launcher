# Launcher 1.0.77 release validation

Simple New Character signs into an account without selecting an existing
character, allowing creation in-game while the game and market services stay
online. Advanced Character Create retains the offline maintenance workflow.

- The user confirmed that Simple creation works with automatic account login
  and no EVE debug console, then authorized the release.
- The final 377 release-related checks passed in isolated test-file processes.
  Coverage includes both creation methods, account-only and character login,
  manual fallback, Native and Docker verification gates, worker/queue behavior,
  real Windows profile junctions, shared settings, localization and schema-2
  activation-journal compatibility.
- All 197 test files were run against the final source. They recorded 3,778
  passes, seven skips, ten failures and one additional Qt process crash. The
  same remaining failures and crash reproduced in eight test files on untouched
  1.0.76 source. These existing fixture, expectation and Qt issues remain open;
  this release does not claim a green complete suite. Shared-process suite runs
  also encountered native Qt failures on both versions.
- A Docker launch regression found during comparison was fixed before release:
  named characters without an ID retain manual login; the account-only route
  can use automatic login without an ID.
- Foundation smoke, source compilation, dependency consistency, build-version
  agreement and working-tree whitespace checks passed. The builder uses the
  recorded Windows x64 CPython 3.12.14 and pinned build requirements.
- The packaged GUI opened as version 1.0.77 with isolated settings and service
  startup disabled, and a monitored normal close returned exit code 0. Its entry
  point and all 179 bundled application modules match the reviewed source after
  excluding compilation filenames. Both character-creation icons match their
  source bytes; bundled version, changelog and README match the release inputs.
- The updater ZIP is verified for CRC, top-level directory layout, bundled
  version, changelog heading and executable identity. Published assets are
  downloaded again and compared with local SHA-256 hashes.

Automatic login keeps the existing supported-client, local-endpoint and server
password-bypass checks. Unknown accounts require server-side automatic account
creation. No real account password is stored, and no client or renderer binary
is bundled. Docker paths have automated coverage; no new live Docker gameplay
validation is claimed.
