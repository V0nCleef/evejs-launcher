# Helper API 1

[Guide home](../MOD_AUTHORING.md) | [Settings](settings.md) | [Ownership](ownership.md)

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
