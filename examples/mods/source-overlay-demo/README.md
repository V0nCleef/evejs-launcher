# Source Overlay Demo

A source mod with no separate activation config and no direct shared-file writes.
It uses the public helper API and the launcher's contribution journal.

1. Use a disposable EveJS installation with its server stopped.
2. Copy `example-source.js` to `server/launcher-overlay-demo.js` in that installation.
   Do not replace an existing file; choose a fresh test installation instead.
3. Import this mod folder or a ZIP containing it. It starts disabled.
4. Open Configure, choose `first` or `second`, and save a replacement value.
5. Enable the mod and inspect the demonstration file. Only its selected region changes.
6. Disable or remove the mod. Its region returns to the original. Undo Removal
   restores its folder disabled and retains private options; enable reapplies them.

To demonstrate two mods together, make a second copy of this package with a unique
folder and manifest `id`. Configure the copies to use different regions. Removing
either preserves the other. Choosing the same region with different values opens
the conflict review. Copy changes belong to your test packages, not the bundled guide.

This file is not loaded by the game. The example proves API behavior, not gameplay
compatibility. Real source mods must identify unique stable anchors and implement
their own runtime cleanup where needed. This example's helpers declare cleanup
ready because it has no timers/entities and all shared edits are host-owned.
