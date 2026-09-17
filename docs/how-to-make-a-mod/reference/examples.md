# Working examples

[Guide home](../00-start-here.md) | [Manifest](manifest.md) | [Helpers](helpers.md)

These complete packages are bundled as readable source. Copy an example into a disposable test installation, then import/adopt it in Mods. The examples demonstrate their stated behavior; they contain no EVE binaries or gameplay patches.

| Example | Language | Behavior |
| --- | --- | --- |
| [Cleanup Retry](../../../examples/mods/cleanup-demo/README.md) | Node.js | Durable pending cleanup, retry after a lost reply, exact-owner preservation and removal only when ready |
| [Hello Loader](../../../examples/mods/hello-loader/README.md) | JavaScript | Disabled by default; logs one startup line when selected as a preload |
| [GitHub Update Demo](../../../examples/mods/github-update-demo/README.md) | JavaScript | Disabled loader with an update-source template; replace its repository placeholders before import |
| [Profile Options](../../../examples/mods/profile-options/README.md) | Node.js | Localized profile form, typed values and private INI key proposals |
| [Client Receipt](../../../examples/mods/client-receipt-demo/README.md) | PowerShell | A real request/reply/receipt lifecycle with no binary payload |
| [Shared Profile Companion](../../../examples/mods/shared-profile-companion/README.md) | JSON only | Two mods share profile INI keys; independent removal preserves the other owner |
| [Source Overlay](../../../examples/mods/source-overlay-demo/README.md) | Node.js | Folder/ZIP import, configurable text region, shared ownership and removal/undo without an external activation flag |

The release tests copy these packages into temporary roots and validate them with the real schema parser, settings backend and helper host. Helpers run only in those copies. This validates the launcher boundary; it is not evidence that an author's unrelated gameplay or renderer implementation works.

For an actual integration, replace the demonstration behavior, choose a unique ID, document its dependencies and implement the [ownership/recovery rules](ownership.md). Do not ship these example IDs or demonstration receipts as proof of a real installed client patch.

## GitHub updates: DLSS5 + ReShade

The [GitHub updates walkthrough](updates.md) shows how an existing mod adds update metadata, names its release assets, presents changelogs and preserves configuration. It uses DLSS5 + ReShade to explain the manual first upgrade from legacy 0.5.7 and subsequent in-launcher updates. Its hypothetical version numbers are documentation examples, not bundled renderer releases. The walkthrough also separates local update fixtures from actual hosted-release and rendering verification.
