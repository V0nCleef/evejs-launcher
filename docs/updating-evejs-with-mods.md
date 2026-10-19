# Updating EveJS with installed mods

Use EveJS's bundled `UpdateEveJS.bat` to update the existing installation.
Keeping the same folder and EVE client preserves DLSS5's attachment and each
launcher's profile graphics preferences.

1. Open the launcher with the installation selected and let Mods finish
   refreshing. The launcher prepares the bundled updater to keep all valid mod
   folders and declared server settings, plus legacy AutoMining state and NPC
   Buyers' native market configuration. Third-party authors use the same public
   settings declarations; no author registration is needed. Disabled
   installed mods receive the same protection. If preparation fails, Mods shows
   a warning; resolve it before running the updater.
2. Install available mod updates. AutoMining 1.2.18 also migrates player
   settings, statistics and fleet groups into the server data root's `automining`
   folder, normally `_local/automining`. Existing canonical state wins; legacy
   files remain untouched.
3. Stop Game and Market through the launcher, then close the launcher. Use
   `UpdateEveJS.bat` with the new EveJS release ZIP and keep its backup.
4. Reopen the launcher. Refresh Mods and verify DLSS5. An ordinary in-place
   update using the same EVE client does not require reinstalling DLSS5.
5. Start normally. With Launcher 1.0.74 and NPC Market Buyers 0.1.19 or later,
   the launcher automatically checks enabled NPC Buyers before starting either
   Game or Market. Recognised updater replacements are repaired and verified
   while both services are stopped. Settings and purchase history are retained,
   and reviewed upstream files become the new rollback baseline. No manual
   Recover action is needed for a supported update. Unknown edits, partial
   updates or an interrupted disable stop startup and require review.

The launcher keeps a copy of each original updater policy under
`_local/launcher-updater-preservation`. It adds preservation rules while keeping
the official updater script and the rest of its policy intact. A later updater
can replace its policy; the next launcher refresh prepares the new copy again.

This covers in-place updates. Moving to another EveJS folder or replacing the
EVE client requires separate attachment and client-compatibility checks.
