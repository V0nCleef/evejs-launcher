# GitHub mod updates

[Guide home](../00-start-here.md) | [Manifest](manifest.md) | [Settings](settings.md) | [Examples](examples.md)

Add optional update metadata to a schema-3 package to let users find a newer version, read its release notes and choose whether to install it from Mods. Updates come from public GitHub releases. A gold count badge beside Mods announces compatible updates even while another page is open; it clears when none remain. Checking never installs a mod automatically, and mods without this metadata still support manual import.

**Candidate validation:** the real DLSS5 installer updated a disposable copied client from local `0.5.8-update-demo.1` to `0.5.8-update-demo.2`, verified its installed files, and prepared the same profile before and after with `NeuralUplift=0` retained. Disabling restored the original client files. This used simulated release/download responses through the production release selector, parser and download verifier. Separately, a read-only GitHub check found the actual `v0.5.7` release, and the native launcher displayed the update button, changelog dialog and working Cancel action. No fake release was published and no EVE client was started for this update test. This proves the local update/provider path, not a hosted release-to-release download or new rendering session.

**Compatibility validation:** 40 focused tests passed, including choosing the newest compatible release for `0.12.7.1` instead of one requiring `0.12.8`, blocking only constrained updates when the local version is unknown, allowing omitted restrictions, rejecting package/sidecar mismatches and rechecking EveJS immediately before installation. The gold button uses the launcher's existing `UpdateButton` component and was visually verified alongside the gold Mods-sidebar count badge. The final real DLSS5 provider update also passed with the sidecar contract, actual profile preparation and stock restoration.

## Add the metadata

Add this property to your existing `evejs-launcher.mod.json`; this is a fragment, not a complete manifest. This client-only DLSS5 example omits EveJS-version constraints because its own installer checks the EVE client build instead. Declare constraints separately when your mod depends on particular EveJS versions.

```json
"updates": {
  "provider": "github",
  "repository": "V0nCleef/EveJS-DLSS5",
  "asset": "EveJS-DLSS5-{version}.zip",
  "channel": "stable",
  "tagPrefix": "v",
  "preserveFiles": []
}
```

Replace the repository and asset name for your own mod. Keep the mod's `id` stable between versions. Use a SemVer version such as `1.2.3` or `1.3.0-rc.1` in its manifest.

| Field | Meaning |
| --- | --- |
| `provider` | `github`; this contract supports public GitHub releases only |
| `repository` | `owner/repository`, without a URL, branch or release suffix |
| `asset` | Exact attached ZIP filename; `{version}` expands to the release's normalized SemVer without the tag prefix |
| `channel` | `stable` excludes prereleases; `prerelease` permits prereleases as well as stable versions |
| `tagPrefix` | Prefix removed from release tags before comparing versions; use `v` for tags such as `v1.2.3` |
| `preserveFiles` | Additional package-relative mutable files to carry forward; files declared by settings schema with `base: "mod"` are already carried forward |

A tag `v1.2.3` with the asset pattern `MyMod-{version}.zip` selects `MyMod-1.2.3.zip`. The release must contain one exact matching ZIP and its compatibility sidecar described below. Upload the installable mod ZIP, not just GitHub's automatically generated source archive. Draft releases are not update candidates. A newer version must also match the installed mod's identity, package kind and declared update source before installation.

Discovery examines the first 100 GitHub releases. Automatic checks are cached for one hour in the running launcher; **Check mod updates** requests a fresh check. An update that changes the declared source or preservation contract requires manual review/import instead. This prevents a release from silently redirecting future updates or changing which files survive replacement.

The launcher displays the selected release's body as plain text in its update dialog, alongside the installed and available versions. Include useful changes and upgrade notes there; Markdown may appear literally. Launcher controls follow the user's selected language. Your release notes remain in the language you wrote them in.

## Declare supported EveJS versions

When your mod depends on particular EveJS versions, declare them in its manifest:

```json
"compatibility": {"evejsVersions": ["0.12.7.1", "0.12.7.2"]}
```

Entries are exact three- or four-component numeric versions, not ranges. **Omit `compatibility.evejsVersions`, or the entire `compatibility` object, when your mod has no EveJS-version restriction.** No wildcard declaration is needed. This does not waive the mod's other checks: a client-only DLSS5 package can omit EveJS constraints while its installer still strictly verifies the EVE client build and renderer files.

The launcher identifies the selected installation from `config/version.json` (`evejsVersion`) and `package.json` (`version`). If both values are present, they must agree. An unknown or conflicting installation version blocks version-bound offers; updates with no EveJS-version restriction can still be offered.

Each GitHub release must attach a small JSON file beside its ZIP. Replace `.zip` with `.update.json` in the ZIP filename. For example, `EveJS-DLSS5-0.5.8.zip` pairs with `EveJS-DLSS5-0.5.8.update.json`:

```json
{
  "schemaVersion": 1,
  "id": "evejs-dlss5",
  "version": "0.5.8",
  "asset": "EveJS-DLSS5-0.5.8.zip"
}
```

The sidecar must match the ZIP's manifest ID, version and compatibility declaration, and name the exact ZIP asset. Add `"evejsVersions": ["0.12.7.1"]` for a version-bound mod; omit it when the package has no restriction, as in this DLSS5 example. The sidecar itself remains required, even when its version list is omitted, so each new release can declare its own constraints. The launcher reads it before offering or downloading the package, then verifies those declarations again inside the ZIP. Missing or mismatched metadata does not produce an update offer.

Generate it from your finished ZIP with the bundled [metadata generator](../../../examples/tools/build_mod_update_metadata.py):

```text
python examples/tools/build_mod_update_metadata.py path/to/Example-1.0.0.zip
```

