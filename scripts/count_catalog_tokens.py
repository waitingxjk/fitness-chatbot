"""Measure full-catalog prompt variants locally; does not call a model API."""

import argparse
import json
from pathlib import Path

import tiktoken

from fitness_bot.catalog import DATA_DIR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    encoding = tiktoken.encoding_for_model(args.model)
    items = json.loads((DATA_DIR / "exercises_zh_hans.json").read_text())
    terms = json.loads((DATA_DIR / "catalog_terms_zh_hans.json").read_text())
    labels = {(t["type"], t["code"]): t["displayNameZhHans"] for t in terms}
    variants = {
        "id_name_lines": "\n".join(f"{e['id']}|{e['displayNameZhHans']}" for e in items),
        "id_name_attributes_lines": "\n".join(
            f"{e['id']}|{e['displayNameZhHans']}|"
            f"{labels.get(('bodyPart', e['bodyPartCode']), e['bodyPartCode'])}；"
            f"{labels.get(('equipment', e['equipmentCode']), e['equipmentCode'])}；主要训练"
            f"{labels.get(('muscle', e['targetMuscleCode']), e['targetMuscleCode'])}"
            for e in items
        ),
        "id_name_first_step_lines": "\n".join(
            f"{e['id']}|{e['displayNameZhHans']}|"
            f"{e['instructionStepsZhHans'][0] if e['instructionStepsZhHans'] else ''}"
            for e in items
        ),
        "id_name_full_description_lines": "\n".join(
            f"{e['id']}|{e['displayNameZhHans']}|{' '.join(e['instructionStepsZhHans'])}"
            for e in items
        ),
        "id_name_full_description_json": json.dumps(
            [
                {
                    "id": e["id"],
                    "name": e["displayNameZhHans"],
                    "description": " ".join(e["instructionStepsZhHans"]),
                }
                for e in items
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "current_full_catalog_json_compact": json.dumps(
            items,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    counts = {}
    for name, prompt in variants.items():
        count = len(encoding.encode(prompt))
        counts[name] = {
            "tokens": count,
            "average_tokens_per_exercise": round(count / len(items), 1),
            "utf8_bytes": len(prompt.encode()),
        }
    report = {
        "model": args.model,
        "encoding": encoding.name,
        "exercise_count": len(items),
        "variants": counts,
        "scope": "Catalog content only; excludes messages framing, tools, history and responses.",
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)


if __name__ == "__main__":
    main()
