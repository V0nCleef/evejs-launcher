# Legacy installer-managed removal

[Guide home](../MOD_AUTHORING.md) | [Current ownership and recovery](ownership.md)

This is the existing schema-2 installer enrollment contract. Public schema-3 packages use the helper and receipt protocol instead. Do not mix the two ownership models for one installation.

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
