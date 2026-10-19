# Native Startup Example

Requires Windows Native, Node.js and Launcher 1.0.74+. Import this folder using
**Add Folder** in a disposable EveJS installation, then enable it. It is disabled
on import. No game binaries, source patches or external services are included.

Before Game, Market or Start Stack, the declared `prepare_server_start` helper
records the SHA-256 generation of the root `package.json` under
`_local/native-startup-demo/prepared.json`. A repeated healthy check does not
rewrite that record. Changing the copied package metadata demonstrates a new
generation. Invalid metadata or an unknown receipt fails preparation and prevents
requested service startup. The loader merely prints a line when explicitly used.

Configure saves a label in `config/native-startup-demo.json`. Refreshing Mods
adds that declared file and `mods/` to the bundled updater's preservation policy,
even while this mod is disabled. Private `_local` state is already preserved by
the official updater. This helper never edits that label.

Use your own ID, config filename and private state directory. This is a working
host-protocol example, **not a native patch installer**. Fingerprinting any version
is safe here because only a private derived record changes. A real integration
must verify supported source hashes, service inactivity before shared writes,
transaction ownership, backups and rollback. Never copy this generation check
as permission to patch unknown upstream code. See the
[upgrade contract](../../../docs/how-to-make-a-mod/reference/evejs-upgrades.md).
