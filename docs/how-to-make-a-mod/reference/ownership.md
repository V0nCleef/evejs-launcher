# Ownership, conflicts and recovery

[Guide home](../00-start-here.md) | [Helper API](helpers.md) | [Recipes](recipes.md)

The launcher records what a mod owns. A filename appearing in a package or an existing config does not grant permission to delete someone else's data.

## Configuration contributions

Each owner includes the physical EveJS root, relative mod directory and optional stable profile identity. It remains stable across ordinary mod versions. Every write to one physical file must use the same coordinator and captured storage contract.

Several mods can own distinct keys in one file. Identical same-key contributions can coexist. Different same-key values conflict unless an explicit host action establishes precedence; API 1 does not expose an override field. Removing an owner restores the next surviving contribution or that key's baseline. It does not restore an old whole-file backup over unrelated edits.

Manual edits to unrelated keys survive. If a manually changed owned key would be overwritten, removal/save stops and preserves the file. An ineffective lower layer can be removed without changing the visible value. Unknown historical ownership cannot authorize a destructive restoration.

Global and profile owners are separate. Removing profile A must not reset profile B. Disable/removal keeps user preferences inside the mod's private package/profile data directories while shared config integrations are restored. Private config bytes are retained with the reversible archived package where applicable. An explicit complete cleanup can remove recorded private contributions too.

## Shared physical clients

Two EveJS installations can point to one physical `tq` client. Client and profile contributions use the ledger and lifecycle lock under that physical client, while their owners still identify the originating EveJS root. Mod-only and EveJS configuration use the EveJS-root ledger. Never write the same physical file through both ledgers.

Resolve and validate the target your mod actually changes. Do not store the only binary rollback receipt in a replaceable package folder. A client patcher should keep durable backups/receipts beside the physical target and verify an ownership transfer before using another server installation's state.

## Transaction behavior

The host captures allowed roots and complete before-bytes. At commit it rechecks the registry, files and no-follow paths. It stages before/after material, writes a prepared journal, then atomically replaces each file and the ownership index. Related edits share one recovery unit; filesystem replacement is atomic per file, not a hardware-wide multi-file atomic operation.

An ordinary failure rolls back exact pre-operation bytes. After process loss, recovery restores only files that still match recorded before/after states. An intervening manual edit is preserved and reported. Unrelated paths can continue when recovery knows their state. Empty directories or staging material may remain; do not delete journals to make a warning disappear.

State lives under `<coordinator>/_local/launcher-mods/`, including `contributions.json`, `transactions/` and helper diagnostics. The lifecycle lock is `<coordinator>/_local/.evejs-mod-lifecycle.lock`. These are host-owned implementation files, not author-editable activation APIs.

## Binary receipt reference

A helper reply can include:

```json
{
  "base": "client",
  "path": "_local/mod-receipts/example.json",
  "state": "active",
  "schemaVersion": 1
}
```

The referenced JSON file must exist and include at least:

```json
{
  "schemaVersion": 1,
  "modIdentity": "exact request.mod.identity",
  "clientRoot": "exact request.runtime.clientRoot",
  "state": "active"
}
```

Reference bases are `client`, `mod`, `evejs`; states are `active`, `restored`, `recoverable`. For a client operation the host checks the physical client binding, mod identity, schema and state, then checks that the receipt has not changed before committing proposed keys. Extra receipt content belongs to the mod.

A real patcher's receipt should also bind its supported client build, mod version, payload hashes, originals, backups and transaction state. The mod must independently verify those details. A receipt reference alone does not prove a DLL works. The [Client Receipt example](../../../examples/mods/client-receipt-demo/README.md) deliberately demonstrates enrollment only; it installs no renderer binaries.

## Binary failure and cleanup

Record a recoverable state before the first binary replacement, keep exact original bytes, and close all handles. If a partial update cannot be safely restored, return failed/pending with a useful message and retain recovery material. Do not report active or restored before verification.

Disable/remove must complete shared cleanup before the launcher changes ownership. Keep profile preferences. Never restore `code.ccp` or another shared file from a mod's whole-file original over another owner's changes; require a compatible composition mechanism or report the conflict. Legacy DLSS5 keeps its existing verified bridge. A new public package at that exact folder routes through its adapter once, while unrelated public mods do not suppress the legacy bridge.

## Reviewing local edits during removal

When a recorded configuration key was edited outside the launcher, removal
offers a review of the current file and the proposed result. Users can keep
the local edits or restore the composition from the remaining mods. Keeping a
manual value records it as an unowned layer, so removing another contributor
later does not erase it. Private settings remain outside shared cleanup.

The review does not write files. Applying it rechecks the exact file bytes and
ownership index. A changed preview is rejected. Replacement uses the normal
transaction journal, which retains the complete pre-operation bytes for recovery.
Unknown ownership and unsupported whole-file/binary conflicts still require
their provider; the review does not invent missing baselines.
