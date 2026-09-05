# EveJS Launcher mod authoring and integration guide

This document describes the mod contract implemented by EveJS Launcher 1.0.51.
It is a contract reference, not a promise that arbitrary EveJS source patches can
be disabled safely.

The launcher adds a **mod-management layer** around mechanisms EveJS already
provides. It does not add a universal upstream EveJS plugin API.

## What disabled means

After the Game server restarts, a disabled mod must contribute no gameplay
behavior: no entities, timers, repositories, commands, listeners, database
writes, or other runtime side effects.

There are two supported ways to reach that result:

- A loader mod is not passed to Node at all when disabled.
- A source-integrated mod keeps only a tiny configuration and status gate
  discoverable. That gate reports `disabled` and returns before the mod
  initializes gameplay behavior or touches its gameplay contract, state,
  repositories, entities, timers, or listeners. Side-effect-free adapter modules
  may already have been required by EveJS before that gate runs.

Disabling is not uninstalling. The mod's files, configuration, and saved state
remain on disk. A restart is required; hot unloading is deliberately unsupported.

If a mod performs an irreversible migration or side effect before checking its
enabled state, the launcher cannot undo it. That mod does not satisfy this
contract.

## Framework boundary

The launcher supplies:

- discovery of supported mod layouts;
- strict manifest and configuration validation;
- enable/disable transactions with locking, atomic replacement, and rollback;
- durable pending-restart intent;
- Native and Managed Docker loader selection;
- Native runtime attestation for source-integrated mods and exact Docker
  override evidence for loader mods;
- restart orchestration; and
- optional, separately trusted installer enrollment for a Remove action.

EveJS currently supplies the underlying loading mechanisms:

- CommonJS preloads under `mods/<name>/loader.js`;
- recursive service discovery for supported `*Service.js` files; and
- normal source integration, where a mod explicitly patches or extends EveJS.

The launcher intentionally does not execute commands, scripts, or hooks named by
a manifest. Unknown activation strategies fail closed instead of becoming an
arbitrary-code interface.

## Mod compatibility: depend on what the mod actually changes

Do not bind a mod to an EveJS version merely because its package is stored inside
an EveJS folder. Compatibility should follow the boundary the mod actually
modifies or calls:

| What the mod depends on | Compatibility that should be declared and tested |
| --- | --- |
| EveJS source, services, loader behavior, configuration, database schema, or internal APIs | Exact supported EveJS versions and source/API fingerprints |
| A physical EVE client installation, without reading or changing EveJS server code | Supported EVE client build, renderer/platform contract, and the mod's own manager protocol |
| An external service or stable public protocol | That service/API/protocol version, plus any required platform constraints |

An EveJS-version-independent claim is valid only when the complete mod remains
independent of EveJS internals. A package is still EveJS-coupled if any loader,
installer, helper, import, patch, launch argument, configuration path, or runtime
assumption depends on a particular EveJS release. `any` must mean “not a real
dependency,” not “we have not tested it yet.”

For a portable client-side or external mod:

- keep the copied package folder as replaceable release content, not as the sole
  home of durable state;
- keep only mod-owned receipts, downloads, backups, and audit history at a stable
  location associated with the physical target, or under a per-user application
  data directory keyed to that target;
- record the canonical physical target, supported client/API version, package
  version, and hashes needed for rollback;
- verify both the old and new owner before transferring state between sibling
  EveJS roots, and fail closed if ownership is ambiguous; and
- never move or relabel EveJS player, character, inventory, GameStore, or Market
  data as part of a client-mod handoff.

DLSS5 0.5.6 is the first reviewed use of this model: its durable mod state follows
the physical `tq` client while its separately downloaded package remains under
`<evejs>/mods/DLSS5`. The launcher itself does not contain or install DLSS5.

### Current third-party limit

The DLSS5 client-package manifest in Launcher 1.0.51 is a narrow, hard-coded
security contract, not a generic third-party client-mod API. Copying
`evejs-launcher.client-mod.json`, changing its ID, or declaring
`evejsVersionPolicy: "any"` will be rejected. Other authors can use the dependency
and state-ownership design above today, but automatic launcher execution requires
a reviewed generic client-package contract first.

