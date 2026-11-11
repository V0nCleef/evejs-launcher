# Portable Docker mods and shared login delivery — Launcher 1.0.80

`supportedBackends` is an author's support claim, not an emulator for Windows
installers. A plain Node loader can already run in Docker. A source mod, native
binary, installer or client patch needs an explicit implementation for each side.
Keep support declarations truthful; changing `native` to `docker` cannot port code.

## Where helpers run

Use manifest schema 3, `launcherApi.version: 1`, and minimum Launcher **1.0.80**
for `docker` schema 1. `docker.dataDirectory` is a stable package-owned relative
directory. `serverHelper` accepts `runtime: node`, a package-relative `.js`/`.cjs`
path and explicit lifecycle `actions`. `hostActions` lists actions performed by
the normal Windows helper. An action cannot appear in both lists. Supported
actions must also be advertised in `launcherApi.capabilities`.

Server helpers receive one JSON request on stdin (maximum 16 KiB), using
`evejs_launcher_mod_v1`. The runtime identifies `backend: docker`, `platform:
linux`, `evejsRoot: /app`, the read-only `modRoot`, persistent `dataRoot:
/var/lib/evejs`, and `modDataRoot: /var/lib/evejs/mods/<dataDirectory>`. A stable
`installationIdentity` owns stored state; `targetIdentity` binds one operation's
exact Compose configuration. Never use the changing image identity as save ownership.

Return exactly one `EVEJS_MOD_HELPER_RESULT=` JSON line with matching protocol and
request ID, `success`, `state`, `message`, and empty `restartRequired`,
`contributions`, `environment`, `arguments` for container helpers. The JSON line
is limited to 64 KiB. Helpers have a 120-second budget. They run with cleared
`NODE_OPTIONS`; they do not inherit active preload code. No host paths or arbitrary
host contributions are accepted from a container response. Keep diagnostics
bounded and free of secrets. Verify must be read-only, including on failure.

An explicit Windows `prepare_profile` helper can prepare the selected physical
client and its profile. It receives advertised host capabilities. Do not run a
Windows executable in Linux or edit an image to prepare a Windows client.

Connect-only mode never runs lifecycle helpers, builds images, creates character
maintenance containers or changes activation. It observes status, logs and rosters.

## Immutable image contributions

An optional `docker.image` can use `source-overlay-v1` for the `server` role.
Declare a package `sourceDirectory` and 1–64 `files`; each specifies `source`, an
absolute destination below `/app/server/`, and exact lowercase `baseSha256` and
`sha256`. The build verifies both existing and replacement bytes. Unknown server
versions fail before publication. Never substitute a guessed hash.

`cargo-release-v1` accepts `server` or `market`, `sourceDirectory`, a relative
Cargo `manifest`, a `binary`, `/usr/local/bin/<binary>` destination, and a pinned
`rust:<version>-bookworm` toolchain. Include Cargo.lock. The launcher builds Linux
code and verifies the copied binary in the resulting image. Installing a Windows
Market binary is not Docker support.

Source inputs are frozen and checked again before/after building: at most 2,048
files and 64 MiB, with bounded files and no hardlinks, symlinks or reparse paths.
The current contract allows **one contributing mod per service role**. Overlapping
contributors are rejected; arbitrary Dockerfiles and unreviewed merge ordering
are outside this contract. Pulling a required Cargo toolchain/dependencies can
need Internet access and disk space.

Image publication records the previous and next exact registry bytes durably
before replacement. Rollback restores the previous image decision before old
activation. Startup checks the selected base image and actual running image IDs;
a rebuilt base requires reapplication. Explicit Stop and log viewing remain
available when next-start mod assets have drifted.

## Enabled updates and recovery

Docker-only packages may declare `enabledUpdate` protocol
`evejs-mod-enabled-update-v1`, a stable compatibility ID and exact qualified
`fromVersions`, with all four server-side phases: `prepare_update`, `apply_update`,
`commit_update`, `rollback_update`. Implement backups and durable transaction
decisions; do not merely echo success. The retained old provider remains available
read-only for rollback. Preserve preferences, balances, inventories, pending jobs
and unrelated records across install, update, failed acknowledgement, restart,
Disable, Remove, Undo and Recover. Stop services when the operation needs exclusive
state; do not treat an unknown runtime as stopped.

## Shared login ABI and ordering

`src/core/client/login_delivery.cjs` is the canonical ABI v1 runtime. Vendor exact
reviewed bytes when a mod must also work outside the launcher. Call
`getRuntime(evejsRoot).register({id, source, after, requires, required, onReceipt,
onFailure})` before the first login. IDs are stable lowercase names. The same
global singleton composes compatible contributions at the compiled native signed
function boundary, preserving authentication, native return values and existing
reply observers. Do not replace another mod's handshake source with string edits.

`after` orders present providers. `requires` also fails a provider when its
prerequisite is absent or failed. At most 64 providers, 256 KiB UTF-8 per source,
and 1 MiB aggregate raw source are accepted; the encoded expression is also bounded.
Providers execute once per login in separate namespaces with `_evejs_context`
available. Optional provider failure is isolated. Required failure refuses login.
Keep a qualified legacy path when the reviewed native adapter is unavailable.

Receipts are bounded, nonce-correlated complete lines. The native codec can return
Python Unicode as an explicit `wstring`, not a JavaScript string. A provider's OK
receipt proves its initializer returned. It does **not** prove asynchronous UI,
RPC, HUD or gameplay readiness. Require a separate mod-specific acknowledgement.
Reconnect must remove stale tasks/listeners and create one fresh UI registration.

The **Sort by dependencies** preview uses manifest `requires`, `loadBefore`,
`loadAfter` and `conflicts`; unconstrained mods keep their relative order. It saves
only after review and refuses stale previews, cycles and missing requirements.
Sorting cannot repair two legacy mods rewriting the same login expression.
Declare real relationships; never add a hard-coded global list of mod names.

Qualify Native and Docker separately. Record exact EveJS/client/mod versions,
codecs, package hashes, action coverage, crash/rollback evidence and actual retail
UI/RPC proof. Synthetic signed-function tests do not constitute retail-client proof.
