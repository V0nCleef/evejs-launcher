# EveJS Launcher V1

## Changelog

## v1.0.52 — 2026-09-05

### DLSS5 persisted-toggle hotfix

- Trust the matched DLSS5 0.5.7 package manager while retaining the exact 0.5.6
  manager and receipt contracts for recovery and uninstall.
- Bind each client-scoped package version to its reviewed manager hash, so an
  older trusted manager cannot be relabelled as the newer hotfix.
- DLSS5 0.5.7 accepts the documented persisted `NeuralUplift` values `0` and
  `1` during read-only preparation. Missing, duplicate, or invalid values and
  changes to security-critical ReShade keys still fail closed.
- The client renderer payload, downloads, receipt schema, state location, and
  launcher behavior for users without the separate DLSS5 package are unchanged.

## v1.0.51 — 2026-09-05

### DLSS5 survives EveJS upgrades

- DLSS5 schema-3 packages now declare `evejsVersionPolicy: any`. The launcher
  still validates a real `eve.js` root, the exact local client build 3396210,
  the reviewed renderer payload, and a trusted manager; an unrelated EveJS
  server version is no longer treated as a renderer compatibility boundary.
- DLSS5 0.5.6 state, backups, cache, and the active receipt now live beside the
  physical copied client under `_evejs/dlss5/install`. Copying the current
  `mods/DLSS5` package into a new EveJS root is therefore enough for its trusted
  manager to verify and safely move that same client between roots.
- Client-scoped schema-5 receipts bind the active EveJS root, physical `tq`
  directory, config, backups, and exact payload. A selected root with known
  DLSS5 bytes but neither its package nor a matching valid receipt now stops
  before client launch instead of guessing ownership.
- Historical schema-1/schema-2 packages and schema-4 root-local receipts retain
  their exact 0.12.6/0.12.7/0.12.7.1 contracts for verified launch, rollback,
  and uninstall. They are not silently relabelled as client-scoped state.
- Launcher uninstall now understands both the new schema-5 client state and
  the retained schema-4 rollback state, while keeping package archival
  recoverable and refusing changed client/config/backup bytes.

## v1.0.50 — release candidate

### DLSS5 0.5.5 package support

- Local EveJS 0.12.7.1 compatibility recognizes the matching DLSS5 package and
  retains the reviewed 0.5.5 rollback and standalone-receipt contracts.

- Recognizes the optional DLSS5 mod automatically when its folder is placed in
  the selected EveJS root's `mods` directory. First client launch prepares its
  verified dependencies; starting the server alone does not install it.
- Adds an Uninstall action with verified rollback and a recoverable package
  archive. Backups and audit receipts are retained; uninstall completion is
  reported once.
- Supports the separately installed standalone package without requiring a Mods
  entry or routing client launches through `Play.bat`.
- Accepts the reviewed 0.5.5 package while preserving older development-package
  trust records for existing installations and rollback.
- Clients without DLSS5 retain the normal launch path. No global DX12 setting
  or DLSS5 installation is applied merely by updating the launcher.

### DLSS5 launcher routing

- The launcher now recognizes the managed `EVEJS_DLSS5=on` and
  `TRINITYPLATFORM=dx12` assignments in the selected EveJS root and carries
  them into direct client launches when the configured copied-client path is
  an exact match.
- Installations without the DLSS5 marker retain the previous client launch
  environment—including any user-supplied ReShade base-path override; DX12 is
  not enabled globally or guessed from loose DLL files.
- Each launcher character profile now receives its own ReShade configuration,
  transition log, and Neural Rendering preference. Multiple clients can share
  the installed DLSS binaries without fighting over `ReShade.ini`, and a
  user's F6 choice affects the foreground client only. With the current DLSS5
  package, switching from another upscaler into DLSS enables NR; switching away
  disables it. F6 can override NR while DLSS remains selected.
- Marked installations fail closed when the ReShade proxy or RenoDX add-on is
  missing, or when a shared ReShade `BasePath` would defeat profile isolation.
- Per-account client junctions are checked before every launch and safely
  rebound when Settings points the launcher at a different copied client.
  A real directory is never replaced, and a failed rebind restores the prior
  junction target when possible.
- Dangling profile junctions left behind when an older copied client is moved
  or deleted are now recognized as existing links, rebound on launch, and
  removable during profile deletion instead of failing with "file already
  exists".
- The launcher MOD contract now accepts an explicit schema-2 compatibility
  list for EveJS 0.12.6 and 0.12.7. It also validates the selected root's real
  `package.json`, so a package cannot claim compatibility with a different
  EveJS version or crash discovery with malformed version metadata.
- The windowed launcher now gives the trusted DLSS5 manager an explicit null
  input handle. This prevents Windows error 50 after Native service console
  handling leaves no supported standard-input handle to inherit.
- The reviewed `0.5.5` PowerShell manager is an explicit launcher trust
  anchor. That manager in turn pins its download helper and raw payload
  manifest; package metadata cannot redirect execution or silently replace its
  verified sources.
- First-time DLSS5 preparation now has a one-hour outer budget covering the
  manager's three independently bounded downloads, verification, extraction,
  and local client-guard generation. Existing installations retain the
  manager's read-only fast path and do not redownload or rewrite mapped DLLs.
- Preparation uses the launcher's existing Windows Job Object containment:
  PowerShell is assigned before it starts, and timeout/launcher-exit cleanup
  includes its local guard-builder descendants rather than killing only the
  parent. The game client is not part of this preparation job.
- A completed standalone `0.5.5` DLSS5 installation works without a
  `mods/DLSS5` folder. Direct launch verifies its receipt, selected roots,
  launcher-pinned installed payload hashes, unchanged executable, and profile
  isolation settings offline. This path performs no install, repair, download,
  or package-script execution. Standalone installations do not become MOD rows.

The current candidate preserves the previously user-tested renderer. New final
package identity, launcher routing and packaging still require exact-artifact
manual acceptance. This is not a publication or blanket compatibility claim.

## v1.0.49 — 2026-08-31

