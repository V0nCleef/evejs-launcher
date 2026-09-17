# Practical recipes

[Guide home](../00-start-here.md) | [Examples](examples.md)

## A small server loader

Start with [Hello Loader](../../../examples/mods/hello-loader/README.md). Keep the entry preload small and put implementation in adjacent modules. Disabled code must not load. Support exactly one recognized loader state; do not ship an active file and a disabled backup together. Wrap the specific module/export shape you actually support, preserve original behavior on isolated mod failures, and document EveJS dependencies.

Native Normal startup uses the configured loaders. An explicit Vanilla start omits loader preloads. Managed Docker uses its reviewed Compose override. Discovery/configured state and runtime selection are different facts; verify the actual restart path. See the [legacy loading reference](legacy.md) for wrapper and status patterns.

## A settings-only package

Use `kind: "settings"` and `activation: {"strategy":"package"}`. A generated form needs no helper merely to save values. Add `prepare_profile` when launching needs derived profile keys or environment values. Store per-profile data in `profile`, not in the shared client or package directory.

The [Profile Options example](../../../examples/mods/profile-options/README.md) reads typed values from its request and returns proposals. It does not directly rewrite the destination, import the launcher or manipulate a database.

## A client renderer or other binary package

Use `kind: "client-package"` with a public helper. Keep installation/removal global to the physical copied client. Expose profile preferences separately. Implement install, verify, cleanup and recovery against the exact build/files the package changes. Keep binary receipts/backups near the physical target and return a bound reference.

On client launch the host runs verify, then optional profile preparation. Do not implicitly install binaries from `prepare_profile`, especially while another character's client is already running. `RESHADE_BASE_PATH_OVERRIDE` can point to the captured private mod-data directory without making all renderer settings global. The host completes key contributions before client spawn.

Use the [Client Receipt example](../../../examples/mods/client-receipt-demo/README.md) to learn the request/reply boundary. Its demonstration receipt is not a binary patcher; add your real validation, transaction and rollback implementation before claiming rendering support.

## Upgrading an existing mod

Existing loader packages need no descriptor migration. To add public settings/metadata, preserve the loader and add schema 3. Keep the package identity and private config paths stable so ownership/preferences survive new versions.

Schema-2 source integration can remain on its existing contract. It has a fixed enabled flag and Game stdout status protocol. Do not mechanically change `schemaVersion: 2` to `3`: schema 3 has different fields and does not reinterpret legacy status declarations. Keep the old receipt/uninstaller path until its own supported upgrade transfers ownership safely.

Legacy DLSS5 0.5.7 and other supported older packages retain the frozen bridge. A new public DLSS5 package owns its own adapter contract and real client build checks. Do not modify the legacy launcher's approved hashes to force acceptance of a new binary package.

For client-only mods, do not add an EveJS version dependency unless some actual code/config interaction needs it. Test the client build and renderer contract; for server or loader integration also test the EveJS source/API versions it uses. Keep source fingerprints as compatibility evidence, not a claim of publisher trust.

## A useful release check

Validate the descriptor and form with the actual launcher. In a disposable tree, run enable/verify/prepare/disable/remove/recover, reopen settings, change a preference, and launch two profiles with different values. Verify repeated prepare is a byte-preserving no-op. Add another mod editing a distinct key and then a conflicting key. Interrupt one transaction and confirm manual edits/other owners survive recovery.

Publish only capabilities and dependencies you actually tested. Launcher helper readiness is not a live rendering or gameplay acceptance result.
