# Maintaining the illustrated guide

`guide-content.json` contains the task pages and their explicit prose translations. A `code` block is shared verbatim by every language. The offline reader renders this source directly for translated pages. English Markdown is generated for GitHub; there is no GitHub language switcher.

Run `python tools/build_mod_guide.py` after editing the content. Run it with `--check` to detect stale English pages or missing translations. Translate explanations and button names into natural language; keep literal filenames, paths and code blocks unchanged. Implementation-only explanations can use `detailOnly`. Code examples appear below a horizontal divider at the bottom of the page in both the launcher and GitHub.

Run `python tools/render_mod_guide_images.py` to refresh screenshots. `mod_guide_scenes.py` builds the additional scenes from actual launcher widgets and disposable imported packages. It never reads a player's configuration or starts EveJS. Background events and file contents use explicitly labelled illustrative author views, not invented launcher buttons or claims of live gameplay.

Screenshots are rendered at twice the widget resolution. The reader resamples them smoothly for the page and opens the original in a zoomable image window. Keep the PNG `logicalWidth` metadata so the page does not enlarge an image past its intended display size. The update example sets the real update button to a fictional available release; it never queries or installs an update.

All feature chapters appear in one contents list. Walkthrough links must target a page in that list or an image. Do not link beginner prose to raw source or invisible reference pages. `visuals` maps paragraph indices to screenshots; numbered instructions are split into individual paragraphs before rendering. `afterImages` attaches examples to a complete block. Keep every numbered action illustrated when changing the text.

The optional handoff button copies the selected topic and its explicitly catalogued `handoffFiles`; it never sends anything to an AI service. The example-files page exports only the catalogued example files. Detailed English contracts remain available to the handoff and GitHub readers. The guide window can grow or maximize, with a minimum size of 1140 × 800.

`navigation.json` is the explicit catalog for navigation, local links, resources and PyInstaller packaging. Add new images to `assets`, raw example files to `examples`, and detailed documents to `references`. Do not bundle arbitrary directories. The reader rejects unlisted images and local links.