### Fixed
- **Docker stays available after Windows line-ending rewrites** — the launcher now accepts its exact managed Compose mod override when external Windows tools change only LF line endings to CRLF. Monitoring, Home status, character roster, lifecycle controls, and Tool Deck no longer fail because of that harmless formatting change.
- Thanks to Darius Tumas ([@Tokeiito](https://github.com/Tokeiito)) for reproducing, diagnosing, testing, and contributing the fix. 🫡

### Safety and compatibility
- Only CRLF-to-LF equivalence is allowed at the final exact-renderer comparison. Extra Compose content, changed preload values, invalid UTF-8, BOMs, truncation, and all other tested line separators remain rejected.
- This release changes only validation of the launcher-owned Docker mod override. It does not rewrite the primary Compose file, change Native launch behavior, or alter EveJS databases, characters, accounts, items, Market data, profiles, or installed mods.

### Verification
- The complete isolated release suite passed **2,017 tests** with 6 skipped. The focused Docker-mod suite passed **28 tests** with 2 skipped; an exact v1.0.48 parent/PR comparison reproduced the CRLF failure before the fix and passed it afterward, while an expanded mutation matrix continued rejecting semantic changes and malformed documents.

## v1.0.48 — 2026-08-30

### Added
- **Complete multilingual launcher** — English, Simplified Chinese, Japanese, Korean, French, German, Dutch, and Russian now cover launcher pages, controls, dialogs, setup wizard, updater, accessibility text, and first-run errors. The launcher selects a supported Windows language automatically on first use, falls back to English, and exposes language selectors in both the wizard and footer.
- **Safer, friendlier mod discovery** — the Mods page remains available when `<EveJS>\\mods` does not exist, can create or open that exact folder, explains how to install a mod, and links mod authors to the public authoring guide.
- **Real music visualization and navigation** — the title bar now includes previous and next controls, randomizes the initial track when a library contains multiple entries, and drives a responsive 16-band spectrum from the decoded audio actually being played.

### Changed
- **One approved bundled track** — v1.0.48 ships only the original **Celestial Transit** composition. Deep Signal Ambience has been removed; user-selected local music remains supported and is referenced in place rather than copied into the launcher.
- **Language selector placement** — the compact flag and native language name now live in the launcher footer alongside server status, while the setup wizard presents the same choice before configuration is complete.

### Fixed
- **Updates preserve neighboring files** — replacement owns only `EveJS-Launcher-V1.exe` and `_internal`. Other files and folders beside the launcher, including an EveJS installation placed there, are left untouched even on the first update from v1.0.47 to v1.0.48.
- **Unicode Windows identity and paths** — launcher startup, path handling, process launch, and helper boundaries accept non-ASCII Windows usernames, computer names, and folders, including Chinese, Japanese, Korean, and Cyrillic characters.
- **Client Code Grabber window behavior** — the window opens within the usable screen area, and clicking its focused taskbar button minimizes it normally instead of leaving its controls above the display.

### Safety and compatibility
- The update hardening is constrained to launcher-owned files and has an explicit v1.0.47-to-v1.0.48 regression test. It does not delete, migrate, or rewrite EveJS databases, characters, accounts, profiles, items, Market data, mods, or arbitrary neighboring files.
- Bundled playback uses the existing Qt media stack. The visualizer does not launch a second decoder, and mute, stop, playback failure, and track completion clear the spectrum immediately.

### Verification
- **1,917 automated tests passed** with 6 skipped, covering the complete launcher suite. A focused 350-test release boundary additionally exercised the one-track package contract, native Qt audio decode and spectrum reset, updater preservation, Unicode discovery, all translation catalogs, first-run wizard, Mods folder actions, and Windows tool-window behavior.
- Tests and frozen-launcher smoke checks use disposable application-data directories so they do not read, rewrite, or depend on the operator's live launcher configuration.

## v1.0.47 — 2026-08-30

### Fixed
- **Native services no longer depend on launcher log draining** — Game and Market output is written directly to durable log files instead of crossing launcher-owned stdout pipes. A frozen or force-closed launcher can therefore no longer backpressure Node.js until compatibility handshakes and remote calls stall.
- **Client-window checks no longer freeze the interface** — potentially blocking Win32 window discovery for Launch All runs away from the Qt GUI thread, with only one probe in flight and stale results ignored after cancellation or timeout.
- **Live clients remain tracked through temporary window loss** — periodic tracking now uses process exit as its liveness boundary and no longer performs synchronous window enumeration or retires a running client merely because its top-level window temporarily disappears.
- **Large console bursts stay bounded** — the visible console tails only the newest 100 KB per refresh and inserts at most 2,000 lines in one Qt operation, preventing multi-second interface stalls from line-by-line insertion and cleanup.

### Safety and compatibility
- This release changes process supervision, window-readiness observation, log transport, and console rendering only. It does not alter EveJS databases, characters, accounts, profiles, items, Industry jobs, Market data, or mod configuration.
- Native mod launch evidence remains isolated per launch while Game output continues independently of launcher health.

### Verification
- **1,661 automated tests passed** with 4 skipped, including delayed child output after the launcher's file handle closes, blocked Win32 probes while Qt remains responsive, stale-result suppression after timeout and deletion, live-process tracking through window loss, exact mod-attestation boundaries, and bounded large console bursts.
- Manual testing of the exact packaged build confirmed that all configured clients launched successfully without a launcher freeze or stalled final logins.

## v1.0.46 — 2026-08-28

### Fixed
- **Play.bat resource-cache parity** — every EVE client now launches against the verified `ResFiles` cache and resource index belonging to the selected copied client. Inherited cache paths are cleared before launch, fixing the launcher-only failure where the normal Industry blueprint list and controls differed from a client started through `Play.bat`.
- **Stable Launch All sequencing** — the next queued client now waits non-blockingly until the exact launched process owns a usable game window, then applies the configured stagger. If a client exits early or never opens a window, the remaining queue stops instead of adding more startup load; cancelling still leaves already-started clients alone.
- **Exact client-window restoration** — window focus and restore now use the launched process ID rather than the first title containing “EVE”, so an older client, the launcher, or another similarly named window cannot steal the restoration attempt. The watcher also stops when that exact process exits.
- **Client setup no longer hangs on write-restricted folders** — profile endpoint updates avoid unchanged writes, and Windows permission failures now return promptly with an actionable error instead of entering `tempfile`'s extremely long collision-retry path and leaving a card stuck on **LAUNCHING**.
- **Native endpoint consistency** — Game startup, monitoring, service controls, client arguments, and data-safety guards now use the configured Native game port consistently. The launcher validates the game port and proxy origin, waits for both Game TCP and the proxy health endpoint before changing a profile, and rechecks both immediately before spawning EVE.
- **Safer runtime-setting changes** — changing the Native root, game port, proxy URL, or backend is refused while affected Native services are active, preventing the saved configuration from drifting away from the running stack.
- **Better client-exit diagnostics** — `launcher.log` now records account-neutral PID, uptime, exit-code, and window-retirement evidence when a tracked client disappears.

### Safety and compatibility
- This release changes launcher validation, environment construction, process sequencing, and diagnostics only. It does not repair, move, delete, or rewrite GameStore items, blueprints, Industry jobs, characters, accounts, profiles, or Market data. In particular, pre-existing ghost corporation blueprints that report **“That facility could not be found”** are a separate EveJS database-custody issue and are not repaired by this launcher release.
- Launch All remains cancellable and keeps the interface responsive while waiting. A readiness timeout leaves the already-spawned client alone, reports why the sequence stopped, and does not start the remaining clients.
- The Windows package now includes only explicitly reviewed public documentation, preventing unrelated local investigation notes from being swept into a release by a wildcard.

### Verification
- **1,655 automated tests passed** with 4 skipped, covering copied-client resource validation, Play.bat environment parity, endpoint readiness and last-moment rechecks, custom Native ports, active-service settings guards, atomic-write permission failures, exact-PID window handling, queue cancellation and timeout behavior, stale queue signals, and account-neutral process diagnostics.
- Manual source testing confirmed the Industry blueprint list remains usable through the launcher and that Launch All waits between real client windows without freezing the launcher. The release tree also passed syntax compilation, Foundation smoke, dependency checks, whitespace checks, and packaged-artifact verification.

## v1.0.45 — 2026-08-22

### Added
- **Launcher-managed mods** — schema-v2 manifests let the launcher discover supported mods, show them under **Mods**, and enable or disable them without uninstalling their files or deleting their state.
- **Native and Docker activation contracts** — Native loader/source-integrated mods and Managed Docker loader mods now share one fail-closed lifecycle, including durable activation intent, cross-process locking, restart requirements, and runtime verification.
- **Temp NPC 0.4.2 support** — Temp NPC is recognized as a Native mod and can be toggled from the launcher. When disabled, its bootstrap exits before loading EveJS or mod state, so it is functionally absent from the running game.
- **Launcher-native mod removal** — compatible source-integrated mod installers can use registration schema 2 and the `inno-user-v2` provider to enroll a separately verified removal kit and post-removal inventory, allowing an installed mod to be removed directly from its row under **Mods**.
- **Explicit saved-data choice** — removal defaults to keeping mod data, with an advanced quarantine option only when the mod's installer supports it. Shared EveJS and GameStore database records are never presented as mod-owned data.
- **Complete mod-author contract** — `docs/MOD_AUTHORING.md` documents loader and source-integrated layouts, the strict schema-v2 manifest, disabled-state boundaries, runtime attestation, installer transactions, launcher-managed removal, upgrade rules, verification, and the distinction between the launcher framework and a future upstream EveJS plugin API.

### Safety and compatibility
- Unknown, malformed, or unsupported mod contracts are never guessed at and remain unavailable. A successful toggle changes configured state; it is not reported as runtime-effective until launch-time verification confirms the exact expected state. Source-integrated mods provide that evidence through their Game-server attestation markers. Mismatches trigger a corrective stop instead of leaving an ambiguous server running.
- Mod files and persistent state remain installed while disabled. GameStore, Market, character, account, item, and profile data are not purged or migrated by mod toggles.
- Native startup attestation reads at most 2 MiB of stable Game-server stdout evidence. Oversized, malformed, duplicate, stale-PID, missing, or state-mismatched evidence fails closed.
- Docker activation uses an exact launcher-rendered override and a durable prior/desired-hash transaction marker. Ordinary lifecycle attachment fails closed after an interrupted Apply; explicitly applying the same visible selection can resume the exact stranded transaction. Sensitive environment values stay outside the structured status parser.
- The launcher stops Game and Market processes it owns before removal, refuses to alter live files beneath externally started services, keeps the interface responsive, and leaves the stack stopped afterward.
- Removal authority is not accepted from the runtime mod manifest. The launcher validates a fixed installer registration, exact EveJS root, package/helper/current-journal/removal-inventory/`unins000.exe`/`unins000.dat` hashes, matching Windows uninstall metadata, and safe non-reparse paths. After a zero-exit uninstall it verifies every enrolled integration path is absent or restored to its exact original hash, then proves the journal, registrations, and complete uninstall kit are gone.
- Registry enrollment is checked explicitly across 32-bit and 64-bit Windows views. Ambiguous or tampered registrations fail closed and appear as needing repair instead of executing an untrusted command.
- The v2 installer provider holds a per-AppId Windows mutex across Setup/removal and uses a one-use launcher authorization mutex for the verified uninstaller. The child also requires exact three-way agreement between the launcher's validated root and both provider registry roots before changing files. This closes executable, metadata, and target-root validation-to-execution swap windows without allowing concurrent compatible operations.
- The v2 installer provider supports one installation of a given mod per Windows user. Its Setup must refuse a second EveJS root until the first installation is removed.
- Managed removal v2 requires a source-integrated schema-v2 manifest and package version. Legacy loader-only mods remain toggleable but cannot enroll this removal provider.

### Changed
- Mod rows now distinguish launcher-managed removal from externally installed mods. An external mod remains toggleable when its activation contract is supported; running its matching compatible Setup once adds the **Remove** action.
- Mod-management actions now use scoped Deep Signal styling: a restrained destructive treatment for **Remove**, amber for **Repair**, and a neutral read-only state for **External**, including dedicated hover, pressed, focus, disabled, and cursor behavior.
- Windows **Installed apps** remains a fallback for compatible Setup packages, not the normal EveJS workflow.

### Fixed
- **Client launch certificate discovery** — the launcher and EveJS v0.12.6 certificate helper now inspect only the four documented `bin64`/`bin` certificate-bundle locations instead of recursively walking the entire copied EVE client. Healthy bundles bypass PowerShell bundle discovery, first-time repairs remain reverified, and profile junction creation now has an explicit timeout.
- **Accurate client launch state** — each tracked process now owns its own time-based launch grace period, with an exact GUI refresh at expiry. A different EVE window can no longer leave another character card stuck on **LAUNCHING**.
- **Exited clients no longer remain stuck as running** — status reads can no longer consume the process-exit event before the UI refresh sees it. Exact-PID window tracking retires lingering EVE processes after their visible client closes, and the selected character panel now uses the same `RUNNING`, `WAITING`, and ready state as its card.
- **Launch-worker recovery and lifecycle gating** — thread-start exceptions and workers that exit without a terminal result release their exact pending request and queue slot. Native client spawning is also blocked while a server or mod lifecycle owns the runtime, closing the race with managed mod removal.

### Verification
- **1,570 automated launcher tests passed** with 4 skipped. Focused Windows PowerShell 5.1 probes verified bounded certificate discovery against both a decoy fixture and the live copied client; the live lookup returned its two exact `bin64` bundles in 10 ms. Character lifecycle regressions cover status-read ordering, exact-PID window ownership, lingering processes, multi-client isolation, and card/detail-panel agreement. Mod regressions include strict activation/runtime parsing, transactional Docker restart recovery, exact managed-removal inventory proof, worker-failure recovery, external-service race refusal, and launcher-to-uninstaller root binding.
- Temp NPC completed **193 automated tests** and its package validation, including a real isolated Setup/generated-uninstaller cycle and a two-root registry-drift refusal with byte-identical targets. A live Native disable/enable cycle reported exact disabled and running attestations, stopped gracefully both times, and finished enabled with no pending activation intent. A real client check then confirmed NPCs spawn while enabled and do not spawn after disabling the mod and restarting the stack.

## v1.0.44 — 2026-08-21

### Fixed
- **Chat after switching EveJS installations** — before spawning EVE, the launcher now runs the selected installation's official `Install-EvEJSCerts.ps1` trust preparation in the existing background launch worker. EveJS v0.12.6 gives each installation its own local CA; this keeps Windows `CurrentUser\Root` and every copied-client `cacert.pem` bundle synchronized with the active root, preventing the repeating `localhost:5222` chat connection dialog after moving between a fresh install and an existing save.
- **Actionable Launch All failures** — queued client launches now retain and display the first worker failure, so certificate, client-path, and PowerShell errors are no longer reduced to only “N failed.”

### Safety and compatibility
- The launcher delegates certificate creation, validation, stale-CA removal, root-store installation, and client-bundle updates to the selected EveJS root's own official helper. It does not accumulate historical EveJS CAs or modify GameStore, Market, account, character, item, or profile data.
- A real CA rotation is refused while any EVE client is running. Same-root multibox launches remain supported because the official health check is idempotent. A per-user Windows named mutex keeps certificate preparation and EVE process creation atomic across multiple launcher instances.
- Older EveJS layouts without `tools/ClientSETUP/scripts/Install-EvEJSCerts.ps1` retain their established launch path unchanged.

### Verification
- **1,254 automated tests passed**, including selected-root trust preparation, hidden PowerShell and isolated-standard-handle contracts, post-install bundle verification, failure-before-spawn behavior, same-root live-client behavior, cross-process launch serialization, older-layout fallback, queued error visibility, and the full existing Native, Docker, fresh-v0.12.6, legacy-save, character, profile, and service-lifecycle suites.
- The reported fresh-to-existing-save switch was reproduced with a healthy XMPP listener on `127.0.0.1:5222`: Windows and both EVE client bundles trusted the fresh root while the selected copy presented its different CA. Running the copy's official setup changed its certificate check from orange to green, after which the existing character connected to chat. The patched source then completed the exact official trust helper successfully against that same copied root.

## v1.0.43 — 2026-08-21

### Fixed
- **Native restart after graceful shutdown** — Game, Market, the EVE-client safety probe, forced service cleanup, and profile junction creation no longer inherit an invalid Windows standard-input handle after the launcher sends Game a graceful Ctrl+Break. This fixes the misleading “Close every EVE client” character-creation block and the later `[WinError 6] The handle is invalid` Game/Market startup failures.

### Safety and compatibility
- Existing GameStore, Market, character, account, profile, and client data are not migrated, rebuilt, or rewritten by this fix. Healthy `better-sqlite3` installations—including older saves and earlier supported EveJS layouts—still pass the existing runtime probe and are left in place.
- The EVE-client check remains fail-closed for genuine Windows process-query failures; only its dependency on the launcher's inherited console handle was removed.

### Verification
- **1,243 automated tests passed**, including deterministic consoleless and invalid-standard-handle Windows regressions, explicit isolated-handle assertions for Game, Market, client detection, and forced cleanup, plus all existing Native, Docker, fresh-v0.12.6, and legacy-layout tests.
- A pristine v0.12.6 archive completed official Native setup under npm 12, had its blocked `better-sqlite3` binding repaired, booted and stopped cleanly three times, created a real character, and booted successfully afterward. An independent clone of a migrated v0.12.2-era save then completed three full Market + modded Game cycles around real character creation; every Game shutdown was graceful, final SQLite `quick_check` passed, and the original copy's Game/Market database and WAL hashes remained unchanged.
- A separately isolated pristine v0.12.6 Compose project initialized GameStore, built the official 890,192-order Market seed, reached healthy, created and verified a real character through the launcher's managed Docker transaction, and completed two launcher-managed stop/start cycles around that transaction. Its unique containers, image, volumes, network, ports, backups, and extracted files were removed afterward.

## v1.0.42 — 2026-08-21

### Fixed
- **Post-character service restoration** — Native character creation can no longer leave Game unavailable solely because the optional Market process fails during automatic restoration. The launcher still reports the Market failure, but continues Game startup and any waiting client launch once Game itself is ready.
- **Actionable Market restart diagnostics** — Market startup attempts now append timestamped command and PID markers instead of truncating the console log twice. Retry evidence is preserved, and Native lifecycle failures are also written to `launcher.log` instead of existing only in a temporary dialog.
- **Fresh EveJS v0.12.6 Docker character creation** — Managed Compose character creation now recognizes the exact reviewed v0.12.6 image in addition to v0.12.5. It verifies v0.12.6's starter-skill records, lazily initialized mail and notification state, and mailbox message identifiers before committing, so a freshly downloaded stack can create a character and restart safely.

### Safety and compatibility
- The fallback changes service sequencing only. It does not replace, reset, migrate, or rewrite GameStore, Market, character, account, item, profile, or older-save data.
- Market remains optional only when Game was also requested. A standalone Market start retains strict failure behavior, and the prior default remains available to non-launcher callers.
- Docker character creation remains exact-version and exact-source gated. The original v0.12.5 hashes and mutation contract are unchanged; v0.12.6 has its own immutable 14-source contract, canonical starter-skill metadata and totals, and strict rollback verification. Modified or unknown images still fail closed.

### Verification
- **1,240 automated tests passed**, including optional-Market failure continuation, strict standalone-Market behavior, post-character callback restoration, durable retry diagnostics, fresh v0.12.6 setup, older Native save migration, profile repair, graceful shutdown, exact v0.12.5/v0.12.6 Docker image contracts, canonical v0.12.6 starter skills, rollback, and adjacent Docker lifecycle regressions.
- An untouched EveJS v0.12.6 archive completed its official Native setup, reproduced the blocked `better-sqlite3` binding, was repaired by the launcher, booted twice, created a real account/character and rookie ship between boots, and stopped cleanly both times. The final GameStore passed SQLite quick/integrity checks with an empty persistence outbox and every ownership lease released.
- A separately extracted untouched v0.12.6 Compose project built from scratch with isolated resource names, initialized GameStore, and seeded 890,192 Market orders. A real invalid-name transaction mutated then restored all nine scoped logical tables exactly; a valid transaction created an account, character, canonical starter skills, welcome mail/notification state, and verified Ibis. Both post-transaction stack restarts reached healthy, final SQLite quick/integrity checks and retained-backup digests passed, the persistence outbox and owner leases were clean, and the stock Market doctor validated all 890,192 open orders plus 580,560 history rows.

## v1.0.41 — 2026-08-21

### Fixed
- **Fresh EveJS v0.12.6 Native setup** — the setup wizard now accepts a completed static GameStore (`manifest.json` plus generated data) before the first server run creates `gamestore.sqlite`. Existing initialized Native installations remain supported, while incomplete roots still fail validation.
- **Fresh EveJS v0.12.6 Node dependencies** — Game startup now proves that `better-sqlite3` can create and query a real in-memory database instead of trusting the presence of `node_modules`. If a fresh npm install omitted the native binding, the launcher reinstalls the locked dependencies with lifecycle scripts disabled, approves only the exact pinned `better-sqlite3` package on npm 12+, performs its targeted rebuild, and verifies the runtime again before launching EveJS. npm 11 and earlier retain a compatible targeted-rebuild path.
- **Legacy Native save handoff** — direct launcher startup now honors EveJS v0.12.6's official `_local/newDatabase` → `_local/gameStore` migration before selecting the runtime data path. It never creates an empty destination ahead of migration, and an ambiguous root containing both save layouts stops with an actionable error instead of silently choosing one.
- **Optional Native Market startup** — a fresh v0.12.6 install without a built Market seed no longer blocks Game startup or client auto-start. The launcher validates the configured SQLite seed, explains how to build it from Tools, and gives first-time Rust compilation a separate five-minute readiness budget.
- **Manual-login profile rendering** — numeric account names are serialized as YAML strings, and incomplete or malformed `core_public__.yaml` sections are repaired from the complete launcher template before EVE starts. Existing valid values are retained and the original document is backed up before structural repair.
- **Graceful Native Game shutdown** — the launcher-owned Game process receives Ctrl+Break through an isolated hidden console so Node can finish its shutdown hooks and persistence flushes. The launcher waits for the full bounded cleanup window and clearly reports any forced or non-clean exit; Market retains its normal bounded terminate path.

### Safety and compatibility
- Working existing installations pass the native SQLite probe and skip npm repair entirely. Dependency repair is confined to the selected server's locked Node dependencies and pinned install-script policy; it does not delete, seed, replace, or migrate `_local`, `gamestore.sqlite`, Market data, characters, profiles, or EVE client settings.
- An installed but broken `better-sqlite3` is rebuilt in place, preserving additional mod dependencies that are not in the stock lockfile. A full script-disabled `npm ci` is reserved for a missing or unreadable required package.
- Automatic repair never enables arbitrary dependency scripts, preserves an explicit `better-sqlite3` denial, has bounded command timeouts with exact process-tree cleanup, and keeps the bootstrap output in the Game Console. Fatal Node reports now have their required directory before Game startup.
- Profile repair never copies, deletes, or rewrites `core_user__.dat`, `core_char__.dat`, or browser cache state. Required YAML defaults are merged without overwriting valid account-specific settings.
- Fresh-root validation still requires the Native server entrypoint, local certificate, client configuration script, static-data manifest, and populated data tree; `compose.yaml` alone is not accepted as a Native installation.
- Forced shutdown remains exact-process scoped and is used only after the graceful request fails or exceeds its bounded timeout.

### Verification
- **1,208 automated tests passed**, including the real setup-wizard state for a pre-first-run v0.12.6 root, npm 12 pinned-script repair, older-npm fallback, healthy-install bypass, in-place mod-dependency preservation, explicit-denial preservation, dependency timeouts, legacy-save migration and ambiguous-layout refusal, missing/corrupt/default-path Market seeds, Game/client continuation without optional Market, slow first-time Market compilation, numeric username parsing, malformed-profile repair and backup, Windows Ctrl+Break delivery to a hidden Node process, graceful shutdown timing, and adjacent Native/Docker regressions.
- A clean EveJS v0.12.6 installation was repaired on Node 24, started to its live TCP endpoint, created a 115-table GameStore that passed SQLite integrity checking, completed its persistence flush, and exited cleanly through the launcher's graceful shutdown path.

## v1.0.40 — 2026-08-16

### Fixed
- **Docker character roster loading** — an explicitly retired character with a cleared account reference no longer invalidates the exported roster. Docker now matches the Native reader by excluding retired records while retaining valid accounts and characters.
- **Modded container export decoding** — preload banners, Node warnings, and unrelated JSON output without a player collection no longer make the Characters page appear empty.
- **Visible data-load failures** — an unreadable roster now reports `DATA UNAVAILABLE` with a safe reason instead of looking like a valid game store containing zero characters. The warning clears immediately when the selected root, backend, project, or observed Docker target changes.

### Safety and compatibility
- Only records with an explicitly cleared account reference are treated as retired. Malformed active records, duplicate character IDs, and conflicting account metadata continue to fail closed.
- Export discovery is bounded and accepts only a document with the expected player collection. Missing, oversized, and malformed exports remain private-safe failures, and diagnostics never include exported account or character data.
- Character roster loading is read-only and does not modify EveJS game data.

### Contributor
- Thanks to Darius Tumas ([@Tokeiito](https://github.com/Tokeiito)) for reproducing, diagnosing, testing, and contributing the original fix. 🫡

### Verification
- **1,158 automated tests passed**, including retired-character exports, noisy preload output, competing JSON output, malformed active records, privacy boundaries, visible failure state, authority changes, and roster recovery.
- Source compilation, dependency validation, the Foundation smoke suite, release-version consistency, packaged startup and shutdown, and ZIP integrity were verified before publication.

## v1.0.39 — 2026-08-14

### Fixed
- **Native character provisioning after service shutdown** — character creation and character/account deletion now wait for EveJS v0.12.5's durable world, wallet, and scheduler ownership leases to clear before backing up the GameStore and starting maintenance. This prevents the persistence-owner conflict that could appear immediately after the launcher stopped the game service.

### Safety and compatibility
- The ownership wait is bounded and fails closed with an actionable message if another process keeps renewing a lease. The launcher never forces ownership or edits lease records.
- Maintenance acquires without replaying journal entries, rejects pending persistence recovery before character mutation, and validates that no owner epoch changed before an automatic rollback restores data.
- A helper failure before maintenance begins no longer triggers an unnecessary restore. EveJS v0.12.4 keeps its established offline backup-and-rollback behavior without importing GameStore before the backup.

### Verification
- **1,145 automated tests passed**, including durable-lease expiry and renewal timing, held maintenance authority, pending-journal rejection, owner-checkpoint rollback fencing, legacy compatibility, Native creation/deletion, and adjacent lifecycle and Docker regressions.
- Source compilation, helper syntax checks, dependency validation, the Foundation smoke suite, and release-version consistency passed before packaging.

## v1.0.38 — 2026-08-13

### Added
- **Deep Signal interface** — Home, Characters, Mods, Tools, Settings, the setup wizard, and the updater now share one glass-panel operations-console design with responsive layouts, clearer hierarchy, and semantic service states.
- **LYRA Balanced Lift voice** — a bundled fixed catalog announces important service and client operations, with independent voice controls, a Settings preview, captions, bounded startup retries, and no runtime voice synthesis.
- **Living Universe audio** — the original Celestial Transit soundtrack and Deep Signal ambience add an optional soundscape without UI click sounds. Music and LYRA voice can be controlled independently.
- **Managed Docker character creation** — compatible launcher-controlled EveJS v0.12.5 Compose projects can create a new account and character from the Characters page. Connect-only remains read-only, and character/account deletion remains Native-only.

### Changed
- **First-run and update flow** — the setup wizard, available-update dialog, download/install progress, restart stage, failure state, and standalone update agent now use the Deep Signal visual system and explicit progress stages.
- **Public documentation** — README screenshots now reflect the v1.0.38 interface with generic example paths, private identity text obscured, and no mouse cursor.

### Safety and compatibility
- Managed Docker creation freezes and revalidates the exact stopped Compose target immediately before mutation, including the effective interpolated config, service records, reviewed image/runtime contract, authoritative GameStore mount, and nested/overlapping mount exclusions.
- Creation acquires the maintenance lease, creates and revalidates a scoped retained backup, verifies the account, character, rookie ship, persistence queue, and exact allowed logical data changes, and restores only the services that were previously online after confirmed cleanup or rollback.
- Unsupported layouts, changed targets, ambiguous service states, invalid helper inputs, pending persistence work, damaged backups, unexpected existing-data changes, and unverified rollback or lease cleanup all fail closed before service restoration.
- LYRA ships only the finite reviewed WAV catalog and manifest. Raw voice sources, the approved review master, and synthesis/processing tools are excluded from the application package.

### Verification
- **1,120 automated tests passed**, covering Managed Docker creation authority, backup, rollback, retained-data, service restoration, Deep Signal UI, LYRA catalog/playback, and Native compatibility behavior.
- Native Qt Multimedia playback, source-mode visual checks, packaged LYRA integrity, frozen Settings preview, and Windows startup/shutdown were verified before publication.

## v1.0.37 — 2026-08-12

### Added
- **Unsaved Settings protection** — leaving Settings or closing the launcher now offers Save, Discard, and Cancel. Failed saves and Docker validation failures keep the draft open instead of navigating away.

### Changed
- **Canonical EVE client selection** — setup and Settings now browse for the copied client folder and consistently store its `tq` root. Selecting `bin64` or `exefile.exe` is repaired automatically when the client layout can be verified.

### Fixed
- **EveJS v0.12.5 character maintenance** — character creation and character/account deletion now opt into the offline maintenance role, acquire the durable owner lease, and release it through the public shutdown path. This fixes the `reader may not flush __all_tables__` deletion failure and prevents a maintenance lease from delaying the restarted world server.
- **Invalid client-path persistence** — newly entered paths must contain `start.ini` and `bin64/exefile.exe`, and every launch-time consumer receives the verified `tq` folder instead of a raw executable or `bin64` path.

### Safety and compatibility
- EveJS v0.12.4 retains its checked legacy flush/worker/SQLite shutdown path, while v0.12.5 uses the maintenance ownership API. Existing backup, rollback, offline-service, and post-operation verification boundaries remain in place.
- An unchanged legacy client path on a temporarily unavailable drive remains editable in Settings, but unresolved paths are rejected before profile or client-launch mutation begins.

### Verification
- **811 automated tests passed**, including real-helper maintenance-role coverage, character rollback, client-path normalization, unsaved navigation and close handling, Docker validation continuations, and existing Native/Docker regressions.
- Source compilation, dependency checks, the Foundation smoke suite, and an interactive source launch were verified before commit.

## v1.0.36 — 2026-08-11

### Added
- **Optional local auto-login** — compatible Native installations can opt in per launcher configuration. The launcher uses EveJS's fixed local development credential and never stores a real account password.
- **Character and account creation** — the Characters page now includes a New Character tile with account name, character name, optional GM status, and optional overview-copy source.
- **Verified overview-copy bridge** — the launcher can apply or remove a backup-first EVE client patch for the exact supported client build 3396210, allowing a new character to receive another character's overview settings on first launch.
- **Character groups** — create, rename, delete, and fully configure groups, assign characters, select a group from Home or Characters, and launch its eligible accounts through the existing serial queue.
- **Backup-first deletion** — Native users can delete a character or its complete account from the character menu after typed confirmation. The launcher verifies the result and restores the scoped backup if the operation fails.

### Changed
- **Account-aware group launching** — group launches preserve the existing one-character-per-account rule, skip ineligible clients, and expose the same cancellation and stagger controls as Launch All.
- **Immediate character refresh** — newly created characters remain visible and the creation dialog updates its patch state as soon as patching completes.

### Fixed
- **Safe overview patching** — patch version 3 preserves the original CCP method bodies instead of relying on damaged decompiler output, preventing obstructed menus and black-screen undocking.
- **Automatic login launch path** — supported auto-login launches no longer leave a visible CCP command window behind.
- **Current EveJS portraits** — portrait discovery now checks the active EveJS v0.12.4 game-store image directory before legacy locations, restoring character profile pictures.

### Safety and compatibility
- Character creation, deletion, and overview patch changes require a Native runtime with Game, Market, and EVE clients offline.
- The overview bridge and auto-login are enabled only after exact build, archive-entry, hash, and local server-configuration checks. Unsupported clients remain unmodified.
- Every overview patch and destructive database operation creates a scoped backup and verifies the final state.

### Verification
- **780 automated tests passed**, including character lifecycle, overview patching, auto-login, portrait discovery, group configuration, group launch, and existing Native/Docker regressions.
- Source compilation, dependency checks, the Foundation smoke suite, and isolated packaged onedir startup were verified before publication.

## v1.0.35 — 2026-08-01

### Added
- **Docker Compose support** — choose Docker during setup or in Settings, test the project before saving, and use either read-only Connect-only mode or launcher-controlled Managed mode.
- **Docker-aware launcher features** — service status, logs, client endpoints, characters, portraits, mods, and supported Tool Deck actions now follow the selected Compose project.

### Fixed
- **Safer Docker startup** — setup failures are clearer, services wait for real readiness checks, and timed-out Docker commands clean up their process trees.
- **Native startup status** — Native start actions now show Starting immediately instead of briefly returning to Offline.
- **Clearer setup** — Native and Docker choices and their optional settings are now explained in plain language.
- **Responsive client launch** — character buttons now show Launching immediately and the launcher stays usable while EVE starts.

## v1.0.34 — 2026-07-29

### Added
- **Curated Tool Deck** — a searchable Tools page groups 11 reviewed EveJS utilities into Client & Setup, Configuration, Data & Content, and Market sections, with availability, prerequisites, source folders, and responsive cards.
- **Safe external tool launching** — supported wrappers run from the configured EveJS installation in independent visible consoles with explicit working directories. The launcher does not recursively expose arbitrary or internal scripts.
- **Guarded maintenance actions** — database reset includes a non-destructive preview mode and requires explicit confirmation before the real reset. System-changing setup actions also require confirmation.

### Changed
- **Root-aware refresh** — Tools refreshes when opened and when the configured EveJS root changes, while missing roots, folders, and wrappers now have deliberate explanatory states.

### Fixed
- **Server Config Editor prerequisite** — its Tool Deck card now states that Docker Desktop must be running for containerized EveJS installations instead of presenting the utility without that requirement.
- **Clean Tool Deck tab transitions** — returning to Tools now reuses unchanged cards instead of rebuilding them, eliminating transient mini-windows and card pop-in while still rescanning wrapper availability.
- **CMD-sensitive tool paths** — wrapper paths are passed through a dedicated environment variable with delayed expansion disabled, preserving legal `%`, `!`, `&`, `^`, parenthesis, and space characters without interpolating them into command text.
- **Per-tool path resolution failures** — inaccessible reparse points and resolution loops now mark only the affected tool unavailable instead of breaking the entire Tool Deck refresh.
- **Central launch guardrails** — the application re-resolves the current curated wrapper and action, restores catalog-owned arguments, and enforces destructive/system confirmation at the final spawn boundary.

### Verification
- **210 automated tests passed**, including focused Tool Deck safety and integration coverage, the Foundation smoke suite, and layout checks at 100%, 125%, and 150% scaling.
- Source-mode and isolated packaged onedir smoke checks confirmed the Tool Deck resolves all 11 wrappers from the configured external EveJS installation without bundling the tools tree.

## v1.0.33 — 2026-07-28

### Fixed
- **Update cleanup reliability** — completed self-updates no longer spawn a detached `cmd.exe` cleanup process. The restarted launcher removes only its validated temporary staging folder and adjacent rollback copy.
- **Home release notes** — Home now recognizes the newest version heading instead of treating the document's `Changelog` heading as a release, so the current version and highlights display again.

## v1.0.32 — 2026-07-28

### Added
- **Transparent self-update progress** — a branded launcher-style progress window now shows real download bytes, package preparation, installation, restart activity, and actionable errors instead of leaving an unexplained gap.
- **Safe visible update handoff** — the staged new launcher keeps the update window open while the old launcher exits, then reports each backup, file-copy, verification, and restart step.
- **Non-blocking Launch All queue** — clients launch serially with the configured stagger while the UI stays responsive, reports progress, and can cancel future launches without closing clients already started.
- **Shared runtime service snapshot** — Home, navigation, and footer now present one consistent Game/Market state, including Starting, Online, Stopping, Failed, and externally managed services.

### Changed
- **Service lifecycle work moved off the UI thread** — Market readiness, Game readiness, graceful shutdown, and fallback termination run in dedicated workers, keeping the launcher interactive during long operations.
- **Home and dashboard polish** — visible-character totals, service action labels, compact layout behavior, footer status, toggles, save feedback, and hero-animation settings now update consistently at runtime.
- **Update replacement safety** — the current onedir install is renamed to `.old` before replacement and retained until the new executable has been copied and verified.

### Fixed
- **Windows update lock delay is now explicit** — the updater visibly counts down while Windows releases PyInstaller/DLL file locks before it swaps launcher folders.
- **No accidental control of external services** — a reachable Game or Market service that was not started by this launcher is shown as externally managed and is never force-stopped by the launcher.
- **Lifecycle close races** — closing during an update check, service monitor startup, or service lifecycle work now waits for the retained worker to finish cleanly instead of risking a frozen or abruptly terminated launcher.
- **Launch All responsiveness and consistency** — hidden/banned character filtering, username prefill, dashboard metrics, and bulk launch now use the same visible-character view.

### Verification
- Full automated regression suite, source-mode GUI smoke, and packaged onedir smoke passed before release packaging.

## v1.0.31 — 2026-07-28

### Added
- **Server start selector** — Settings now discovers `StartServer*.bat` mode indicators from the configured EveJS root. Choose **Always ask**, save a specific indicator, or rescan after changing roots.
- **Runtime chooser behavior** — one supported indicator is selected automatically; multiple indicators prompt at the moment the game server is started; a saved filename skips the prompt while it remains available.
- **Complete start-path integration** — manual Start Server, Start All, client-triggered auto-start, server restart, and Mods → Apply & Restart all use the same resolver.
- **Selector regression coverage** — added focused tests for discovery, saved/stale preferences, cancellation, vanilla/modded commands, all start routes, settings behavior, cache invalidation, and config recovery.

### Changed
- **Batch files are mode indicators only** — `StartServer.bat` maps exactly to vanilla and `StartServerWithMods.bat` maps exactly to modded. The launcher never executes either batch file; it continues to launch Node.js directly with an explicit mode.
- **Safe cancellation ordering** — Start All and client auto-start resolve the selected mode before starting Market, so cancelling the chooser starts nothing.
- **Single preference model** — server selection is stored as `ask` or a root-relative filename. Legacy selector keys migrate automatically.

### Fixed
- **Stale saved selections** now reset to Always ask and show the chooser instead of silently choosing another mode.
- **Unknown custom indicators** are rejected and described as unsupported instead of being treated as automatically usable.
- **Settings explanations** refresh immediately after the user changes a stale selection.
- **EveJS root changes** clear solar-system and portrait caches and refresh the Mods page, preventing data from the previous installation from remaining visible.
- **Config persistence** now uses an atomic temporary-file replacement, isolated defaults, malformed-file backup, and recovery to clean defaults.

### Verification
- **59 automated tests passed**, including the Foundation smoke suite.
- Source-mode startup and the complete live selector matrix passed for Always Ask, cancellation, vanilla, modded, saved preference, single-script automatic selection, Start All, and Mods → Apply & Restart.

## v1.0.25 — 2026-07-25

### Fixed — Distribution packaging refinements
- **Switched from PyInstaller `--onefile` to `--onedir`** — the launcher is now a folder with a small bootloader and `_internal/` directory.
- **Replaced `pyautogui`/`pygetwindow`** with native Win32 APIs (`ctypes` — `EnumWindows`, `SetForegroundWindow`) for window focus and restoration.
- **Unbundled VBS updater** — VBScript is now a Python string constant, not a separate bundled file, avoiding a shipped file-deletion helper.
- **Exe size: 35 MB → 2.1 MB** (DLLs now live in `_internal/` folder).

### Changed
- **Distribution format** — now ships as a `.zip` containing the onedir folder. Extract and run `EveJS-Launcher-V1.exe` from inside the folder (do not move the exe out).
- **Updater rewritten** for folder-based distribution — downloads `.zip`, extracts, and replaces the install folder.
- **Update checker** now looks for `.zip` assets (not `.exe`) in GitHub releases.
- **`requirements.txt`** simplified: only PyQt6 + pyinstaller remain.

## v1.0.24 — 2026-07-24

### Fixed
- **Character portraits missing after EveJS v0.12.3 upgrade** — portrait images in `server/src/_secondary/image/generated/Character/` are generated by the EveJS server and weren't carried over during migration. Now documented as a migration step; launcher gracefully shows skeleton placeholder when portraits are absent.

### Removed
- **Linux support** — removed `platform_linux.py`, `build_linux.spec`, `.venv-linux/`, and all Linux-specific code paths from `platform.py`, `app.py`, and `updater/installer.py`. Platform abstraction simplified to Windows-only. Linux support may be picked up by community contributors in the future.

## v1.0.23 — 2026-07-24

### Added
- **Linux cross-platform support** — platform abstraction layer auto-detects OS at import time. Proton auto-detection, wmctrl/xdotool window focus, os.symlink profiles, bash nohup+disown updater, Wine settings paths through Proton prefix, xdg-open/$EDITOR for text editor. Single codebase — no more Windows-only assumptions.
- **Linux PyInstaller build** — `build_linux.spec` and `scripts/build_linux.sh`.

### Fixed
- **ResFiles resolved from configured client, not TQ junction** — previously resolved through `.resolve()` on the profile junction, which landed on the real TQ client's `SharedCache\ResFiles`. Now derives ResFiles from the **configured `client_path`** directly (`client_path/../ResFiles`), matching official Play.bat behavior. Launcher now works without official EVE TQ installed. Thanks to **Space Police Backup [ELYS]** for catching this! 🫡

### Changed
- 8 files refactored to use platform API instead of Windows-only code.

## v1.0.22 — 2026-07-24

### Added
- **Version number in status bar** — app version now displayed bottom-right in the footer for quick reference.
- **Auto-hide test/GM accounts** — new `hide_test_accounts` config option (default: true). Accounts with "test" prefix or "gm" in username are hidden from the character grid. Users can unhide individually or disable in config.

### Changed
- **Template YAML scrubbed** — placeholder usernames replace personal data in shipped template.

## v1.0.13 — 2026-07-24

### Fixed
- **New accounts: EVE window never appeared** — root cause was missing bootstrap settings files (`core_user__.dat`, `core_char__.dat`). The EVE client requires these to render the DirectX login window; without them the process stays alive but shows nothing. `create_profile()` now copies template bootstrap files for every new account.
- **Username pre-fill not working** — `prefill_username()` wrote `username:` as a top-level YAML key, but the EVE client stores it under the `ui:` section and silently drops anything at the wrong nesting level. Now writes username + usernames under `ui:` with correct indentation.

### Changed
- **Auto-login removed from launch flow** — clicking LAUNCH now spawns the EVE client with username pre-filled and stops. No CMD windows, no typing, no interference. User types password manually.

## v1.0.12 — 2026-07-24

### Fixed
- **CMD windows flashed on every auto-login action** — `subprocess.run(["powershell", ...])` spawned visible console windows. The polling loop in `_find_window_via_powershell` flashed a CMD every 1 second while waiting for EVE (10-30 windows per launch). These popups stole focus, minimized fullscreen games, and could crash the EVE client. Added `creationflags=CREATE_NO_WINDOW` to all PowerShell subprocess calls.

## v1.0.11 — 2026-07-24

### Fixed
- **New accounts never showed login window** — `newbie=1` in `prefs.ini` caused EVE client to open setup wizard instead of login screen. The wizard renders nothing under EveJS proxy → process alive but no DirectX window. `prefill_username()` now auto-sets `newbie=0` for all accounts so they show the normal login screen on first launch.
- **Auto-login crashed with `'bool' object has no attribute 'activate'`** — gateway condition used `and` instead of `or`, so when pyautogui was available but pygetwindow wasn't, the code fell through to the pygetwindow path which returned a bool from PowerShell detection. Changed `and` → `or`.
- **Auto-login typed credentials into wrong window (PowerShell path)** — `_find_window_via_powershell()` had no false-positive filter, matching the launcher itself and File Explorer windows. Added `-notmatch` exclusion for same substrings used in pygetwindow path.

## v1.0.1 — 2026-07-24

### Fixed
- **EVE client window never appeared** — `stdout=subprocess.DEVNULL` redirected the GUI process's output handles, preventing the DirectX window from being created. Removed both redirects.
- **"Running" status shown instantly but window takes 15s** — added gold **"LAUNCHING..."** status for the first 20 seconds after subprocess spawn, transitioning to green "RUNNING" once the window is expected to be visible.
- **EVE window minimized/hidden after launch** — `_restore_eve_window` daemon thread now finds the EVE window, un-minimizes it, and brings it to front.
- **Auto-login typed into wrong window** — `wait_for_window` matched File Explorer, browsers, and the launcher itself ("EVE" substring too broad). Now excludes known false-positives and requires window ≥300×200.
- **Auto-login stopped after login screen** — now waits 5s for character-select screen and presses Enter to auto-select the first character.
- **"LAUNCHING" stuck after closing EVE** — `_eve_window_exists()` check detects when the user closes the client so status drops to READY immediately.
- **Market showed online when only server running** — `_is_market_running` was checking port 26001 (game server's market proxy). Now checks `_market_proc` + port 40111 (actual market RPC).
- **Server/Market consoles empty** — both processes now pipe stdout to temp log files (`server_console.log`, `market_console.log`). Console panels tail these files for a 1:1 mirror of terminal output.
- **Market server shut down immediately** — batch wrapper used `start /b /wait` which shared the console; replaced with direct `cargo run` / pre-built binary launch via `subprocess.Popen` with stdout capture.
- **Market console showed stale server log** — added `clear_content()` and `set_title()` to ConsolePanel for market-specific display.

## v1.3.0 — 2026-07-24

### Fixed
- **Console panel not opening on first click** — StatusSection child labels (dot + text) were intercepting mouse clicks before they reached the clickable parent. Now `WA_TransparentForMouseEvents` passes all clicks through to StatusSection directly. Single-click the "Server: Offline" or "Market: Offline" status bar section to open the console.
- **Hero banner not filling container** — three-part root cause fix:
  - Removed broken `QStackedLayout` (reported 0×0 geometry on Windows/Qt6); labels now manually positioned via `setGeometry()` in `resizeEvent`
  - Removed unnecessary `QGraphicsOpacityEffect` from front label (broke `scaledContents` rendering, causing pixmap to display at native size instead of stretching)
  - Removed stale `_active_width` cache — rendering now uses live `self.width()` + `scaledContents=True` so banner always fills 100% of container from the first frame
- **Hero banner top margin gap** — removed 16px gap so banner sits flush under the title bar
- **Hero banner cross-fade rendering** — `_advance()` now correctly renders the *next* image for the back label instead of the current one
- **Nested page duplication** — pages were constructed with `MainWindow` as parent AND added to `QStackedWidget`, causing Qt to render them in both places. Removed `self` parent — `QStackedWidget` now owns pages correctly.
- **Character portraits not loading** — `PortraitLoader` searched wrong paths; updated to V1's actual path with hex-masked rendering and async loading
- **Character selection no visual feedback** — cards now get 2px teal border + glow + lift on selection; hover suppressed while selected
- **"Hide Character" button dead** — `lambda: None` placeholder replaced with full signal chain: card overflow menu + detail panel button → config save → grid refresh
- **Title bar buttons rendering as boxes** — Segoe MDL2 glyphs replaced with universal Unicode characters (`— □ ❐ ✕`) at 14px Segoe UI
- **Launcher closing when stopping server** — `_graceful_kill` used `ctypes.AttachConsole` + `GenerateConsoleCtrlEvent` which could kill the GUI process; replaced with `proc.terminate()` → wait → `taskkill /F`
- **Character stats not showing in detail panel** — `Character` dataclass now extracts ISK, SP, ship, location, and sec status from EveJS character JSON; formatted with helpers (`_fmt_isk`, `_fmt_sp`); wired through card → page → detail panel

### Added
- **First-run setup wizard** — modal dialog appears automatically when no `evejs_root` is configured. Walks through: Welcome → Browse EveJS folder → Validation → Done. Writes config and launches normally.
- **Discord card restyling** — Discord blurple replaced with launcher dark theme colors (`carbon` + `steel`); EveJS logo replaces 💬 emoji; content centered

---

## v1.2.0 — 2026-07-24

### Added
- **Animated hero banner** — cross-fades between fleet, station, and nebula banners every 6s with cinematic Ken Burns zoom effect
- **Page transition animation** — smooth fade between Home and Characters pages
- **Card hover effects** — cards lift with a teal glow on hover
- **Button glow pulse** — Launch All and Start All Servers buttons have a subtle pulsing glow
- **Scanline overlay** — subtle EVE-style scanline texture on the home page
- **Animation toggle** — disable all animations in Settings → General if you prefer the classic static look
- **Hero rotation interval** — configurable in Settings (3–30 seconds)

### Changed
- Hero banner now animates by default; static random-pick is the fallback when animations are off

---

## v1.1.0 — 2026-07-23

### Fixed
- **Server/Market startup bug**: CMD windows opened and closed instantly due to broken path quoting (`""path""` → `"path"`). Servers now start correctly.
- CMD windows now use `/k` flag so they stay open even if the script exits, letting you read errors.

### Added
- **Start All Servers** button on Home page — starts Market first, waits for it, then starts Game Server.
- **Changelog panel** on Home page (this section).
- **Discord invite** button on Home page linking to the EveJS community.

### Changed
- Home page action buttons repositioned and cleaned up.
- Market server and Game server startup order enforced: Market always starts first.

---

## v1.0.0 — 2026-07-21

### Initial Release
- Multi-account character grid with 3-per-row card layout
- Junction-based EVE client profiles (zero-copy)
- Server & Market start/stop from within the launcher
- Mod manager with toggle (rename `loader.js`)
- Auto-login support
- First-run wizard for EveJS root and client path
- EVE Online dark theme with teal accents
- Character detail panel with portrait, ISK, ship, SP, sec status
- Launch All with configurable stagger delay
- Kill All Clients button
- Frameless window with custom title bar and resize edges
