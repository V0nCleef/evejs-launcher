EveJS character-list diagnostics for JcKalck

1. Extract this whole ZIP into a NEW folder. Do not copy it over your Launcher.
2. Leave your existing EveJS Docker Server running.
3. Double-click Collect-Roster-Diagnostics.cmd.
4. If you have multiple running worlds, select the affected one.
5. Send back ONLY the EveJS-Roster-Diagnostics.json file shown at the end.

No Python installation or administrator access is required.

The collector reads the existing running container. It never starts, stops or
recreates containers, and never updates mods or changes your Launcher settings.
The game database is opened read-only. Reports contain counts, validation stages,
field names and value types. They do not contain account/character names, IDs,
passwords, tokens, database contents, raw exports, local paths or container names.
Nothing is uploaded automatically.

The included Collector folder supplies its small standalone runtime. It uses the
same validators as the Launcher fix. You do not need to install or replace your
normal Launcher to collect the report.
