# EveJS Launcher V1

## Changelog

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

### 🔒 Fixed — Antivirus false-positive detections
- **Switched from PyInstaller `--onefile` to `--onedir`** — eliminates `%TEMP%` extraction that triggered malware heuristics (T1027, T1497, T1129). Launcher is now a folder with a small bootloader + `_internal/` directory.
- **Replaced `pyautogui`/`pygetwindow`** with native Win32 API (`ctypes` — `EnumWindows`, `SetForegroundWindow`). Removes riskware-classified input simulation library.
- **Unbundled VBS updater** — VBScript is now a Python string constant, not a separate bundled file. AV heuristics no longer see a file-deletion script in the binary.
- **VirusTotal: 0/59** (was 4/70). Sigma rules: NOT FOUND. MITRE signatures cleared.
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
