"""Technical health of the running observer (M15). Read-only; never touches ecology.

Everything here describes the machine and the records, not the world: free disk space,
the last durable (committed) tick, the decision barrier and queue, the provider's own
health, the resource ledger check and recent technical notes. A technical alert can pause
logical time; it never changes a rule, a resource or an organism.
"""

import json
import os
import shutil
import statistics
from contextlib import closing
from pathlib import Path

from genesis.engine.ledger import reconcile

MIN_FREE_BYTES = int(os.environ.get("GENESIS_MIN_FREE_MB", "512")) * 1024 * 1024


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path.parent).free


def disk(path: Path) -> dict:
    usage = shutil.disk_usage(path.parent)
    files = {p.name: p.stat().st_size for p in path.parent.glob(path.name + "*") if p.is_file()}
    return {
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "min_free_bytes": MIN_FREE_BYTES,
        "ok": usage.free >= MIN_FREE_BYTES,
        "database_bytes": sum(files.values()),
    }


def invariants(world) -> dict:
    report = reconcile(world)
    return {
        "ledger_balanced": report.balanced,
        "energy_expected": report.energy_expected,
        "energy_actual": report.energy_actual,
        "material_expected": report.material_expected,
        "material_actual": report.material_actual,
    }


def metrics(store, run_id: str) -> dict:
    """Inference metrics of one run from its recorded decision attempts."""
    with closing(store._connect()) as db:
        rows = db.execute(
            "SELECT parse_status, usage_json FROM decision_attempts WHERE run_id = ?", (run_id,)
        ).fetchall()
        failures = db.execute(
            "SELECT COUNT(*) FROM technical_log WHERE run_id = ? AND kind = 'provider_failure'",
            (run_id,),
        ).fetchone()[0]
    latencies = [json.loads(u).get("latency_ms") for _, u in rows]
    latencies = [x for x in latencies if x is not None]
    return {
        "decisions": len(rows),
        "unparsed": sum(status != "valid" for status, _ in rows),
        "latency_ms_median": statistics.median(latencies) if latencies else None,
        "provider_failures": failures,
    }


def durable_tick(store, run_id: str) -> int | None:
    with closing(store._connect()) as db:
        return db.execute("SELECT MAX(tick) FROM ticks WHERE run_id = ?", (run_id,)).fetchone()[0]


def report(runner) -> dict:
    """The health of the active run and its storage (observer view)."""
    snapshot = runner.snapshot()
    run_id = snapshot.meta.run_id
    store = runner.store
    durable = durable_tick(store, run_id)
    queue = None
    if runner._worker is not None:
        queue = {
            "capacity": runner._worker.capacity,
            "max_depth_seen": runner._worker.max_depth,
        }
    problems = []
    space = disk(store.path)
    if not space["ok"]:
        problems.append("low_disk")
    checks = invariants(snapshot.world)
    if not checks["ledger_balanced"]:
        problems.append("ledger_imbalance")
    if durable != snapshot.world.tick:
        problems.append("durable_tick_mismatch")
    if snapshot.last_error:
        problems.append("technical_pause")
    return {
        "status": "attention" if problems else "ok",
        "problems": problems,
        "run_id": run_id,
        "live_tick": snapshot.world.tick,
        "durable_tick": durable,
        "running": snapshot.status == "running",
        "last_error": snapshot.last_error,
        "pending": snapshot.pending,
        "queue": queue,
        "provider": None if snapshot.provider is None else snapshot.provider.get("health"),
        "disk": space,
        "invariants": checks,
        "metrics": metrics(store, run_id),
        "technical_log": store.technical_log(run_id)[-20:],
    }
