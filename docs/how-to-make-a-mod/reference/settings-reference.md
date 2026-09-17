# Settings, profiles and languages

[Guide home](../00-start-here.md) | [Helpers](helpers.md) | [Profile example](../../../examples/mods/profile-options/README.md)

A descriptor's optional `settings` object declares a form and its storage. The launcher renders controls; the helper receives values, not Qt objects. Opening/cancelling performs no target writes. Save validates the draft, checks changed keys against the opened values and commits through key ownership.

## Complete small declaration

```json
{
  "schemaVersion": 1,
  "files": [{"id": "prefs", "base": "profile", "path": "prefs.ini", "format": "ini"}],
  "fields": [{
    "id": "quality", "label": {"en": "Quality", "nl": "Kwaliteit"},
    "description": "A preference for this profile.", "group": "Rendering",
    "type": "integer", "default": 2, "minimum": 0, "maximum": 5, "step": 1,
    "restart": "client", "file": "prefs", "key": ["Graphics", "Quality"]
  }]
}
```

## File bases

| Base | Destination | Form scope | Coordinator |
| --- | --- | --- | --- |
| `mod` | Package directory | Global | Physical EveJS root |
| `evejs` | EveJS root; path starts `config/` | Global | Physical EveJS root |
| `client` | Physical copied EVE client | Global | Physical client |
| `profile` | Private directory for this mod/profile | Profile | Physical client |
| `profile_settings` | Actual EVE text settings for the selected profile | Profile | Physical client |

`profile` never means the shared `tq` junction. The host supplies its private directory as `profile.modDataRoot`. Use it for renderer preferences, logs and caches that must stay separate for two characters. Renderer DLLs remain global even if they consume per-profile preferences.

A form cannot mix EveJS-coordinated and client-coordinated files. Keep them in separate scopes/declarations. Storage anchors remain stable when a first save creates missing directories.

File objects have exactly `id`, `base`, `path`, `format`, optional `encoding`. Declare 1–32 files and 1–256 fields. IDs start alphanumerically and use ASCII letters/digits with `.`, `_`, `-`, up to 64 characters. Relative paths use forward slashes and `.json`, `.ini`, `.yaml`, `.yml`; the format must match. Optional encoding is `utf-8`, `utf-16-le`, `utf-16-be`, `cp1252`. UTF-8/UTF-16 BOMs survive; ANSI encodings are not guessed.

## Fields

Required: `id`, `label`, `type`, `default`, `file`, `key`. Scope follows the file base; there is no `scope` field.

| Property | Meaning |
| --- | --- |
| `type` | `boolean`, `integer`, `number`, `string`, `choice` |
| `label`, `description`, `group` | Text or a locale map with `en` fallback; description/group are optional |
| `advanced` | Optional Boolean, default false |
| `minimum`, `maximum`, `step` | Finite numeric constraints; integer controls need integer values; step is positive |
| `choices` | Choice fields need 1–128 `{ "value": scalar, "label": text-or-locale-map }` entries with distinct typed values |
| `restart` | `none` by default, or `client`, `game_server`, `launcher` |
| `file` | Declared file ID |
| `key` | 1–32 nonempty string components, each at most 128 characters |
| `maxLength` | String limit 1–65,536; default 4,096 |
| `storage` | `native` by default; `numeric_boolean` stores a Boolean as 0/1 |

Defaults pass the same validation as saved values. Invalid existing values produce a load error instead of silently becoming defaults. Numeric INI strings are decoded for numeric fields; string fields stay strings. `numeric_boolean` reads stored 0/1 and supplies a real Boolean to the helper.

Forms support search, groups and advanced fields. Scrolling across numeric/choice controls does not alter their values. Changes remain drafts until Save. Cancel does not install a package or rewrite preferences.

## Languages

Localize labels, descriptions, groups and choice labels. The selected launcher locale is tried first, then `en`. IDs, keys, protocol names, enum values and stored preferences remain stable. A value such as `high` stays the same while its label changes.

Author text is plain text and is not automatically translated as launcher chrome. English-only strings are valid. Maps need `en`, at most 32 locale entries and bounded text. The supported launcher language codes are `en`, `zh_CN`, `ja`, `ko`, `fr`, `de`, `nl` and `ru`.

## Preservation and syntax limits

The engine edits selected key spans and preserves unrelated values, comments, line endings, BOM and encoding. Unchanged semantic values are exact-byte no-ops. Files are capped at 8 MiB. Binary, SQLite and shared GameStore data are not config targets.

- **JSON:** object root, nested object keys, normal JSON values; no duplicate keys, comments or non-finite numbers. The internal engine permits array leaves; public helper replies are scalar-only.
- **INI:** `(key)` or `(section,key)`, case-insensitive lookup with original spelling/comments preserved. Values are single-line scalars. Duplicate keys/sections, targeted multiline values and ambiguous comment strings fail.
- **YAML:** a conservative subset of space-indented block mappings, supported scalars and JSON-compatible flow values. Requested aliases, anchors, tags, block scalars/sequences, merge keys, multiline values and ambiguous implicit scalars fail. Unrelated opaque branches can survive. Quote ambiguous strings such as `on`, `yes` or dates. Multiple YAML documents are unsupported.

Unsafe or unrepresentable edits fail before writes. Use supported syntax or keep that option outside the generated form; do not flatten unfamiliar YAML to satisfy the parser.

## Conflicting settings saves

Saving a changed key that another mod owns, or that was edited after the form
opened, offers a file preview. Keep current settings leaves the file and
ownership records unchanged. Apply my settings records explicit precedence;
the previous mod contribution remains available when the new contribution is
removed. Cancelling leaves the draft open without writing.

Both choices recheck the captured file contents, ownership records and settings
declaration. A stale preview must be reopened. Unknown structural overlaps,
such as competing parent and child JSON keys, are not silently forced through.
