# Integrate your mod with the shared in-game Mods menu

Keep gameplay and UI code in this mod. Do not edit the Launcher or borrow
AutoMining's private bootstrap. Use Launcher 1.0.69/menu API v1 and
the schema 3 `clientMenu` declaration in this folder's complete example.

Choose one stable manifest ID. Make its entrypoint self-contained, or supply
a callback to this mod's separately delivered window/HUD. Register a localized
label and real opener through `evejs_mod_menu`. If the existing companion loads
later, use a cheap `is_available` callback; do not import unavailable private
modules eagerly. Do not automatically open the window on registration.

Use handle.close() in cleanup and dispose this mod's own windows/listeners/tasks.
Keep independent supported openers (for example a chat command) working. Let
the shared framework own the Neocom and top-level Insider category; remove
your own duplicate shared-menu hook when migrating your mod. Do not alter
native roles or native entries. Use only the frozen enabled mod selection.

Verify without AutoMining, with a second participating mod, ordinary-player
Neocom, elevated Insider, repeat registration, reconnect and disable/restart.
Mark mock checks separately from actual client evidence. The Native Neocom button was checked in-game. Test your own mod openers
and lifecycle; live Insider and Managed Docker verification remain separate.
