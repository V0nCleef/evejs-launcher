# Hello Loader

[Guide home](../../../docs/how-to-make-a-mod/00-start-here.md) | [Descriptor](evejs-launcher.mod.json) | [Loader source](loader.js.disabled)

This complete example logs one startup line. It changes no gameplay, state, timers or databases. It ships disabled as `loader.js.disabled`.

Copy the `hello-loader` directory into a disposable EveJS installation's `mods` directory, adopt it in Mods, enable it and start the configured loader runtime. The Game console should contain `[hello-loader] Selected launcher preload is running.` Disable and restart: the line should be absent. An explicit Vanilla launch also omits it.

The descriptor is optional for a legacy loader but supplies a stable public ID and display name. Rename the example ID/folder before developing a real mod. See [legacy loading patterns](../../../docs/how-to-make-a-mod/reference/legacy.md) before adding EveJS-specific hooks.