The tool reads the actual ZIP's manifest and writes `Example-1.0.0.update.json` beside it. It refuses to overwrite an existing sidecar. Generate metadata after finalizing the ZIP and upload both files to the same release; review an existing sidecar before replacing it after a package change.

The launcher chooses the newest compatible release. It skips a newer release that requires another EveJS version and may offer an older compatible release that is still newer than the installed mod. A compatible available update gets the gold **Update available** button; unsupported releases must not invite installation.

## Keep user configuration

Prefer the launcher's global/profile settings storage and the private mod-data locations described in [Settings](settings.md) and [Ownership](ownership.md). Those locations are outside the replaceable package. Keep their identities stable, and make your helper understand previously saved settings.

Package-local configuration files declared in settings schema with `base: "mod"` are carried forward automatically. Use `preserveFiles` for additional mutable config or preset files inside the package:

```json
"preserveFiles": ["config/preferences.json", "presets/UserPreset.ini"]
```

These paths name regular files, not directories, wildcards, scripts, executables or manifests. Use the normal [relative-path rules](manifest.md#paths). Do not list installation payloads or updater code. The old file is carried forward when present; if it is absent, the new package's default is used.

Reserved launcher manifests cannot be preserved through either mechanism. Configuration preservation must never replace the new version's checked package descriptor.

Preservation copies the existing file; it does not blindly merge new defaults into an old JSON or INI document. Your mod must handle missing new options or perform its own supported config migration. Keep that migration recoverable. Files and presets already outside the package do not belong in `preserveFiles`.

The launcher checks package identity and validates a GitHub asset digest when GitHub supplies one. This needs no launcher-maintained list of mod hashes. A matching download digest verifies downloaded bytes; it does not replace your mod's own target-build and installation checks.

## Runtime and recovery

Native server mods require the server to be stopped before update; client packages require EVE clients to be closed. The launcher does not stop those processes automatically. Docker server-package updates currently require the manual update workflow.

The previous package is retained during replacement. If installation fails, the launcher attempts to restore the previous version and enabled state through the mod's lifecycle provider. If recovery cannot finish, **Recover mod update** exposes the pending operation. Keep the retained package and recovery records until recovery succeeds; do not delete them to clear an error. The mod still owns recovery of its external binary changes.

## DLSS5 + ReShade example

DLSS5 remains a separate combined renderer package. Its helper owns installation, verification and recovery of the physical copied EVE client. Update metadata tells the launcher where to find a new package; it does not move the renderer's implementation into the launcher.

The intended user's path is:

1. A user with legacy **DLSS5 0.5.7** updates the launcher. Their existing mod continues through its legacy integration. It has no update metadata, so it cannot gain an update button merely from that launcher update.
2. The user manually imports the first compatible public DLSS5 package with update metadata, following that package's migration instructions. The real installer must preserve original-file backups and supported preferences.
3. The author later publishes a newer compatible version in the declared GitHub repository, with a matching versioned ZIP, compatibility sidecar and release notes.
4. The launcher finds that newer version and offers an update. The user opens the dialog, reads the changes and chooses to install. Any required client shutdown happens before installation.
5. The update retains supported settings and the previous enabled state. The DLSS5 helper still verifies the physical client and owns its binary recovery. The launcher update button is not permission to overwrite unknown client files.

For example, a **hypothetical** future stable release tagged `v0.5.9` would attach `EveJS-DLSS5-0.5.9.zip`, whose manifest version is `0.5.9`, plus `EveJS-DLSS5-0.5.9.update.json` with the same identity/version and compatibility declaration. This demonstrates naming only; it does not announce that version or claim it is published.

For an opt-in candidate channel, use `"channel": "prerelease"`, a SemVer such as `0.5.9-rc.1`, a matching tag `v0.5.9-rc.1` and asset `EveJS-DLSS5-0.5.9-rc.1.zip`. Mark the GitHub release as a prerelease too. Stable users must not be offered that candidate.

## Test before publishing

Use a disposable installation and copied EVE client for DLSS5 tests. Keep production users and their installed payloads out of an updater experiment.

1. Validate both package manifests with the actual launcher. Give them the same ID, package kind and update source, and different valid versions.
2. For a local updater fixture, the payload may be identical while the two manifests have explicitly labelled test versions. Keep those packages local; do not publish fake versions to the real stable feed to make a button appear.
3. Exercise release selection, ZIP and sidecar names, release notes, cancellation and a completed update. Verify stable/prerelease selection, exact EveJS-version matching, omitted constraints, unknown/conflicting installation versions, and rejection of a mismatched version, ID, source or sidecar. A newer incompatible release must not hide an older compatible update.
4. Change global/profile settings and any declared package-local config before updating. Check those values afterwards, including an absent optional old config file. Test both enabled and disabled packages.
5. Exercise a failed update and recovery. For a client renderer, separately verify actual installation and rendering on a supported copied client. A local release-response fixture proves updater behavior, not public GitHub delivery or renderer compatibility.
6. When a genuine release is ready, verify the published tag, attached ZIP and release body against the installed descriptor. Record hosted delivery separately from local fixture results.

No launcher source edit should be needed for your next mod version. Keep the public manifest/helper contract, publish the correctly named package, and maintain your own compatibility and migration rules.

For a complete minimal package to adapt, see [GitHub Update Demo](../../../examples/mods/github-update-demo/README.md).


### Update progress in the launcher

The release-notes window stays open after **Update mod** is selected. Download progress shows the percentage and bytes received. Checking, backup, installation, verification, and any rollback use named activity stages rather than estimated percentages. Success or error details remain visible until **Close** is selected. Closing the window is blocked while an update or recovery is running. Launcher-owned stage labels are translated into all eight supported interface languages; author release notes remain unchanged. Mod authors do not need additional manifest fields for this display.