A future generic contract should keep its protocol stable and authenticate mod
publishers or signed release manifests independently. The launcher should then
need an update only when that protocol or trust relationship changes—not for
every ordinary mod release. Until that contract exists, do not advertise normal
third-party client packages as launcher-managed.

## Compatibility matrix

| Contract | Discovery location | Toggle mechanism | Native | Managed Docker | Connect-only Docker |
| --- | --- | --- | --- | --- | --- |
| Loader mod | `<evejs>/mods/<name>/loader.js` | Rename `loader.js` and `loader.js.disabled` | Yes, in Modded mode | Yes | Read-only |
| Source-integrated schema v2 | `<evejs>/server/mods/<id>/evejs-launcher.mod.json` | Top-level JSON Boolean `enabled` | Yes | No | No |
| Arbitrary source patch | Anywhere else | Unknown | Shown only if it also exposes a supported contract | No | No |

Native Vanilla mode deliberately starts without loader preloads. A loader may be
configured enabled on disk while remaining absent from a Vanilla runtime. Use
Modded mode when starting Native EveJS with loader mods.

## Choosing a contract

Use a loader mod when all integration can happen from a CommonJS preload and the
mod can tolerate EveJS internal API changes. Disabled loader code is not loaded.

Use a source-integrated mod when the feature needs reviewed EveJS services or
source adapters that cannot be installed safely through a preload. This is more
version-coupled, so the installer must validate the exact EveJS versions and
source shapes it supports.

Do not pretend an arbitrary source patch is toggleable. First add an early
enabled gate, ensure every side effect is behind it, then expose one of the
supported launcher contracts.

## Loader mod contract

### Layout

```text
<evejs>/
  mods/
    example-mod/
      loader.js
      lib/
        runtime.js
```

The folder name is the launcher mod ID and display name for this legacy
contract. It must be safe as a Windows folder name, valid UTF-8, at most 255
characters, and must not end in a space or period.

Recognized states are:

- `loader.js` — configured enabled;
- `loader.js.disabled` — configured disabled;
- `loader.js.off` or `loader.js.bak` — recognized legacy disabled states.

The launcher always writes `loader.js.disabled` when it disables a loader. An
active loader and any disabled variant must never coexist. Multiple disabled
variants are also invalid. Any path, including a symlink, whose resolved target
escapes the selected EveJS root is rejected.

The launcher fingerprints the selected loader and refuses payloads larger than
2 MiB. Put substantial implementation in adjacent files and keep the preload
small.

### Loading behavior

For a Native Modded start, the launcher passes each selected loader to Node as a
separate `--require` preload. For Managed Docker, it produces a deterministic
launcher-owned Compose override and `NODE_OPTIONS` selection.

`process.argv` does not contain Node's consumed `--require` arguments. Do not use
`process.argv` as proof that a preload was or was not loaded.

### Safe preload pattern

EveJS does not currently expose stable lifecycle hooks for arbitrary external
plugins. A preload commonly wraps CommonJS loading:

```js
"use strict";

const Module = require("module");
const originalLoad = Module._load;
let patched = false;

Module._load = function evejsExampleLoad(request, parent, isMain) {
  const resolved = Module._resolveFilename(request, parent, isMain);
  const result = originalLoad.apply(this, arguments);
  const normalized = String(resolved).replace(/\\/g, "/");

  if (!patched && /\/target\/module\.js$/.test(normalized) &&
      result && typeof result.targetMethod === "function") {
    patched = true;
    installPatch(result);
  }
  return result;
};

function installPatch(target) {
  const original = target.targetMethod;
  target.targetMethod = function exampleWrapper(...args) {
    try {
      // Mod behavior. Keep failures isolated.
    } catch (error) {
      console.error("[example-mod]", error);
    }
    return original.apply(this, args);
  };
}
```

Resolve the target filename before matching it and also verify the export shape.
A raw request such as `./runtime` depends on the requiring module's directory and
is not a reliable identity.

If EveJS destructures an export into a local `const`, replacing that exported
property afterward will not update the captured reference. Patch a prototype or
an earlier stable call boundary instead.

Every wrapper must fail open to EveJS: isolate mod failures, call the original
function when appropriate, preserve its return value, and avoid partially
installed patches.

Loader mods are coupled to CommonJS and EveJS internals. An upstream migration
to ESM or an internal rename can break them.

