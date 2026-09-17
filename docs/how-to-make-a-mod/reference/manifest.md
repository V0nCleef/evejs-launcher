# Manifest schema 3

[Guide home](../00-start-here.md) | [Helpers](helpers.md) | [Settings](settings.md)

The filename is `evejs-launcher.mod.json`. It is a JSON object limited to 1 MiB. Duplicate properties, non-finite numbers, unsupported fields and unsafe paths fail validation. Parsing never runs a helper.

## Complete small example

```json
{
  "schemaVersion": 3,
  "id": "hello-loader",
  "displayName": "Hello Loader",
  "version": "1.0.0",
  "description": "A small loader example.",
  "kind": "loader",
  "supportedBackends": ["native", "docker"],
  "activation": {"strategy": "loader_rename"},
  "restart": "game_server"
}
```

The package also contains exactly one recognized loader state, such as `loader.js.disabled`. The public ID can differ from its folder name. Keep IDs unique within an installation. Ownership includes the physical root and relative package path.

## Top-level fields

| Field | Required | Value |
| --- | --- | --- |
| `schemaVersion` | Yes | Integer `3` |
| `id` | Yes | Nonempty trimmed plain text, at most 128 characters |
| `displayName` | Yes | Nonempty trimmed plain text, at most 100 characters |
| `version` | Yes | Nonempty trimmed plain text, at most 64 characters |
| `description` | No | Trimmed plain text, at most 1,000 characters; default empty |
| `kind` | Yes | `loader`, `source-integrated`, `client-package`, `settings` |
| `supportedBackends` | No | Distinct `native`/`docker`; default both for loaders, Native otherwise |
| `activation` | Yes | A shape below |
| `restart` | Yes | `none`, `game_server`, `client`, `launcher` |
| `launcherApi` | No | Helper declaration below |
| `settings` | No | [Settings schema 1](settings.md) |
| `updates` | No | [GitHub release source, asset naming and configuration preservation](updates.md) |
| `compatibility` | No | `{"evejsVersions":["0.12.7.1"]}` for exact three/four-component version restrictions; omit this field or its version list when EveJS-independent; [release-sidecar rules](updates.md#declare-supported-evejs-versions) |

Schema 3 does not accept schema-2 `status`. The optional `compatibility` object has the exact EveJS-version shape above. Update-enabled packages may omit it when EveJS-independent. Document additional build compatibility in the package README and verify actual runtime/build requirements in the implementation. Public configured state is not schema-2 Game-server attestation.

## Optional dependencies and ordering

Use top-level arrays of mod IDs, for example:

```json
"requires": ["base-content"],
"loadAfter": ["mining-rules"],
"loadBefore": ["final-overlay"],
"conflicts": ["alternative-overhaul"]
```

Each array accepts up to 64 distinct IDs. References are case-insensitive and
cannot refer to the declaring mod itself. Existing mods need no such fields.
For legacy loaders, the ID is the folder name. Give public mods unique IDs:
a reference matching multiple installed folders is ambiguous and is rejected.

`requires` means the prerequisite must be installed, enabled and valid on the
selected backend. For two loaders it also places the prerequisite first.
Disabling/removing a prerequisite while a dependent is enabled is blocked with
the dependent's name; other mods are never silently disabled or removed.

`loadBefore` and `loadAfter` are optional loader ordering hints: absent or disabled
targets are ignored. They only order Node preloads; they cannot change integrated
service initialization. The resolver preserves the saved order wherever the
declared edges permit it. Native and Managed Docker consume the same resolved
server plan. Cycles are reported before startup.

`conflicts` prevents the declared pair from being enabled together, even when
only one mod declares the conflict. This is author-provided compatibility
information, not automatic detection of every runtime hook interaction.

## Activation

| Kind | Allowed strategy | Launcher-owned change |
| --- | --- | --- |
| `loader` | `loader_rename` | Recognized loader filename |
| `source-integrated` | `json_boolean`, `package` | Declared Boolean, or owned overlay package state after helper preparation |
| `client-package` | `client_package`, `package` | Owned package state, after required helper cleanup |
| `settings` | `json_boolean`, `package` | Declared Boolean or owned package state |

For a package or loader, the entire object is `{"strategy":"..."}`. A Boolean declaration uses:

```json
{
  "strategy": "json_boolean",
  "configPath": "config/mods/example.json",
  "property": ["features", "enabled"],
  "allowedConfigSchemaVersions": [1, 2]
}
```

`configPath` is a root-relative `.json` file and cannot target the launcher registry or shared `_local/GameStore` data. Use a mod configuration file for the toggle; changing game data belongs to the mod's runtime/lifecycle implementation. `property` is one key string or 1–16 nested key strings. Its existing value must be a Boolean. Optional `allowedConfigSchemaVersions` contains distinct positive integers up to 65,535; when present, the config must contain a supported schema version. Unrelated values survive activation changes.

A source-integrated disabled gate must run before gameplay effects. A flag cannot undo repositories, entities or timers initialized before that check.

A self-contained source-overlay package may instead use `{"strategy":"package"}`.
Declare `install`, `prepare_disable` and `prepare_remove`. It can then be imported
from a folder or ZIP, starts disabled and needs no external activation config.
Enabling runs its installer before recording enabled state; disabling/removing
runs cleanup and restores host-owned contributions. Undo restores it disabled.
See the [Source Overlay example](../../../examples/mods/source-overlay-demo/README.md).
Legacy external source patches retain their JSON/provider workflow.

## Helper declaration

```json
{
  "version": 1,
  "minLauncherVersion": "1.0.53",
  "helper": {"runtime": "node", "path": "launcher/helper.js"},
  "capabilities": ["prepare_profile"]
}
```

Only `version` is required in `launcherApi`. Minimum launcher version defaults to `1.0.53` and uses three numeric components. `helper` contains exactly `runtime` and `path`. Runtimes are `node`, `powershell`, `executable`. Executable capabilities require an existing helper file within the package. Capabilities are distinct names from the [action table](helpers.md).

An enabled client package needs `verify` before client launch. `prepare_profile` is optional. Normal new mod versions do not require launcher source edits or publisher-specific source hashes; the stable API and the helper's real target checks define compatibility.

## Paths

Use relative paths with forward slashes. Empty components, `.`, `..`, absolute paths, device names, wildcards, alternate data streams, trailing spaces/periods, symlinks and junctions fail validation. The helper must remain in its captured package. A descriptor or helper changed during an action invalidates the result.

Unsupported fields are errors, not extension points.
