# Shared Profile Companion

[Guide home](../../../docs/MOD_AUTHORING.md) | [Profile Options](../profile-options/README.md) | [Ownership](../../../docs/mod-authoring/ownership.md)

This complete settings-only mod needs no helper, Node runtime or author-supplied
hashes. Its [descriptor](evejs-launcher.mod.json) creates a profile form through
the public settings API. Use a disposable installation and close its clients
before editing their settings.

## Two mods, two profiles, one file per profile

1. Import this folder and Profile Options using **Add Mod Folder**.
2. In Profile Options, save label `Alpha` for profile A and `Beta` for profile B.
   Enable Profile Options and prepare/launch each test profile. Its helper
   proposes `LauncherExample.Label` in that profile's actual `prefs.ini`.
3. Open this companion's settings and save hint level `2` for A and `4` for B.
   Its form writes `LauncherExample.HintLevel` in those same files.
4. Inspect each profile's `prefs.ini`. A has `Label=Alpha` and `HintLevel=2`;
   B has `Label=Beta` and `HintLevel=4`. Existing sections and comments remain.
5. Remove Profile Options. Its shared `Label` contribution returns to the value
   present before that mod changed it, or disappears if there was no value.
   Both companion hint levels remain. Private Profile Options preferences stay
   available for a later reinstall/undo.
6. Remove this companion. Each hint level returns to its own baseline too;
   unrelated user keys remain.

These keys are inert examples, not EVE settings or gameplay features. The files
are shared only between mods within one profile: profile A and B have different
physical files. The profile's `tq` junction is never the settings destination.

To explore a same-key conflict, change the companion descriptor's key to
`["LauncherExample", "Label"]` in a fresh disposable copy. Saving a different
value opens the host's conflict review. Inspect the result, then choose explicit
precedence or keep the current value. Do not resolve conflicts by replacing the
whole INI file or changing the host's ownership ledger.