## Source-integrated schema-v2 contract

### Required layout

```text
<evejs>/
  config/
    mods/
      example-mod.json
  server/
    mods/
      example-mod/
        evejs-launcher.mod.json
        index.js
        lib/
          runtime.js
  server/
    src/
      services/
        exampleMod/
          exampleModAdapterService.js
```

Only the manifest and configuration paths are launcher contracts. The runtime
and service paths are examples; the mod installer owns its reviewed EveJS
integration.

The manifest folder and `id` must match exactly.

### Complete manifest schema

The manifest must be UTF-8 JSON without a byte-order mark or comments. Its size
limit is 64 KiB. Duplicate keys, non-finite numbers, invalid Unicode, unsafe
paths, missing fields, and unknown fields are rejected.

```json
{
  "schemaVersion": 2,
  "id": "example-mod",
  "displayName": "Example Mod",
  "version": "1.0.0",
  "description": "What this mod changes.",
  "kind": "source-integrated",
  "supportedBackends": ["native"],
  "activation": {
    "strategy": "json_boolean",
    "configPath": "config/mods/example-mod.json",
    "property": "enabled",
    "allowedConfigSchemaVersions": [1]
  },
  "status": {
    "protocol": "evejs_mod_status_v1",
    "transport": "server_stdout"
  },
  "restart": "game_server"
}
```

The top-level object must contain exactly:

- `schemaVersion`: integer `2`;
- `id`: `[a-z0-9][a-z0-9._-]{0,63}`, equal to the manifest folder name;
- `displayName`: non-empty trimmed text, at most 100 characters;
- `version`: non-empty trimmed text, at most 64 characters;
- `description`: trimmed text, at most 500 characters; it may be empty;
- `kind`: exactly `source-integrated`;
- `supportedBackends`: exactly `["native"]`;
- `activation`: the exact activation object below;
- `status`: the exact status object below; and
- `restart`: exactly `game_server`.

The `activation` object must contain exactly:

- `strategy`: exactly `json_boolean`;
- `configPath`: exactly `config/mods/<id>.json`, using forward slashes;
- `property`: exactly `enabled`; and
- `allowedConfigSchemaVersions`: 1 to 32 unique ascending integers from 1
  through 65,535.

The `status` object must contain exactly:

- `protocol`: `evejs_mod_status_v1`; and
- `transport`: `server_stdout`.

Schema v2 does not accept Docker support, nested enabled properties, custom
commands, custom restart scopes, or author-defined status transports.

### Configuration

The configuration file must be UTF-8 JSON without comments and at most 2 MiB.
It must contain a supported integer `schemaVersion` and an exact Boolean
`enabled` value:

```json
{
  "schemaVersion": 1,
  "enabled": false,
  "exampleSetting": 25
}
```

Additional mod-owned fields are allowed. The launcher changes only the semantic
value of `enabled`; it verifies that every other value is unchanged. Writes use
a shared lifecycle lock, compare-and-swap protection, same-directory atomic
replacement, post-write verification, and rollback on failure.

A fresh install should default to `enabled: false`. An upgrade should preserve
the user's existing configuration bytes whenever its schema is still supported.

### Required bootstrap boundary

The first mod-owned code reached by EveJS must read and validate configuration
before importing gameplay modules:

```js
"use strict";

function emitState(state) {
  process.stdout.write(
    `EVEJS_MOD_STATUS ${JSON.stringify({
      id: "example-mod",
      pid: process.pid,
      state
    })}\n`
  );
}

const config = readAndValidateConfig();
if (config.enabled === false) {
  emitState("disabled");
  return;
}

const runtime = require("./lib/runtime");
runtime.initialize(config);
emitState("running");
```

The actual module boundary may differ, but the ordering may not:

1. Read and validate configuration.
2. If disabled, emit `disabled` and return.
3. Load and initialize all gameplay behavior.
4. Emit `running` only after initialization succeeds.

Do not create repositories, entities, timers, listeners, services, commands, or
database state before step 2. Do not emit `running` before a partially failed
initialization.

### Runtime status protocol

Every discovered source-integrated mod must emit exactly one complete marker per
Game-server start:

```text
EVEJS_MOD_STATUS {"id":"example-mod","pid":1234,"state":"disabled"}
```

or:

