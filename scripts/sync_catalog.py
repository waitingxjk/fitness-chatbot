"""Import the full iOS text catalog from --source ../flutter-init."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from pydantic import TypeAdapter

from fitness_bot.catalog import DATA_DIR
from fitness_bot.schemas import Exercise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    names = ["exercises_zh_hans.json", "catalog_terms_zh_hans.json"]
    payloads = {name: (source / "assets/catalog" / name).read_bytes() for name in names}
    exercises = TypeAdapter(list[Exercise]).validate_json(payloads[names[0]])
    if not exercises or len({e.id for e in exercises}) != len(exercises):
        raise ValueError("Empty catalog or duplicate IDs")
    versions = {e.catalog_version for e in exercises}
    if len(versions) != 1:
        raise ValueError("Mixed catalog versions")
    license_path = source / "third_party/exercises-dataset/LICENSE"
    license_text = license_path.read_bytes()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in payloads.items():
        (DATA_DIR / name).write_bytes(content)
    (DATA_DIR / "LICENSE.exercises-dataset").write_bytes(license_text)
    notice = source / "third_party/exercises-dataset/NOTICE.md"
    shutil.copyfile(notice, DATA_DIR / "UPSTREAM_NOTICE.md")
    manifest = {
        "source": "flutter-init/assets/catalog",
        "upstream": "https://github.com/hasaneyldrm/exercises-dataset",
        "catalog_version": versions.pop(),
        "exercise_count": len(exercises),
        "sha256": {name: hashlib.sha256(content).hexdigest() for name, content in payloads.items()},
        "media": "Metadata references only; images remain in the iOS app.",
    }
    (DATA_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Imported {len(exercises)} exercises and their terminology into {DATA_DIR}")


if __name__ == "__main__":
    main()
