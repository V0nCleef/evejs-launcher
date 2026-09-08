# Profile Options

[Guide home](../../../docs/MOD_AUTHORING.md) | [Descriptor/form](evejs-launcher.mod.json) | [Node helper](helper.js)

This complete settings package stores a label, integer and Boolean in private per-profile INI files. Its options are deliberately demonstration values; they do not change rendering or gameplay. No binary installer or package-specific launcher code is involved.

During profile preparation it also proposes the inert `LauncherExample.Label`
key in the actual profile's `prefs.ini`. The [Shared Profile Companion](../shared-profile-companion/README.md)
adds a different key to that same section and demonstrates independent removal.
The helper needs a profile settings context even though its editable preferences
are private. Use disposable profiles and close their clients before this exercise.

Import/adopt this directory in a disposable EveJS installation, enable it, choose a profile and open its configuration form. Save different values for two profiles. On launch the Node helper receives typed values and proposes the corresponding keys; the host commits them before client spawn. Reopening and saving unchanged values preserves the original bytes.

Node.js must be on PATH. The helper writes only its result JSON. It never imports Qt, touches the shared client, changes EVE endpoints or starts a process. Its optional `LAUNCHER_PROFILE_OPTIONS` environment variable identifies the private file for an author's own consumer; EVE itself does not read that demonstration variable.

Labels show English and Dutch locale maps with an English fallback. IDs and persisted keys stay unchanged across languages. `enabled` demonstrates Boolean-to-0/1 storage, while the helper still receives a true Boolean.
