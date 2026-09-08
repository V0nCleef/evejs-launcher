# Client Receipt

[Guide home](../../../docs/MOD_AUTHORING.md) | [Descriptor](evejs-launcher.mod.json) | [PowerShell helper](helper.ps1)

This is a working protocol demonstration, not a renderer. It writes only its own demonstration receipt under the selected disposable client's `_local/mod-receipts/`. It installs no DLL, modifies no EVE binary and changes no gameplay.

Copy/import the directory into a disposable installation. Run Install to create its bound active receipt, Verify to read it, and profile preparation to check that enrollment. Disable/Remove changes that receipt to restored. Recover reads the current receipt; there is no multi-file binary state in this example.

The host checks the receipt's schema, state, mod identity and physical-client identity. Replace the demonstration action with your actual build checks, transaction, backup and rollback before writing a real binary mod. Only report active/restored after those checks succeed. Keep durable receipts independent of the package folder so cleanup remains understandable after an EveJS package upgrade.

The script is intentionally small and has no Qt/launcher imports. It demonstrates named PowerShell file parameters and atomic replacement of one owned receipt. It is not a complete multi-file binary installer template.
