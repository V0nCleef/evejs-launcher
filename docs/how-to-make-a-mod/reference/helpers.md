# Helper API 1

[Guide home](../00-start-here.md) | [Settings](settings.md) | [Ownership](ownership.md)

Helpers are ordinary local programs. Do not import Qt or launcher internals, or start the EVE client/Game server. The launcher invokes only a declared capability using separate request and result files.

## Invocation and limits

Node: `node <helper> --request <request.json> --result <result.json>`.

Executable: `<helper.exe> --request <request.json> --result <result.json>`.

PowerShell: `powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <helper.ps1> -RequestPath <request.json> -ResultPath <result.json>`.

The host passes arguments as a list with `shell=False`. PowerShell uses its own built-in module directory; Node runs with inherited `NODE_OPTIONS` removed. No command string is evaluated.

The process starts suspended, enters a kill-on-close Job Object, then resumes. Timeout, launcher exit and normal completion terminate remaining descendants. The host polls output files and rejects combined stdout/stderr over 256 KiB. Requests/replies are capped at 1 MiB; the entry helper is capped at 32 MiB. Default timeouts: profile/verify 2 minutes, disable/remove 10 minutes, install/recover 1 hour. Keep output concise; never print credentials.

## Actions

| Action | Purpose |
| --- | --- |
| `prepare_profile` | Prepare private mod files and/or the captured profile's EVE text settings. No shared client/global writes. |
| `install` | Install/update shared integration through the mod's own transaction. |
| `verify` | Verify physical installed state before client launch. Client-launch verification cannot propose config mutations. |
| `prepare_disable` | Restore shared integration before disabled state is recorded. |
| `prepare_remove` | Finish cleanup before owned package removal. |
| `recover` | Resolve the mod's interrupted installation/restoration. |
| `launch_result` | Observe the spawn result after profile preparation, with the captured profile and launch identity. |
| `client_exit` | Observe the exit code of that exact launched process while the launcher remains open. |

Install/verify/disable/remove/recover receive `profile: null`. Profile preparation
and client notifications receive the captured profile. Client packages verify
before optional profile preparation. A `settings` package can prepare profiles
without a binary installer.

### Client notifications

Declare either or both notification capabilities to opt in. Requests add an
`event` object, for example:

```json
{"launchId":"942d7e04-d779-4de9-b6f3-f1e26da76eda","status":"exited","pid":123,"exitCode":0,"errorType":""}
```

`launch_result` reports `started` with a PID, or `failed` with no PID and only the
exception type. It does not expose exception text, credentials or the command
line. `client_exit` reports `exited` and the process's exit code. Both share one
opaque launch ID; separate launches get separate IDs even when they use the same
profile. `started` means process creation succeeded, not successful game login.

Delivery runs off the GUI thread, in launch/exit order for each process, with a
30-second helper timeout. Replies use the ordinary success/state/message fields
but must leave contributions, environment, arguments and restartRequired empty,
and omit receipts. Notifications cannot change an already submitted launch.
Helpers may maintain their own private logs/saves. A failed callback is recorded
without stopping the client or preventing the subsequent exit callback.

These are best-effort observations, not durable shutdown hooks: no callbacks run
for attempts that fail before profile preparation completes; exiting the launcher
ends observation; removed or changed packages are not executed from stale context.
Use explicit cleanup/recovery for required restoration, never an exit notification.

When a user enables a mod, a declared `install` helper must finish successfully
with `state: "ready"` before the launcher changes its enabled flag or loader
filename. This applies to loaders and JSON-backed source/settings mods as well
as client packages. Pending or failed preparation leaves activation unchanged;
make installation repeatable so the user can retry. Mods without an `install`
capability keep their ordinary activation behavior.

For a schema-3 `source-integrated` mod stored in its own `mods/<folder>`, declare
both `prepare_remove` and `install` to use the launcher's Remove/Undo workflow.
The removal helper owns restoration of the source integration and must report
`ready` only when that work is complete. The launcher then sets the declared JSON
activation flag or package state to false and archives the private folder, retaining its contents.
Undo restores the folder disabled; enabling it runs `install` again. If something
else re-enabled the flag while the folder was archived, undo stops for review and
preserves that edit. Integrations without this contract, outside a private mod
folder, or owned by an older installer retain their existing removal provider.

## Staged whole-file contributions

