"""Extract character-display names from an existing CCP JSONL SDE export."""
from pathlib import Path
import argparse
import hashlib
import json


def generate(sde: Path, output: Path) -> dict:
    def rows(name):
        with (sde / name).open(encoding="utf-8") as stream:
            for line in stream:
                yield json.loads(line)

    def names(row):
        return {code: value for code, value in row["name"].items()
                if code in {"en", "de", "fr", "ja", "ko", "ru", "zh"} and value}

    groups = {row["_key"] for row in rows("groups.jsonl") if row.get("categoryID") == 6}
    ships = {str(row["_key"]): names(row) for row in rows("types.jsonl") if row.get("groupID") in groups}
    systems = {row["name"]["en"]: names(row) for row in rows("mapSolarSystems.jsonl")}
    source_files = {name: hashlib.sha256((sde / name).read_bytes()).hexdigest()
                    for name in ("groups.jsonl", "types.jsonl", "mapSolarSystems.jsonl")}
    data = {"schemaVersion": 1, "source": "CCP EVE Online Static Data Export", "sourceFiles": source_files,
            "ships": ships, "systems": systems}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sde", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = generate(args.sde, args.output)
    print(json.dumps({"ships": len(result["ships"]), "systems": len(result["systems"]),
                      "Retriever": result["ships"].get("17478"), "Penirgman": result["systems"].get("Penirgman")}))
