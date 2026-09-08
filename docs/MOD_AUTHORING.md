# Make a mod work with EveJS Launcher

This guide describes **Launcher 1.0.53**, public **manifest schema 3**, **helper API 1**, and **settings schema 1**. It ships with the launcher and opens from the Mods page without an internet connection.

The launcher manages discovery, local package ownership, settings, launch preparation and recovery. Your mod still owns its gameplay integration and any binary patcher. A descriptor does not create an upstream EveJS plugin API or make arbitrary source changes reversible.

## Start here

| Your mod | Start with |
| --- | --- |
| A JavaScript preload | [Hello Loader](../examples/mods/hello-loader/README.md). Existing `loader.js` mods also work without a descriptor. |
| Preferences or profile preparation | [Profile Options](../examples/mods/profile-options/README.md). No binary installer is needed. |
| A physical EVE client package | [Client Receipt](../examples/mods/client-receipt-demo/README.md), then replace its demonstration lifecycle with your verified installer. |
| A source-integrated schema-2 mod | Keep the [legacy contract](mod-authoring/legacy.md), or follow the [upgrade recipe](mod-authoring/recipes.md). |
| A mod distributed through GitHub releases | Add [update metadata](mod-authoring/updates.md), using DLSS5 as the worked example. |

1. Keep the package in its own directory under `<evejs>/mods/<folder>` or, for reviewed source integration, `<evejs>/server/mods/<folder>`.
2. Add `evejs-launcher.mod.json`. Choose a supported kind and activation in the [manifest reference](mod-authoring/manifest.md).
3. Add optional [settings](mod-authoring/settings.md). Add a [helper](mod-authoring/helpers.md) only for executable preparation.
4. Import or explicitly adopt the local package in Mods. Inspect the displayed paths before enabling it.
5. Test enable, the appropriate restart, disable, another restart, interrupted work and recovery in a disposable installation.

Copying a directory makes it discoverable. A public package's enabled state also needs launcher ownership; discovery alone does not authorize deleting an existing folder or installing into the client.

## Reference

| Page | Covers |
| --- | --- |
| [Manifest](mod-authoring/manifest.md) | Exact fields, kinds, activation, versions and backends |
| [Helper protocol](mod-authoring/helpers.md) | Requests, replies, actions, environment, arguments and execution limits |
| [Settings and languages](mod-authoring/settings.md) | Controls, labels, global/profile scope and text formats |
| [GitHub updates](mod-authoring/updates.md) | Update metadata, release notes, config preservation and the DLSS5 migration example |
| [Ownership and recovery](mod-authoring/ownership.md) | Key conflicts, preferences, shared clients, receipts and rollback |
| [Recipes](mod-authoring/recipes.md) | Layouts, client upgrades and legacy migration |
| [Working examples](mod-authoring/examples.md) | Complete Node and PowerShell packages |
| [Legacy loaders and schema 2](mod-authoring/legacy.md) | Existing loader and Game-server status contracts |
| [Legacy installer removal](mod-authoring/legacy-removal.md) | Existing schema-2 enrollment and uninstall handshake |

## What a successful launch proves

Configured enabled, helper ready and running in Game are separate states. A public client package verifies its physical installation before client launch; profile contributions commit before spawning. A server mod's configured flag alone does not prove gameplay behavior is active. Legacy status markers and loader evidence retain their existing rules.

Tie compatibility to what the mod actually changes. A client-only mod verifies its EVE build, renderer and files. If its loader, helper or installer touches EveJS internals, document and test those EveJS dependencies too. Its location inside an EveJS directory does not itself create a server-version dependency.
