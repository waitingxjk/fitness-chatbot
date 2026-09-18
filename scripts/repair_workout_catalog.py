"""Apply an explicitly reviewed, owner-scoped catalog repair plan. Default: read-only."""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from fitness_bot.catalog import Catalog
from fitness_bot.config import Settings
from fitness_bot.storage import ConversationStore
from fitness_bot.workout_catalog_repair import fingerprint, repaired_workout
from fitness_bot.workout_storage import WorkoutRow


def save_private(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


async def run(plan_path, apply):
    plan = json.loads(plan_path.read_text())
    catalog = Catalog.load()
    if plan["catalog_version"] != catalog.version:
        raise ValueError("Catalog changed; rebuild the reviewed plan")
    for source in plan["source_files"]:
        if hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("Original source image changed")
    url = Settings().database_url.get_secret_value()
    target = make_url(url)
    if (
        target.drivername != "mysql+asyncmy"
        or target.host not in {"localhost", "127.0.0.1"}
        or target.database != "fitness_bot"
    ):
        raise ValueError("Only the configured local fitness_bot MySQL is supported")
    store = ConversationStore(url)
    stamp = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    stamp_file = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    receipt_path = plan_path.parent / f"receipt-{stamp_file}.json"
    try:
        async with store.sessions() as session, session.begin():
            rows = list(
                (
                    await session.scalars(
                        select(WorkoutRow)
                        .where(
                            WorkoutRow.user_id == plan["user_id"],
                            WorkoutRow.source_id == plan["source_id"],
                        )
                        .with_for_update()
                    )
                ).all()
            )
            actual = {row.workout_id: row for row in rows}
            if (
                len(plan["rows"]) != len(actual)
                or {r["workout_id"] for r in plan["rows"]} != actual.keys()
            ):
                raise ValueError("Source workout set changed")
            pending, backup = [], []
            for entry in plan["rows"]:
                row = actual[entry["workout_id"]]
                if (
                    row.deleted
                    or row.version != entry["version"]
                    or fingerprint(row.workout) != entry["sha256"]
                ):
                    raise ValueError("Workout changed or repair already applied; refuses overwrite")
                updated = repaired_workout(
                    row.workout, entry["changes"], catalog, plan["repair_id"], stamp
                )
                backup.append(
                    {
                        key: getattr(row, key)
                        for key in (
                            "user_id",
                            "source_id",
                            "workout_id",
                            "version",
                            "deleted",
                            "workout",
                            "uploaded_at",
                        )
                    }
                )
                if entry["changes"]:
                    pending.append((row, updated, len(entry["changes"])))
            summary = {
                "applied": apply,
                "repair_id": plan["repair_id"],
                "workouts": len(pending),
                "records": sum(n for _, _, n in pending),
                "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            }
            if apply:
                backup_path = plan_path.parent / f"before-repair-{stamp_file}.json"
                save_private(backup_path, {"rows": backup, "plan": plan})
                summary["backup"] = str(backup_path.resolve())
                expected = {}
                for row, updated, _ in pending:
                    row.workout = updated
                    row.version += 1
                    row.uploaded_at = stamp
                    expected[row.workout_id] = (row.version, fingerprint(updated))
                # SQL transaction commits only after all rows and the durable backup are ready.
        if apply:
            async with store.sessions() as session:
                saved = list(
                    (
                        await session.scalars(
                            select(WorkoutRow).where(
                                WorkoutRow.user_id == plan["user_id"],
                                WorkoutRow.source_id == plan["source_id"],
                            )
                        )
                    ).all()
                )
                for row in saved:
                    if row.workout_id in expected:
                        if (row.version, fingerprint(row.workout)) != expected[row.workout_id]:
                            raise ValueError("Post-commit verification mismatch; inspect backup")
            summary["read_back_verified"] = True
            save_private(receipt_path, summary)
            summary["receipt"] = str(receipt_path.resolve())
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        await store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.plan, args.apply))
