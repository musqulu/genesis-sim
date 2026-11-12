"""Verified backups and restore checks (M15). Technical operations only.

A backup is a consistent SQLite copy (the online backup API) written next to the run data
under `backups/`. It is verified before it is reported as good: SQLite's integrity check,
then every run's latest committed tick is rebuilt from the copy's recorded history and its
hash compared with the recorded one. A restore check copies a backup to a *separate* path
and opens it the way the observer would; the live database is never overwritten here.
"""

import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from genesis.persistence.replay import HistoryError, committed_tick
from genesis.persistence.sqlite import SqliteStore, StoreError


class BackupError(RuntimeError):
    pass


def backup_dir(store: SqliteStore) -> Path:
    return store.path.parent / "backups"


def verify(path: Path) -> dict:
    """Integrity check plus replay of every run's latest tick; read-only."""
    result = {"path": str(path), "bytes": path.stat().st_size, "runs": [], "ok": False}
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        result["integrity"] = db.execute("PRAGMA integrity_check").fetchone()[0]
        runs = [r[0] for r in db.execute("SELECT run_id FROM run_manifest ORDER BY created_at")]
    reader = SqliteStore(path)
    failures = []
    for run_id in runs:
        try:
            with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
                tick = committed_tick(db, run_id)
            replayed = reader.history(run_id, tick)
            ok = replayed.world.state_hash() == replayed.recorded_hash
        except (HistoryError, StoreError, sqlite3.Error, ValueError) as exc:
            failures.append({"run_id": run_id, "error": str(exc)[:300]})
            continue
        result["runs"].append({"run_id": run_id, "tick": tick, "hash_ok": ok})
        if not ok:
            failures.append({"run_id": run_id, "error": "replayed hash differs"})
    result["failures"] = failures
    result["ok"] = result["integrity"] == "ok" and not failures
    return result


def create(store: SqliteStore) -> dict:
    """Write and verify a backup; the verification result is stored beside it."""
    folder = backup_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    target = folder / f"genesis-{stamp}.sqlite3"
    with closing(sqlite3.connect(store.path)) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)
    report = {**verify(target), "created_at": datetime.now(UTC).isoformat()}
    target.with_suffix(".verify.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def list_backups(store: SqliteStore) -> list[dict]:
    folder = backup_dir(store)
    out = []
    for path in sorted(folder.glob("genesis-*.sqlite3"), reverse=True):
        sidecar = path.with_suffix(".verify.json")
        report = json.loads(sidecar.read_text()) if sidecar.exists() else {}
        out.append(
            {
                "name": path.name,
                "bytes": path.stat().st_size,
                "created_at": report.get("created_at"),
                "verified_ok": report.get("ok"),
                "runs": len(report.get("runs", [])),
            }
        )
    return out


def restore_check(store: SqliteStore, name: str, target: Path) -> dict:
    """Copy a backup to a separate path and open it as the observer would."""
    source = backup_dir(store) / name
    if not source.is_file() or source.parent != backup_dir(store):
        raise BackupError(f"No backup named {name!r}")
    target = target.resolve()
    if target == store.path.resolve() or target.exists():
        raise BackupError("Restore only to a new, separate path; the live database is kept")
    shutil.copyfile(source, target)
    restored = SqliteStore(target)
    restored.initialize()
    loaded = restored.load_active()
    return {
        "path": str(target),
        "active_run": loaded.meta.run_id,
        "tick": loaded.world.tick,
        "state_hash": loaded.world.state_hash(),
        "ok": True,
    }


def main(argv: list[str]) -> int:
    """Command line: verify a backup, or restore-check one to a separate path.

    uv run python -m genesis.ops.recovery verify data/backups/genesis-<stamp>.sqlite3
    uv run python -m genesis.ops.recovery restore genesis-<stamp>.sqlite3 /path/new.sqlite3
    """
    import os

    if len(argv) >= 2 and argv[0] == "verify":
        result = verify(Path(argv[1]))
        print(json.dumps(result, indent=1))
        return 0 if result["ok"] else 1
    if len(argv) >= 3 and argv[0] == "restore":
        default = Path(__file__).resolve().parents[3] / "data" / "genesis.sqlite3"
        live = SqliteStore(Path(os.environ.get("GENESIS_DB", default)))
        print(json.dumps(restore_check(live, argv[1], Path(argv[2])), indent=1))
        return 0
    print(main.__doc__)
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
