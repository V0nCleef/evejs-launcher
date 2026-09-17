# Configure button demo

This package demonstrates a real Configure form. It saves a preference and has no gameplay code or helper.

1. Use **Mods â†’ Add Folder** and select this folder in a disposable EveJS installation.
2. Click **Configure** beside **Scan Settings Demo**.
3. Change **Scan interval (seconds)** from **10** to **20**, then click **Save**.
4. The imported package's `preferences.json` now contains `"scanInterval": 20`.
5. Close and reopen the form. It still shows **20**. Cancel a different edit and check the file stays unchanged.

The restart reminder demonstrates the field's `restart` declaration; this demo does not actually scan. A real mod must read the value and implement that behaviour itself.

[Manifest](evejs-launcher.mod.json) | [Illustrated walkthrough](../../../docs/how-to-make-a-mod/03-configure.md)