An `install` helper can return a staged replacement from its own package folder:

```json
{"base":"evejs","path":"server/example.js","format":"file","source":"staged/example.payload"}
```

This is one entry in `contributions`, replacing the structured `key`/`value`
fields for that entry. `source` is a relative, ordinary file inside the selected
mod folder. The host captures its bytes, records the target's original content,
and commits through the same rollback journal as structured edits. A missing
target can be created. Identical content can have several owners; the common
file remains until the last owner is removed. Different replacements conflict.

EveJS file overlays target `server/`, excluding certificates and shared GameStore
data. Client targets use `base: "client"`. `prepare_profile` may stage files only
in mod-private `base: "profile"`, not EVE-owned profile settings. Use structured
edits for JSON/INI/YAML; database overlays are rejected. Ordinary file and ownership
journal size limits still apply (8 MiB each, including encoded ownership data).
Large binary installers must retain their own transaction/receipt contract.

Conflicting installation changes open a review with current/proposed text or binary
size and SHA-256. Keep current files cancels that action; Apply mod changes records
the explicit precedence and preserves the previous bytes before retrying activation.
Removal later restores the remaining owner's version, subject to review of newer edits.

## Independent text regions

Use `format: "text"` when a mod changes only part of a source file:

```json
{"base":"evejs","path":"server/example.js","format":"text","key":["// Economy rules\n","// Combat rules"],"value":"exports.priceMultiplier = 2;\n"}
```

The two key strings are unchanged start/end anchors already present in the file.
They may be comments or unique existing code; the host does not add them. Each
must occur exactly once, in that order. Only the text between them changes.
Regions with separate boundaries can coexist; removing one restores its original
region while preserving other regions and later edits outside it. Surrounding
bytes, BOM and encoding are preserved. Supply the intended newlines in `value`.

Two mods using the same anchors share one region's ownership and can request an
explicit precedence choice. Different overlapping regions, duplicated anchors,
and edits that would destroy another region's anchors are rejected. Do not guess
or broaden a region when a server update changes its context; update the mod's
compatibility handling. A local edit inside an owned region is reviewed on removal.
The same scope restrictions as whole-file overlays apply, and replacements are
limited to 65,536 characters with anchors of at most 128 characters each.

## Request

```json
{
  "protocol": "evejs_launcher_mod_v1",
  "requestId": "58d90f84-38e2-4bd7-8ac8-4a4b442882f0",
  "action": "prepare_profile",
  "mod": {
    "id": "example", "version": "1.0.0", "identity": "opaque mod identity",
    "root": "C:\\Example\\EveJS", "path": "C:\\Example\\EveJS\\mods\\example"
  },
  "runtime": {"backend": "native", "evejsRoot": "C:\\Example\\EveJS", "clientRoot": "C:\\Example\\Client\\tq"},
  "profile": {
    "id": "opaque profile identity", "root": "C:\\Example\\Profiles\\Pilot",
    "settingsRoot": "C:\\Example\\EVE\\Pilot\\settings",
    "modDataRoot": "C:\\Example\\Profiles\\Pilot\\mods\\opaque-directory"
  },
  "settings": {"global": {}, "profile": {"quality": 2}}
}
```

Use captured paths. Treat identities as opaque values; do not derive version-dependent replacements. `settings` holds validated values keyed by field ID, including missing-key defaults and explicit storage conversions. `modDataRoot` may not exist until the first save.

## Reply

All shown fields are required; `receipt` is the only optional extra field:

```json
{
  "protocol": "evejs_launcher_mod_v1",
  "requestId": "58d90f84-38e2-4bd7-8ac8-4a4b442882f0",
  "success": true, "state": "ready", "message": "Profile prepared.",
  "restartRequired": [],
  "contributions": [
    {"base": "profile", "path": "prefs.ini", "format": "ini", "key": ["Graphics", "Quality"], "value": 2}
  ],
  "environment": {"EXAMPLE_PROFILE": "C:\\Example\\Profiles\\Pilot\\mods\\opaque-directory"},
  "arguments": []
}
```

Echo protocol and request ID exactly. `success` is Boolean; `state` is `ready`, `pending`, `failed`. A failed action cannot be ready. `message` is bounded plain text up to 4,096 characters. `restartRequired` is a distinct list of `client`, `game_server`, `launcher`; `[]` or `["none"]` means none.

