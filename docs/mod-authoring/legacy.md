# Legacy loaders and schema 2

[Guide home](../MOD_AUTHORING.md) | [Upgrade to schema 3](recipes.md#upgrading-an-existing-mod)

These existing contracts remain supported in Launcher 1.0.53. A public schema-3 descriptor is optional for a loader. Keep an installed legacy package on its verified contract until its own update provides a supported migration.

## Installer-managed shared files

The legacy `inno-user-v2` removal kit declares terminal file states and hashes.
Its opaque uninstaller does not expose a per-file skip or composition operation.
A recorded target hash alone cannot reconstruct a replacement file or prove that
another mod's changes will survive removal.

When recorded contributions from another mod or unproven later edits overlap
that inventory, the launcher opens a read-only conflict review instead of
executing the uninstaller. It shows each affected path, a bounded current text
snapshot where readable, and the declared deletion or target hash. Missing
replacement contents are labelled explicitly. Binary, oversized, and unsafe
targets do not receive a text preview. Closing this review keeps the mod installed;
it is not a force-remove or merge approval. Service shutdown may already have
completed before the worker discovers the conflict.

The package author must provide a compatible removal/migration method for these
cases. Do not run an old whole-file uninstaller and then blindly restore selected
files: that can retain hooks from the removed mod or erase another mod's changes.
New shared settings should use the [contribution interface](ownership.md), which
records individual keys and can stage the remaining owners' result before commit.

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

To report its effective state, a source-integrated mod emits one complete marker
per Game-server start:

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

Launcher 1.0.53 treats these markers as per-mod diagnostics. Missing or unusable
evidence leaves the mod's effective state **Unverified**; it does not stop an
otherwise healthy Game server. The launcher allows a bounded three-second
marker window after readiness. A valid marker that disagrees with configuration
reports the observed state and a configuration/runtime mismatch. Configuration
and effective runtime state remain distinct.

This relaxation concerns status evidence only. The launched command must still
match its captured mod plan, and changes to that plan during startup are rejected.

The Game-server stdout evidence used for status is capped at 2 MiB. Unreadable or
oversized evidence cannot verify a mod's state, so keep startup output concise
and emit the status marker promptly.

The launcher owns `<evejs>/_local/.evejs-launcher-mod-runtime.json`. Mods and
installers must not create or edit that file.
