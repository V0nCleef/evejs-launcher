"""Data-only walkthrough rendering shared by the offline reader and GitHub export."""
from __future__ import annotations

import os
from pathlib import PurePosixPath
import re


def render_page(source: dict, page: dict, language: str, *, include_code: bool = True) -> str:
    """Translate prose only. Code is shared verbatim across every language."""
    paths = {p["id"]: p["path"] for p in source["pages"]}
    parent = PurePosixPath(page["path"]).parent
    def link(match):
        destination = paths.get(match[1], match[1])
        return os.path.relpath(destination, str(parent)).replace("\\", "/")
    def prose(text):
        text = re.sub(r"@([A-Za-z0-9_./-]+)", link, text)
        return re.sub(r"(!\[[^\]]*\]\(([^)]+)\))",
                      lambda match: match[1] + f"\n\n[{source['ui']['openImage'][language]}]({match[2]})", text)
    parts = ["# " + page["title"][language]]
    if page["id"] != "start":
        parts.append(prose(f"[{source['ui']['home'][language]}](@start)"))
    code_blocks = []
    for block in page["blocks"]:
        if block.get('detailOnly'):
            code_blocks.append(prose(block[language]))
            continue
        if "code" in block:
            code_blocks.append(f"```{block.get('syntax', 'json')}\n{block['code']}\n```")
            continue
        else:
            text = block[language]
            visuals = block.get('visuals', {})
            # Numbered instructions receive a picture beside each individual
            # action, not a gallery at the bottom of the chapter.
            text = re.sub(r'\n(?=\d+\. )', '\n\n', text)
            paragraphs = text.split('\n\n')
            for index, paragraph in enumerate(paragraphs):
                parts.append(prose(paragraph))
                for asset in visuals.get(str(index), []):
                    parts.append(prose(f"![{source['ui']['demoImage'][language]}](@docs/how-to-make-a-mod/images/{asset}.png)"))
        for asset in block.get('afterImages', []):
            parts.append(prose(f"![{source['ui']['demoImage'][language]}](@docs/how-to-make-a-mod/images/{asset}.png)"))
    if include_code and code_blocks:
        parts.extend(['---', '## '+source['ui']['codeHeading'][language], *code_blocks])
    return "\n\n".join(parts) + "\n"


def catalog_paths(catalog: dict) -> list[str]:
    """The single explicit allow-list for navigation, images and packaging."""
    paths = ["docs/how-to-make-a-mod/navigation.json"]
    paths += [row["path"] for key in ("pages", "references") for row in catalog.get(key, [])]
    paths += catalog.get("examples", []) + catalog.get("assets", [])
    paths += ["docs/how-to-make-a-mod/guide-content.json"]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate bundled guide path.")
    for path in paths:
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] not in {"docs", "examples"} or "\\" in path:
            raise ValueError(f"Unsafe bundled guide path: {path}")
    return paths