```text
EVEJS_MOD_STATUS {"id":"example-mod","pid":1234,"state":"running"}
```

The JSON object must contain exactly `id`, `pid`, and `state`:

- `id` must match the manifest ID;
- `pid` must be the positive integer `process.pid` of the current Game server;
- `state` must be exactly `running` or `disabled`;
- the prefix must be exactly `EVEJS_MOD_STATUS ` with one trailing space;
- the JSON must consume the rest of that stdout line; and
- the complete line must not exceed 4 KiB.

Missing, duplicate, malformed, unexpected, stale-PID, or state-mismatched markers
make runtime verification fail closed. The launcher waits for the server to
become ready, allows a bounded three-second marker window, and stops a runtime
whose declared state cannot be verified.

The stable Game-server stdout evidence used for startup attestation is capped at
2 MiB. Oversized evidence is rejected instead of being read or parsed
unboundedly, so keep startup output concise and emit the status marker promptly.

The launcher owns `<evejs>/_local/.evejs-launcher-mod-runtime.json`. Mods and
installers must not create or edit that file.

## Launcher activation lifecycle

Changing a toggle changes configured state. It does not mutate the already
running Node process.

The normal lifecycle is:

1. The author or installer places a valid supported contract.
2. The user opens **Mods** or presses **Refresh**.
3. The launcher validates and displays the mod.
4. A toggle transaction changes only the supported activation primitive.
5. The launcher records durable pending-restart intent.
6. **Apply & Restart Server** starts a frozen plan.
7. The launcher verifies the actual runtime and clears the pending intent.

Rows may report configured state, restart required, runtime unverified, verified,
invalid, external removal, or repair-required removal. These states are not
interchangeable: configured enabled does not prove the running server loaded the
mod. A toggle transaction can successfully change configured state, but the
launcher does not call that state runtime-effective until launch-time evidence
verifies the exact expected result. A source-integrated mod must provide that
evidence through its Game-server attestation marker.

### Interrupted Managed Docker Apply

Managed Docker loader changes use two launcher-owned files beneath
`<evejs>/.evejs-launcher/`:

```text
compose.mods.yaml
compose.mods.transaction.json
```

Before replacing the exact Compose override, the launcher writes a durable
transaction marker containing the prior and desired override SHA-256 values and
a one-use authorization token. The marker remains until the exact frozen target
has been accepted by the authorized lifecycle worker. A write, handoff, or
launcher/power failure therefore cannot quietly expose a half-applied selection
to an unrelated start or restart.

While a marker is present, ordinary Docker lifecycle attachment fails closed.
Do not delete or edit either file manually. Reopen **Mods**, keep the same visible
toggle selection, and press **Apply & Restart Server** again. If the committed
override still matches the marker's exact desired hash, explicit Apply resumes
and consumes that transaction. If the override still matches the prior hash, the
stale marker is cleared safely before a new Apply. If it matches neither hash,
lifecycle operations stay blocked until the artifact is repaired; the launcher
will not guess which state is authoritative.

Launcher activation journals live under
`%APPDATA%/EveJS-Launcher/mod_activation_state`, keyed by a hash of the selected
EveJS root. The cross-process lifecycle lock is
`<evejs>/_local/.evejs-mod-lifecycle.lock`. Both are launcher-owned coordination
data. Mods must neither treat them as an activation API nor edit them directly.

## Installation

The launcher currently has no generic **Install** button. A mod's own installer
places its payload, configuration, and launcher contract. **Refresh** then
discovers it automatically; there is no hard-coded allow-list of mod IDs.

A safe installer transaction should:

1. Resolve and validate the exact target EveJS root.
2. Verify the EveJS version and every source shape the mod depends on.
3. Require affected Game and Market services to be stopped.
4. Acquire the shared root lifecycle lock.
5. Inventory and hash every file it may touch.
6. Back up existing files before replacement.
7. Install runtime and source integration.
8. Create a fresh disabled config or preserve a compatible existing config.
9. Install the early activation/status adapter.
10. Publish `evejs-launcher.mod.json` last.
11. Verify hashes, syntax, configuration, and complete integration.
12. Commit an install journal and optional launcher-removal enrollment.
13. Roll back every touched file if any step fails.

Publishing the manifest last prevents the launcher from discovering a partial
installation.

