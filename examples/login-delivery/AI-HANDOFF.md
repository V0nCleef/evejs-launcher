# AI handoff: migrate legacy client scripts to login delivery

Migrate my EveJS mod's authored client companion from direct client script/archive
patching to supported login-handshake delivery. Use the attached client-files
chapter, helper contract and ownership reference. Preserve gameplay, settings,
other mods, existing Launcher behavior and normal player Update-button workflow.

First inspect the mod and authorized EveJS/Launcher sources. Identify the current
delivery code, exact supported server/client builds, native/Docker behavior,
receipts/backups, upgrade lifecycle and other server roots sharing the same
physical client. Do not inspect proprietary client code without permission.
Do not invent a generic login-payload API or assume the Launcher converts mods.

Implement the reviewed server integration without replacing existing login
behavior. Wait for the authenticated character and required client services.
Handle reconnect, repeated delivery, handler cleanup, disable/remove and failures.
Retain a working legacy path for supported environments lacking handshake support.
Choose the method from verified capability/build evidence, not a version label
alone. Do not quietly fall back to writing archives during profile preparation.

Make migration happen through the normal supported installation/update lifecycle.
Restore only verified entries owned by this mod, using its receipts and backups.
Preserve unrelated changes, player settings and rollback data. A clean migrated
client must still work when switching to Docker, an older supported server, or
another installation sharing that client. If automatic compatibility cannot be
provided, keep the compatible path and report the gap; do not call a manual
repair requirement a seamless migration.

Report clientScriptDelivery only when the Launcher advertises
EVEJS_LAUNCHER_HELPER_FEATURES containing client-script-delivery-v1. Report the
actual chosen method: login-handshake, client-script-patch or none. Older Launchers
must receive the original response shape. Never hide the legacy notice by
misreporting delivery. Graphics/DLL-only modifications are outside this notice.

Test fresh install, legacy upgrade, failed upgrade and rollback, reconnect,
disable/remove, both backends, unsupported-handshake fallback, older Launchers,
and two server roots sharing a client. Verify no duplicate UI or handlers and no
unrelated file/settings changes. Clearly separate synthetic checks from live
coverage and disclose every unsupported path.

Where automatic fallback is required, use the documented Launcher 1.0.61
client-preparation-v1 contract. Enroll only exact older versions you have tested;
require that Launcher version before migration. Keep clientScriptDelivery as
advisory reporting. Do not invent manifest fields or make every mod auto-install.
Use the helper reference included with this handoff for the complete contract.

Deliver a reviewable patch, focused test results, rollback instructions and short
author/player update notes. Do not publish or mutate a live installation without
authorization. Ask about unresolved product choices only after investigating the
available sources; do not ask users to manually fix things the updater can handle.
