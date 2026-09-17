# GitHub Update Demo

[Guide home](../../../docs/how-to-make-a-mod/00-start-here.md) | [Update walkthrough](../../../docs/how-to-make-a-mod/reference/updates.md) | [Descriptor](evejs-launcher.mod.json) | [Loader](loader.js.disabled)

This complete schema-3 loader template demonstrates optional GitHub update metadata. It ships disabled and only logs one line when enabled. It contains no renderer or gameplay implementation.

**Replace the placeholders before importing it.** `YOUR-GITHUB-OWNER/YOUR-MOD-REPOSITORY` is a template value, not a working update feed. The launcher will try to query that declared repository if you import it unchanged. Choose your own mod ID, display name, repository and ZIP prefix; never rely on whoever might own a placeholder repository.

## Adapt it

1. Copy this folder into a disposable workspace. Replace the example identity and update-source placeholders in `evejs-launcher.mod.json`. Replace its example `compatibility.evejsVersions` list with the exact EveJS versions you actually support, or omit `compatibility` when your mod is independent of the EveJS version.
2. Keep the release tag, manifest version and ZIP name consistent. With this asset pattern, tag `v1.0.0` corresponds to manifest version `1.0.0` and attached package `YourMod-1.0.0.zip`.
3. Put the manifest and disabled loader at the package root when creating the ZIP. Include your real implementation and documentation before distributing a mod.
4. Attach `YourMod-1.0.0.update.json` alongside the ZIP. Its schema-1 required fields are `id`, `version` and `asset`, matching the package manifest and ZIP filename. Include `evejsVersions` when the manifest restricts supported EveJS versions; omit it when unrestricted. See the [sidecar contract](../../../docs/how-to-make-a-mod/reference/updates.md#declare-supported-evejs-versions). Publish real releases only when ready. Include user-facing changes in the GitHub release body; the launcher displays that text in its update dialog.
5. A later actual release, for example `v1.0.1`, needs manifest version `1.0.1`, `YourMod-1.0.1.zip` and `YourMod-1.0.1.update.json`. Keep the package ID, kind and update source unchanged. Keep any supported-EveJS-version list consistent between the manifest and sidecar.

An update never installs merely because a check finds it. The gold **Update available** button appears for a release compatible with the selected EveJS installation; the user reads the notes and chooses **Update mod**. This loader requires the Native server to be stopped for replacement. Docker server-package updates currently use the manual workflow.

## Generate the release metadata

After building the example's `YourMod-1.0.0.zip`, run the bundled [generator](../../tools/build_mod_update_metadata.py) from the guide/source bundle root:

```text
python examples/tools/build_mod_update_metadata.py path/to/YourMod-1.0.0.zip
```

This reads the manifest inside the finished ZIP and writes `YourMod-1.0.0.update.json` alongside it. Upload both files to the `v1.0.0` release. The generator refuses to overwrite an existing sidecar; if you change the ZIP, review and replace the previous sidecar before generating it again. Use the real filename prefix you selected in `updates.asset` instead of `YourMod`.

## Configuration and local testing

This minimal example has no settings, so `preserveFiles` is empty. Package-local files declared by a settings schema with `base: "mod"` are preserved automatically. List additional individual config files in `preserveFiles`; private profile data already outside the package needs no entry. See the walkthrough for migration and recovery rules.

For local updater tests, use explicitly labelled test versions in disposable packages and a simulated release feed. Do not publish pretend versions to a real stable feed just to trigger the button. The [DLSS5 worked example](../../../docs/how-to-make-a-mod/reference/updates.md#dlss5--reshade-example) explains how the same update contract applies to a real client package and why legacy 0.5.7 needs a manual first upgrade.