An installer must never treat shared EveJS data as exclusively mod-owned. Player,
account, character, item, location, Market, and shared GameStore records are out
of scope for a generic purge.

## Optional launcher-managed removal

A valid activation contract makes a mod discoverable and toggleable. It does
not authorize code execution for removal. A source-integrated manifest
deliberately has no `uninstallCommand` field.

To display **Remove**, a trusted installer must separately enroll the current v2
removal provider. This provider is intentionally narrow:

- Windows per-user installation;
- Inno Setup;
- provider ID `inno-user-v2`;
- a source-integrated schema-v2 mod with a concrete manifest and package
  version; legacy loader-only rows remain toggleable but cannot enroll this
  provider;
- one installation of a given mod ID per Windows user; and
- one exact EveJS root bound to that enrollment.

This is a provider contract, not a generic cross-platform installer SDK.

### Recovery kit

The installer keeps a self-contained recovery kit at:

```text
%LOCALAPPDATA%/Programs/EveJS Mods/<id>/
  unins000.exe
  unins000.dat
  <id>-package.zip
  <id>-removal-inventory.json
  bootstrap/
    Expand-EmbeddedPackage.ps1
```

The exact persistent package name is `<id>-package.zip`. The uninstaller must be
able to verify and use that package independently of the original downloaded
Setup file. The Inno executable and its adjacent `unins000.dat` are one removal
program and are hashed separately. The launcher refuses the Remove action if
either file changes.

The installer also owns an active pointer at:

```text
<evejs>/_local/<id>/install/current.json
```

The pointer is opaque to the launcher; the launcher binds removal to its exact
SHA-256. The mod's installer and uninstaller own the journal schema and must use
it to restore only files that installation actually changed.

### Managed registry enrollment

Create this per-user key:

```text
HKCU/Software/EveJS Launcher/Managed Mods/<id>
```

It must contain exactly these values and Windows registry types:

| Value | Type | Required meaning |
| --- | --- | --- |
| `SchemaVersion` | `REG_DWORD` | `2` |
| `Provider` | `REG_SZ` | `inno-user-v2` |
| `AppId` | `REG_SZ` | Inno AppId GUID including braces |
| `ModId` | `REG_SZ` | Exact manifest ID |
| `DisplayName` | `REG_SZ` | Exact manifest display name |
| `PackageVersion` | `REG_SZ` | Exact manifest version |
| `EveJSPath` | `REG_SZ` | Canonical selected EveJS root |
| `BundleSha256` | `REG_SZ` | Lowercase SHA-256 of `<id>-package.zip` |
| `ExpandHelperSha256` | `REG_SZ` | Lowercase SHA-256 of the persistent helper |
| `CurrentPointerSha256` | `REG_SZ` | Lowercase SHA-256 of `current.json` |
| `RemovalInventorySha256` | `REG_SZ` | Lowercase SHA-256 of `<id>-removal-inventory.json` |
| `UninstallerSha256` | `REG_SZ` | Lowercase SHA-256 of `unins000.exe` |
| `UninstallerDataSha256` | `REG_SZ` | Lowercase SHA-256 of `unins000.dat` |
| `SupportsPurgeState` | `REG_DWORD` | `0` or `1` |

Do not add private values to this key. Extra, missing, conflicting-across-
registry-view, mistyped, or mismatched values put the row into **Repair** instead of
authorizing execution.

The standard Inno uninstall entry under
`HKCU/Software/Microsoft/Windows/CurrentVersion/Uninstall/<AppId>_is1` must also
agree exactly on `UninstallString`, `InstallLocation`, and `DisplayVersion`.
`UninstallString` must name only the exact registered `unins000.exe`; arguments
are not accepted in this authority field.

The launcher revalidates the manifest contract, target root, safe non-reparse
paths, all registered hashes, both registry views, the standard Inno metadata,
and the current pointer immediately before removal.

### Removal inventory

`<id>-removal-inventory.json` is strict UTF-8 JSON without a BOM and at most
1 MiB. It has this exact schema:

```json
{
  "schemaVersion": 1,
  "modId": "example-mod",
  "entries": [
    {
      "path": "server/mods/example-mod/evejs-launcher.mod.json",
      "postRemove": { "kind": "absent" }
    },
    {
      "path": "server/src/exampleService.js",
      "postRemove": {
        "kind": "sha256",
        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
      }
    }
  ]
}
```

