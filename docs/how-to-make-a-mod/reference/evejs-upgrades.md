# Surviving EveJS updates (Launcher 1.0.74+)

[Guide home](../00-start-here.md) | [Helper API](helpers.md) | [Manifest](manifest.md)

These contracts are available to every mod author. No ID, repository or author
registration is required. The automatic preparation action currently covers
**Windows Native loader mods and JSON-activated source integrations**. Docker
does not run this action. Existing mods keep their behavior unless they opt in.

## Preserve packages and settings

Store durable server state under the resolved EveJS data root, normally
`_local/<your-mod-id>/`. Respect an explicit EveJS data-root override. The official
updater already preserves `_local`; a shared database needs its own consistent
backup and migration strategy. Never place user state in release-owned source.

For a valid installed mod, including a disabled one, the launcher's Native Mods
refresh prepares `tools/Updater/update-policy.json` to preserve `mods/`. Files
declared in your existing `settings.files` with `base: "evejs"` are also added
to `keepMine`. Those declarations use exact JSON/INI/YAML paths under `config/`,
as required by settings schema 1. A declared JSON activation file is preserved
too. No helper runs during discovery or policy preparation. Settings in `mod/`
are covered by folder preservation; client/profile files live outside this update.

There is no arbitrary executable path or source-directory preservation field.
For private binary state, TOML, logs or receipts, prefer your own `_local` folder
and implement any necessary migration in the mod. Legacy exceptions for older
AutoMining and NPC Buyers packages do not restrict this generic author contract.

Tell users to open the updated launcher, let Mods refresh, resolve any protection
warning, stop Game and Market, then use EveJS's bundled updater **in place**.
Reopen and refresh after each update: the incoming release may replace its policy.
The launcher keeps exact policy backups under `_local/launcher-updater-preservation`
and retains unknown policy fields. Unknown policy schemas are left unchanged with
a visible warning. It does not intercept updates run without this preparation.

## Prepare native integration before startup

Add this to a schema-3 Native loader or JSON-activated source mod:

```json
"launcherApi": {
  "version": 1,
  "minLauncherVersion": "1.0.74",
  "helper": {"runtime": "node", "path": "helper.js"},
  "capabilities": ["prepare_server_start"]
}
```

Merge capabilities you already implement; do not drop install/cleanup actions.
The helper uses the ordinary request/result protocol. `action` is
`prepare_server_start`, `runtime.backend` is `native`, `profile` is `null`,
and `runtime.evejsRoot` identifies the selected installation. There is no client
profile or client-root dependency. Reply with matching protocol/requestId,
`success: true`, `state: "ready"`, a concise message and empty `restartRequired`,
`contributions`, `environment` and `arguments`. Default timeout is two minutes.

The startup worker invokes opted-in enabled mods before Market database preflight
and before any requested service spawn, while retaining the root lifecycle lease.
Declared loader dependencies/order apply. Changed declarations/helpers, invalid
dependencies or failed/pending replies stop the requested start, including optional
Market fallback. Existing running services are not stopped. Helpers must not
spawn services, prompt interactively or assume the lease proves external services
are stopped. Multiple mods do not form a global rollback transaction: each helper
must be safe to retry if a later mod fails.

For a healthy install, return ready without modifying shared files, even when
the other service is running. For repair, independently confirm both services
are inactive, check exact reviewed source and owned backup bytes, preserve user
state, apply atomically with durable intent, then verify. Treat unknown edits,
partial upgrades, conflicting ownership and interrupted disable as review cases.
Do not silently re-enable a disabled integration. A known version string alone
is not proof that source is safe to patch.

Start with the runnable [Native Startup Example](../../../examples/mods/native-startup-demo/README.md).
It demonstrates actual import, activation, helper execution, idempotency and
settings protection without modifying native source. Test your own upgrade and
rollback against disposable installations before shipping.

## Compatibility is advisory, actual failures still matter

`compatibility.evejsVersions` describes the versions you tested. Launcher 1.0.74
lets users try mismatched or unknown EveJS releases through Run anyway and offers
newer mod releases for review. That does not bypass invalid manifests, missing
dependencies, helper errors, unsafe transactions or a mod's own source guards.
Document real support limits; do not claim untested future releases are supported.