A successful exit alone proves nothing. Missing, malformed, stale or contradictory replies fail. Pending/failed results do not authorize contributions or client spawn. Request/result diagnostics live below the coordination root's `_local/launcher-mods/helpers/`.

## Contributions

Each of at most 512 entries contains exactly `base`, `path`, `format`, `key`, `value`. Bases follow [settings](settings.md). Values are finite JSON scalars: string, number, Boolean or null. Formats are `json`, `ini`, `yaml`; keys are arrays of strings. Profile preparation accepts only `profile` and `profile_settings`.

These are proposals. Do not directly write those files and then claim the host applied them. The host validates all profile helpers, merges launch outputs, then commits their contributions together before spawning. Repeating a contribution preserves unchanged bytes.

A reply cannot change its coordinator. Client-package actions use the physical client; ordinary global settings/server actions use the EveJS root. Split operations/forms requiring different coordinators. API 1 has no `allow_override`, raw command, file-copy, key-delete or arbitrary database-write field.

## Environment and switches

Return at most 64 distinct environment names and 64 complete named switches. Environment values are strings; names are ASCII identifiers. Generic variables such as `TRINITYPLATFORM` and `RESHADE_BASE_PATH_OVERRIDE` are supported. `EVEJS_*`, `EO_*`, OS/profile paths, identity, PATH, proxy/TLS policy and interpreter injection variables are host-owned.

An argument is one complete switch, such as `/example:enabled`, not separate option/value tokens. Server/port/proxy/login/character/settings/cache/no-console switches are protected. Identical outputs can be shared; conflicting values fail before any profile contribution commits. Do not bypass ownership with alternate spellings.

## Binary receipts

A ready global client-package action requires a [bound receipt](ownership.md): active for install, restored for disable/remove. Verify and recovery may confirm either active or restored state; recoverable is never a completed result. Before launching an enabled client package, verification must prove active state. Profile preparation may reference a receipt too. The host checks the binding; the mod checks real build compatibility, payloads, backups and binary state.

## Prefer client delivery through a reviewed EveJS login integration

For new client companions, prefer a reviewed EveJS login integration when the
server supports it. This avoids persistent archive modification. Existing
server-only mods and client-file mods keep their existing contracts; migration
must preserve compatibility rather than force every author to rewrite at once.
There is no Launcher capability called `login_delivery`, no automatic conversion
of client patches, and no general-purpose mod login API promised by this guide.
AutoMining's unreleased 1.0.7 candidate is one mod-specific implementation, with
local gameplay verification on a reviewed native EveJS build. It is not evidence
that every backend, shared-client combination or future server version works.

The Launcher invokes declared update, cleanup, installation, verification and
profile actions. The author implements payload delivery, compatibility checks,
failure handling and recovery within those existing actions. A process-start
notification is not proof that a character logged in or that the companion works.

For a login-delivered companion:

- Preserve the existing login function's execution and result, including other
  built-in handlers. Bound payload size and reject unreviewed server versions.
- Defer character RPCs and UI work until a character session exists. Validate the
  server/version readiness response and normalize only supported wire text forms.
- Keep handlers scoped to the current session, unregister on replacement, close
  stale windows and prevent duplicate handling by legacy and login companions.
- Verify real UI and gameplay after login, reconnect and character changes. A
  capability acknowledgement alone is not a gameplay test.

Migration must restore only owned data from verified backups, retain recovery
records outside the replaceable package, preserve unrelated archive entries and
settings, and roll back failed updates. Use installation/cleanup actions for
shared physical-client changes; `prepare_profile` is not permission to change
shared binaries or silently patch an archive during launch.

Test migration in both directions, enabled and disabled updates, interrupted
swaps, failed-update rollback, and switching between supported delivery methods.
Also test old and new installations sharing the same physical client. Restoring
an entry for one installation can remove a companion still required by another.
An older helper cannot be assumed to understand a newer delivery method.

**Automatic fallback must be demonstrated, not inferred.** An archive-delivery
fallback that works when already installed does not prove that a clean, migrated
client can switch to it automatically. If switching backend/build or selecting
an older installation requires an extra Install/Update action, the transition
does not meet a no-manual-migration promise. Resolve shared-client ownership and
transition handling before advertising that guarantee; do not add a hidden
archive repair to profile preparation to work around it.

