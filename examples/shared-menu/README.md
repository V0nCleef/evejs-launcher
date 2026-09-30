# Shared in-game Mods menu example

Launcher **1.0.69 or newer**, menu API **1**, manifest **3**,
EVE **build 3396210 / Python 2.7**, reviewed EveJS **0.12.9** login builder.
The Native Neocom button was checked in-game. Managed Docker delivery has
offline launch-plan checks; verify it in your own environment.

Put these three files in one `menu-demo` folder: `evejs-launcher.mod.json`,
`menu.py`, `loader.js.disabled`. ZIP that folder and import it with Add ZIP.
Enable it, start a new modded server with the Launcher and log in.
The in-game Neocom Mods icon opens a menu; Example mod opens this mod's window.
Elevated accounts also get a separate top-level MODS category in native Insider.
Ordinary accounts keep their normal permissions.

`clientMenu` is data: an API version and a relative entrypoint path. It does
not contain an opener expression. The entrypoint is trusted mod code, delivered
by the server from the frozen launch plan and executed in its own namespace.
It must be self-contained (up to 128 KiB UTF-8), or call a companion that your
mod has already delivered itself. Arbitrary sibling files are not uploaded.
The example creates its own native window and does not patch client archives.

The Launcher creates the API before running entrypoints, waits for a character,
and reads only the selected enabled loader folders at preflight. Native and
Managed Docker use the same captured sources. Connect-only Docker cannot alter
the remote server's preload chain: its owner must deploy it with Managed Docker.
Different server profiles never share staged files or registry selections.

`register(id, labels, opener, is_available=None, api_version=1)` returns a handle.
IDs are ASCII letters/digits/dot/underscore/hyphen, at most 128 characters,
and normalize to lowercase. Match the manifest's ID. An English `en` label
is required; translations use `nl`, `de`, `fr`, `ru`, `ja`, `ko`, `zh_CN` etc.
The active client language selects the label, falling back to English.
Opener and optional availability check are real Python callbacks, not strings.
Availability checks must be quick and not yield or mutate UI state.

Registering the same ID replaces its entry. `handle.update(labels, opener,
is_available=None)` updates it without changing ownership. `handle.close()`
unregisters it and is safe even after a newer registration replaced it.
Module-level `update(id, labels, opener, is_available=None)` and `unregister(id)`
are also available. Prefer the handle for delayed cleanup.

The Launcher invokes an optional entrypoint `cleanup()` on logout, character
change or bootstrap replacement; close your mod's windows/tasks/listeners there.
The next character gets a fresh entrypoint. UI refresh reuses the registration.
No entry is displayed before character readiness, after unregistering, when its
availability callback returns false, or after its opener/availability fails.
One bad mod does not prevent the other entries or native menus working.
An explicit update/re-register clears a failure; reconnect reloads entrypoints.

Disabling a loader requires the advertised game-server restart and reconnect.
The frozen running server retains its current selection until that restart.
For rollback disable this mod, restart the server, close/reopen clients, then
return to the previous Launcher. Staged `.evejs-launcher/shared-menu` artifacts
are inert unless the captured preload path is selected; no client file restore
is needed. Managed Docker must Apply its empty/reduced chain before downgrade.

Verify one mod without AutoMining, two mods sharing one icon, opener behavior,
relogin, duplicate registration, disabled/empty selection, ordinary-player
Neocom and role-restricted Insider on your actual supported client.