The root object contains only `schemaVersion`, `modId`, and `entries`.
`schemaVersion` is `1`; `modId` matches the runtime manifest; and `entries`
contains 1 to 4,096 items. Each path is a unique, case-insensitive,
EveJS-root-relative forward-slash path of at most 512 characters. Absolute
paths, backslashes, drive separators, control characters, empty components, and
`.` or `..` components are rejected.

Each `postRemove` object is exactly one of:

- `{ "kind": "absent" }` when the installed path must no longer exist; or
- `{ "kind": "sha256", "sha256": "<lowercase SHA-256>" }` when uninstall
  must restore the exact pre-install file.

The launcher manifest itself must be enrolled as `absent`. Inventory every
executable integration path installed or patched by the mod; do not inventory
saved player data as executable integration. The installer should derive the
inventory from the verified install journal only after installation succeeds,
then hash the completed inventory into the managed registry enrollment.

After the uninstaller exits, the launcher evaluates every entry beneath the
same canonical EveJS root. An `absent` path must be gone; a restored path must be
a safe regular file with the exact enrolled hash. The entire
`server/mods/<id>` source-integration directory must also be absent. Any mismatch
fails removal verification even when Inno returned exit code zero.

### Setup/uninstall serialization and launcher authorization

Provider v2 serializes Setup and removal with this per-AppId Windows mutex:

```text
Local\EveJSLauncher.ManagedMod.<APPID>
```

`<APPID>` is the Inno AppId GUID with braces and hyphens removed and letters
uppercased. Compatible Setup and direct uninstall runs create that mutex and
must abort before changing files if it already exists.

For launcher-managed removal, the launcher owns the provider mutex continuously
from final registration revalidation through terminal removal proof. It creates
a second one-use mutex named
`Local\EveJSLauncher.ManagedModAuth.<token>`, where `<token>` is 64 lowercase
hexadecimal characters, and passes `/LAUNCHERTOKEN=<token>` to the exact verified
uninstaller together with exactly one
`/LAUNCHERROOT=<canonical EveJS root>` switch. The uninstaller must open both
existing mutexes and retain both handles for its complete process lifetime. When
`/LAUNCHERTOKEN` is present, it must reject a malformed, expired, duplicated, or
independently supplied token/root before changing files. It must canonicalize
and require three-way equality between the launcher root, the provider product
registration's `EveJSPath`, and the launcher-managed enrollment's `EveJSPath`.
When the token switch is absent, the uninstaller follows its direct uninstall
path and must acquire the provider mutex itself. This handshake lets the
authorized child run without deadlocking on the provider mutex while preventing
a concurrent compatible Setup or direct uninstall from swapping its registered
executable, data file, target root, journal, or inventory.

### Uninstaller switches and terminal guarantees

The launcher invokes the verified uninstaller with Inno's silent switches, the
launcher authorization token described above, and exactly one state policy:

```text
/KEEPSTATE
```

or:

```text
/PURGESTATE
```

`/KEEPSTATE` is the default and must preserve editable configuration and
mod-local state.

`/PURGESTATE` is offered only when `SupportsPurgeState` is `1`. In the current
reference implementation, purge means moving documented mod-local data to a
recoverable quarantine. It must not delete shared GameStore or Market records.

A safe uninstaller should:

1. Validate the exact target, journal, hashes, and installed integration.
2. Acquire the shared lifecycle lock.
3. Withdraw the launcher manifest first.
4. Remove installed runtime files and restore backed-up EveJS files.
5. Preserve or quarantine only documented mod-local state.
6. Remove the current pointer and managed registry enrollment.
7. Let Inno remove its recovery kit and standard uninstall registration.

After the uninstaller exits, the launcher verifies every removal-inventory end
state and proves that the mod is no longer discoverable. Its manifest,
source-integration directory, active pointer, managed enrollment, standard
uninstall entry, helper, bundle, removal inventory, `unins000.exe`,
`unins000.dat`, and recovery-kit directory must all be absent. It reports success
only after those checks pass.

Windows **Installed apps** remains a recovery fallback because Inno creates its
normal entry. The intended user workflow is the **Remove** button on the launcher's
Mods page.

