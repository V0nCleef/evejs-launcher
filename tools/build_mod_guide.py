"""Render the shared GitHub/offline walkthrough. Run with --check in CI.

Explanations are translated explicitly; code blocks have one shared source.
The existing detailed contracts remain available as English references.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core.mod_guide import render_page
SOURCE = ROOT / "docs/mod-authoring/guide-content.json"
CATALOG = ROOT / "docs/mod-authoring/navigation.json"
LANGUAGES = ("en", "zh_CN", "ja", "ko", "fr", "de", "nl", "ru")


def outputs() -> dict[Path, str]:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    pages = source["pages"]
    result = {}
    for page in pages:
        for language in LANGUAGES:
            # Validate all offline translations while publishing only English.
            rendered = render_page(source, page, language)
            if language == "en":
                result[ROOT / page["path"]] = rendered
    catalog["pages"] = [{"title": p["title"]["en"], "titles": p["title"], "path": p["path"]}
                        for p in pages]
    result[CATALOG] = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, content in outputs().items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        print("Regenerate the guide with python tools/build_mod_guide.py: " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
