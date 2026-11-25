# Array Settings Demo

Import this folder from the launcher’s Mods page, or ZIP the folder and import the archive. Open **Configure** and resize the form to see the percentage fields wrap. In launchers with layout support, the layout file pairs the three percentages and gives Notes its own row. Enable preview still appears in its original group.

Change Array 2 to **17.5**, save, and reopen the form. The value is stored at `escalation.array2ChancePct` in `preferences.json`. This example saves preferences and runs no gameplay code.

Open the manifest to see what fields and values the form uses. Open `evejs-launcher.settings-layout.json` to see how they are arranged. Earlier launchers can read the same settings from the manifest.