## Updates and versioning

Treat the manifest as immutable for one installed version. If an upgrade changes
its activation contract, runtime integration, or recovery package, update the
manifest version and rebuild the installer enrollment hashes as one transaction.

An upgrade should:

- validate the existing install journal before touching files;
- preserve compatible user configuration and saved state;
- back up files again before replacing them;
- keep the mod disabled if it was disabled before the upgrade;
- republish the manifest and current pointer only after verification; and
- replace the recovery kit and registry hashes atomically from the user's point
  of view.

Do not silently widen supported EveJS versions. Validate the exact versions and
source fingerprints exercised by the mod's tests.

## Verification checklist

Before distribution, verify all of the following against a real EveJS
installation, not only a standalone mock:

- [ ] Fresh installation defaults to disabled.
- [ ] The launcher discovers one valid row after Refresh.
- [ ] Disabled Native startup emits exactly one `disabled` marker.
- [ ] Disabled startup creates no gameplay entities, timers, listeners, or state.
- [ ] Enable plus Game restart emits `running` and activates the feature.
- [ ] Disable plus Game restart returns to `disabled` and removes the feature.
- [ ] Launcher and full-stack restarts preserve configured state.
- [ ] Bad config, unsupported schemas, duplicate IDs, and malformed markers fail closed.
- [ ] Loader Vanilla/Modded behavior matches the compatibility matrix.
- [ ] Managed Docker loader enable/disable works if Docker is supported.
- [ ] Every JavaScript file passes `node --check`.
- [ ] Every manifest and config passes strict JSON parsing.
- [ ] Every EveJS-relative import resolves in every supported EveJS version.
- [ ] Installer failure restores every touched file and withdraws partial discovery.
- [ ] Upgrade preserves compatible configuration and state.
- [ ] Keep Data removal supports restoration after reinstall.
- [ ] Purge/quarantine moves only documented mod-local data.
- [ ] Tampered manifest, pointer, helper, bundle, removal inventory,
      `unins000.exe`, `unins000.dat`, or registry data yields Repair.
- [ ] Concurrent Setup/direct uninstall is excluded for the complete
      launcher-managed removal transaction.
- [ ] A zero-exit uninstaller that leaves or incorrectly restores an enrolled
      integration path is reported as failed.
- [ ] Successful removal leaves no executable integration or management enrollment.
- [ ] Existing EveJS characters, accounts, inventory, locations, GameStore, and Market data remain intact.

## Getting a mod incorporated into EveJS Launcher

No source-code registration is needed for a conforming mod ID. The practical
integration process is:

1. Choose loader or source-integrated schema v2.
2. Make disabled behavior satisfy the boundary in this guide.
3. Package the supported directory layout and strict manifest/config contract.
4. Test the complete enable, restart, attestation, disable, and restart cycle.
5. For a source-integrated mod, if Remove is wanted, implement and test the
   `inno-user-v2` enrollment, removal inventory, and mutex authorization
   handshake. The current provider does not enroll legacy loader-only mods.
6. Run the verification checklist and publish the evidence with the mod.
7. Install it into the selected EveJS root and press **Refresh**.

If a mod needs a different activation strategy, backend, status transport,
restart scope, installer technology, or hot lifecycle, the current launcher will
reject it. That requires a reviewed launcher contract change—or, preferably for
broad lifecycle needs, an upstream EveJS plugin API.

## Why an upstream EveJS plugin API would be better

An upstream API is possible and would be cleaner for future mods. A useful API
would provide stable discovery metadata, explicit `initialize` and `shutdown`
hooks, configuration ownership, dependency/version declarations, scene and
session lifecycle events, health/status reporting, and a defined data namespace.

It would reduce source patches and `Module._load` wrapping, and would let the
launcher manage one stable EveJS contract instead of knowing several loading
mechanisms.

It would not make arbitrary hot disable safe. A plugin can own timers, entities,
listeners, in-flight operations, and persisted state. Unless EveJS provides and
enforces complete teardown semantics, enable/disable should still take effect on
a Game-server restart. Restart-based lifecycle is boring, but boring is exactly
what we want around player data.

Until that upstream API exists, the current framework is a strict compatibility
bridge: real launcher infrastructure around version-coupled EveJS integration,
not a universal EveJS plugin SDK.
