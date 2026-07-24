# EveJS Multibox Launcher — Changelog

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
