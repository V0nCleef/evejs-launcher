# Launcher 1.0.63 release validation

This release fixes launcher archive selection and adds explicit confirmation for
third-party mod updates in all eight supported languages. The user reviewed the
English native warning and approved its wording and layout.

- 98 focused updater, mod-update interface, confirmation and build-support tests passed.
- Source compilation, Foundation smoke and dependency checks passed.
- All eight warning layouts were rendered and reviewed.
- The packaged Windows launcher showed its native main window with isolated
  settings and closed with exit code 0. No server or client was started.
- The complete onedir archive passed CRC, bundled-version and layout checks.
  All changed runtime modules were found in the packaged Python archive.
- Source is published as tar.gz so older launchers select the Windows package,
  the only ZIP release asset.

Limits: no live mod installation or full desktop-to-GitHub update cycle was run.
A broader translation check has a pre-existing French identical-word allowance
failure for Actions; the same failure was reproduced with the unchanged catalog.
Exact artifact hashes are published in SHA256SUMS.txt.
