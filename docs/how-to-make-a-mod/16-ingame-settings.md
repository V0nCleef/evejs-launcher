# 16. Add an in-game settings window

[Start here](00-start-here.md)

## Start with an example

Give players a small settings window inside the game: a checkbox, interval field, Apply, Reload and status text. Reuse the included pattern and change the labels and values for your mod. The Launcher’s [Configure form](03-configure.md) is a separate option; it does not create this in-game window.

![Existing AutoMining settings window](images/ingame-settings.png)

[Open full-size image](images/ingame-settings.png)

*Existing AutoMining window, shown as a real in-game example. The included starter is smaller and has not itself been tested in a live client.*

## Reuse the three pieces

Use **Save example files as ZIP** in this guide, then open `examples/ingame-settings`. On GitHub, the same folder contains the full files. Read its README first. This is an integration example, not a mod you can import with Add ZIP.

- `window.py` draws the controls, loads settings and sends Apply requests. Closing discards unsaved edits; a character change closes the window.
- `settings-handlers.js` validates values on the server, uses the authenticated character and rejects stale saves. Connect its `read`/`write` interface to your persistent character settings store.
- Your mod supplies the registered service, reviewed login delivery and a chat/menu action to open the window. Wait for character readiness and dispose old handlers on reconnect or replacement. See [client delivery and migration](12-client-files.md).

The example starts disabled with a 60-second interval and allows 6–86400 seconds. These are demonstration values, not a universal EveJS rule. The server feature must read the saved values. If Launcher settings edit the same feature, choose one owner and synchronize explicitly.

## Before sharing

Check opening and reopening, Apply, Reload, closing without saving, invalid values, stale saves, failed requests, character changes, reconnect and two clients. Confirm persistence after a server restart. Logic mocks do not prove client compatibility; verify your supported build in game. Keep any existing supported delivery path working.

## Hand off to your AI

Use **Hand off to your AI** on this page. It copies a task-specific prompt, the full window and server examples, integration notes and relevant contracts. On GitHub, open the AI handoff file linked below and supply the example folder. Tell the AI your mod folder and which settings players should edit. The migration page has a separate handoff for moving an existing companion to login delivery.

![Actual guide toolbar. Hand off to your AI copies the current topic and its attached files; Save example files as ZIP exports the examples.](images/ai-handoff-toolbar.png)

[Open full-size image](images/ai-handoff-toolbar.png)

*Actual guide toolbar. Hand off to your AI copies the current topic and its attached files; Save example files as ZIP exports the examples.*

---

## Code examples

[AI handoff + example files](../../examples/ingame-settings/AI-HANDOFF.md) · [README](../../examples/ingame-settings/README.md) · [window.py](../../examples/ingame-settings/window.py) · [settings-handlers.js](../../examples/ingame-settings/settings-handlers.js)

```javascript
// YourService and yourCharacterSettingsStore are YOUR integration points.
const { createSettingsHandlers } = require('./settings-handlers');
Object.assign(YourService.prototype, createSettingsHandlers(yourCharacterSettingsStore));
// From your registered command, after this client is ready:
session.sendNotification('OnExampleModSettingsOpen', 'clientID', []);
```

```python
# Your login integration calls this only after character readiness.
# Dispose the previous controller before replacement and on disconnect.
controller = install_window(sm, session, is_ready)
# On teardown:
controller.dispose()
```