### Reporting the selected client script delivery method

A Launcher implementing `client-script-delivery-v1` advertises that token in the
helper-process environment variable `EVEJS_LAUNCHER_HELPER_FEATURES` (comma-separated).
The request JSON and manifest schema remain unchanged. Only when that token is
present may a helper include this optional field in its usual successful reply:

```json
"clientScriptDelivery": "client-script-patch"
```

Allowed values are `login-handshake`, `client-script-patch`, and `none`. Report the
method actually selected for the supplied client and backend, not every method
the package supports. `none` means there is no client-script companion/patch (for
example, a graphics-only or server-only mod). Omission means unknown, not legacy.
On older Launchers, omit the field: their strict reply parser rejects unknown
fields. Existing helpers need no changes to keep working; reporting is opt-in.

For example, wrap the ordinary reply in an authored Node helper:

```javascript
const features = String(process.env.EVEJS_LAUNCHER_HELPER_FEATURES || "").split(",");
if (features.includes("client-script-delivery-v1") && result.success) {
  result.clientScriptDelivery = selectedMethod;
}
```

The Launcher records a report only after successful lifecycle or launch-preparation
commit. Its Mods row shows a non-blocking “Legacy client script patch — still
supported” notice only for an enabled mod reporting `client-script-patch`.
Reports are scoped to the mod folder, server installation, physical client and
backend, and invalidated by manifest identity or helper size/mtime changes.
A subsequent report replaces the previous method; successful cleanup or a
successful action omitting metadata clears it. The tooltip explicitly describes
the last report, not an inspection of client code or proof of current gameplay.

This is advisory metadata. It neither enables login delivery nor migrates files,
changes activation, runs a helper during discovery, or makes a mod unsupported.
Graphics/DLL mods are not labelled merely because they modify client files.
The feature is staged for an upcoming Launcher release; do not assume installed
1.0.60 hosts advertise it. Unknown/unreporting older mods are not guessed from names.

## Optional automatic client preparation (Launcher 1.0.61)

This is a separate lifecycle feature, not an effect of `clientScriptDelivery`.
Set `minLauncherVersion` to `1.0.61` when your migration depends on it. Older
Launchers reject that update before disabling the working package.

After a successful explicit `install` or `recover`, a helper may return:

```json
"clientPreparation": {
  "mode": "verify-install",
  "legacyVersions": ["1.0.6"]
}
```

Only include this field when `EVEJS_LAUNCHER_HELPER_FEATURES`, split on commas,
contains `client-preparation-v1`. The example version is illustrative: list only
exact releases whose helper behavior you have tested. The helper must declare
`install`, `verify` and `prepare_profile`, and the mod must have a stable GitHub
update source. The current version is enrolled automatically; no wildcard or
version-range matching is supported.

The host persists enrollment separately from the advisory notice cache. It is
scoped to the physical client, mod ID and update source (repository, asset pattern
and tag prefix). It applies across server folders and backends only to explicitly
enrolled versions. Cleanup retains this compatibility record because another
older server installation may still use the same client. It does not enable a
disabled mod or run a helper while discovering mods.

For an enabled enrolled release, before profile preparation the host runs its
own `verify`. If verification reports pending/failure, the host checks that EVE
clients are closed, calls its existing `install` with `profile: null`, commits
valid contributions and runs `verify` again. Only a ready recheck permits profile
preparation and launch. Malformed replies, process errors, changed manifests and
conflicting files do not silently become installation permission. There is one
repair attempt, not a retry loop. A healthy second client launch does not install
again. The physical client lease remains held through process creation.

Opting in is an author contract: `verify` must be read-only; `install` must be
idempotent, preserve unrelated edits, use verified backups/receipts, and limit
automatic repairs to client preparation. It must not need a server/Launcher
restart or mutate the running server. Preserve an already compatible fallback
when another client is running. Profile preparation must still not write shared
archives. An older release listed in the policy must satisfy the same install
and verification contract; it need not understand the new reply field itself.

Enrollment failures abort the lifecycle operation so the normal update rollback
can restore the old package. A corrupt coordination record fails closed; it is
not treated as permission to reinstall. Keep the known legacy path until the
normal upgrade, rollback, backend switch and shared-client checks pass. This does
not provide a general server login-payload API, and it does not retrofit automatic
repair into an unchanged older Launcher or an external manual server launcher.
