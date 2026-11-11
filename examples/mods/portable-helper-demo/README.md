# Portable Docker helper example

Requires Launcher 1.0.80, Managed Docker, and an existing supported EveJS Compose
project. Import this folder with **Mods → Add Folder**. Enable it while Game is
stopped, then use **Apply & Restart**. The loader only prints a message.

The Node helper runs in the selected Linux server container, or a temporary
container from its selected image when stopped. Install creates one owned
`/var/lib/evejs/mods/portable-helper-demo/installed.json` receipt. Verify, startup,
Disable and Remove check it without writing. Recovery creates a missing receipt;
it does not overwrite an unknown receipt. Removal retains persistent state.

This demonstrates execution placement and persistence. It does not implement
gameplay, enabled updates, a client companion or an image contribution. Add those
contracts only after implementing and qualifying them. Declaring Docker support
does not make a Windows installer portable.

See [the contract](../../../docs/how-to-make-a-mod/reference/portable-docker.md)
for image layers, update recovery, host client preparation and login delivery.
