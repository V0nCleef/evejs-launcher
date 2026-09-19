# AI handoff: add an in-game settings window

Adapt the attached `window.py` and `settings-handlers.js` to my EveJS mod. Reuse
the pattern instead of writing a second UI framework. Read the example README
and the guide's in-game settings chapter before editing. Read LOGIN-HANDSHAKE.md
and implement its delivery/readiness/cleanup sequence as part of this task.

First identify my mod's existing loader, registered services, settings store,
supported EveJS/client versions, client delivery method and open-window command.
Explain the actual integration points you found. Ask only for missing choices
such as the settings I want players to edit. Do not invent a universal Launcher
window or login API, service names, or unsupported client controls.

Build a small in-game window with clearly labelled controls, status text, Apply
and Reload. Keep edits local until Apply; closing without Apply must discard
them. Use a unique window/event/service identity for my mod. Keep the server
authoritative: derive character identity from the authenticated session, validate
every value and persist settings outside replaceable package files. Reject stale
writes. Connect saved values to the real feature; a checkbox alone is not an
implementation. If the mod also has Launcher settings, define one owner for each
setting and explicit synchronization so the two screens cannot overwrite each other.

Use the mod's reviewed login-handshake delivery for this new window. Do not
implement the tutorial by patching the physical client's archive or shared
command classes. Package the authored window and bootstrap in the mod, extend
the reviewed server login path without replacing normal behavior, execute in a
private namespace, and use bounded cooperative readiness attempts. The bootstrap
must install the controller once and acknowledge readiness to the authenticated
server session before the open-window command is enabled. Cancel stale attempts
and invalidate readiness when the connection, character or payload owner changes.
If no supported handshake integration exists, explain the gap instead of silently
adding a direct client patch. Do not just provide a UI with delivery left unwired.

Wait for character readiness
before exposing the window, preserve existing login behavior, and dispose old
event handlers/windows on replacement or disconnect. Do not patch shared client
command classes. Keep existing supported delivery methods working; this tutorial
does not authorize removing a legacy companion or editing unrelated client files.

Test invalid values, stale saves, failed storage/RPC, closing during a request,
character switching, repeated opening, reconnect and two clients. Verify settings
survive server restart. Use mocks for logic, then clearly list remaining live
checks against the supported build. Do not claim the starter itself is live-tested.

Deliver the adapted files, focused test results, integration instructions and
rollback steps. Do not publish, install into a live game, or restart services
without the user's authorization.
