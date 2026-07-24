# EveJS Launcher V1

## Changelog

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
