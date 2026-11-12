"""SQLite persistence: one transaction per tick; nothing is published before commit.

Committed history (manifest, events, ticks) is authoritative. The `cells` projection and
snapshots are rebuildable conveniences that are verified against it on every load.
"""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from genesis import __version__
from genesis.domain.config import RNG_ALGORITHM, RULES_VERSION, LifeConfig, WorldConfig
from genesis.domain.events import WorldEvent
from genesis.domain.state import Cell, World  # noqa: F401
from genesis.engine.ledger import InvariantViolation
from genesis.engine.world import TickResult, birth_events, create_world, creation_event
from genesis.persistence import events as event_store
from genesis.persistence import snapshots
from genesis.persistence.manifest import Manifest, canonical_json, sha256
from genesis.persistence.replay import (
    HistoryError,
    HistoryUnavailable,
    ReplayResult,
    committed_tick,
    initial_world,
    reconstruct,
)

SCHEMA_VERSION = 7  # database layout (PRAGMA user_version)
STATE_SCHEMA = 12  # state/hash schema for newly created runs
MANIFEST_VERSION = 3
SCHEMA_SQL = (Path(__file__).with_name("schema.sql")).read_text()

# WAL-reset corruption bug: affects SQLite 3.7.0 through 3.51.2; fixed in 3.51.3 and in
# the 3.50.7 and 3.44.6 backports (docs/RESEARCH.md).
_WAL_FIXED = ((3, 51, 3), (3, 50, 7), (3, 44, 6))

log = logging.getLogger("genesis.store")


def _without_lifespan(record: dict) -> dict:
    """A born organism's record as shown to the observer: its sampled lifespan stays hidden
    here (the organism view reveals it only after death)."""
    return {**record, "lifespan": None}


def interaction(actor: str, summary: dict) -> dict:
    """An accepted transfer or message, as drawn on the grid (observer view)."""
    return {
        "kind": summary["action"],
        "from_id": actor,
        "to_id": summary["target"],
        "from_x": summary["from_x"],
        "from_y": summary["from_y"],
        "to_x": summary["to_x"],
        "to_y": summary["to_y"],
        "detail": f"{summary['amount']} {summary['resource']}"
        if summary["action"] == "transfer"
        else f"born, endowment {summary['endowment']} energy"
        if summary["action"] == "reproduce"
        else summary["text"][:80],
    }


class StoreError(RuntimeError):
    """Stored history cannot safely be used; never replace it with a fresh run."""


def wal_runtime_safe(version: tuple[int, int, int] = sqlite3.sqlite_version_info) -> bool:
    if version >= (3, 51, 3):
        return True
    return any(version[:2] == fixed[:2] and version >= fixed for fixed in _WAL_FIXED[1:])


def source_hash() -> str:
    """Fingerprint the actual Python source even before the first Git commit."""
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RunMeta:
    run_id: str
    created_at: datetime
    engine_version: str
    engine_source_hash: str
    initial_state_hash: str
    state_schema: int
    manifest_version: int
    manifest_hash: str
    rules_version: str
    provider_profile: dict | None = None  # sealed inference profile (live model runs)


@dataclass(frozen=True, slots=True)
class RestoreInfo:
    """How the live state was rebuilt at load time (technical metadata)."""

    source: str
    snapshot_tick: int | None
    ticks_replayed: int
    events_applied: int
    skipped_snapshots: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class LoadedRun:
    meta: RunMeta
    world: World
    started: bool
    last_event_sequence: int
    restore: RestoreInfo | None = None


@dataclass(frozen=True, slots=True)
class ControlRecord:
    command_id: str
    command: str
    params: dict
    logical_tick: int


@dataclass(frozen=True, slots=True)
class DecisionAttempt:
    """One consumed request: its exact input binding, raw output, parse and outcome."""

    request_id: str
    tick: int
    actor_id: str
    input_hash: str
    provider: dict
    raw_output: str
    parse_status: str
    parse_error: str | None
    proposal: dict | None
    outcome: str
    outcome_rule: str
    outcome_reason: str | None
    usage: dict


@dataclass(frozen=True, slots=True)
class _RunRecord:
    meta: RunMeta
    manifest: Manifest
    initial: World


def _legacy_manifest(row: tuple) -> Manifest:
    """Seal an M02 (schema 2) run's existing columns without changing any of them."""
    run_id, created, schema, engine, src, rules, rng, config, cells, init_hash = row
    return Manifest.seal(
        {
            "manifest_version": 1,
            "sealed_by": "migration 2->3",
            "run_id": run_id,
            "created_at": created,
            "state_schema": schema,
            "engine_version": engine,
            "engine_source_hash": src,
            "rules_version": rules,
            "rng_algorithm": rng,
            "config": json.loads(config),
            "initial_cells_sha256": sha256(cells),
            "initial_state_hash": init_hash,
            "initial_rng_state_sha256": None,
        }
    )


class SqliteStore:
    def __init__(self, path: Path):
        self.path = path
        self.journal_mode = "delete"
        self.journal_note = "not initialized"
        self._records: dict[str, _RunRecord] = {}

    # -- connections and transactions -------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA synchronous = FULL")
        return db

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """One consistent read snapshot across tables; never writes."""
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            try:
                yield db
            finally:
                db.execute("ROLLBACK")

    # -- setup ------------------------------------------------------------------------
    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 2, 3, 4, 5, 6, SCHEMA_VERSION):
            # Do not touch a database written by an unknown or newer layout.
            raise StoreError(f"Unsupported database schema version {version}")
        self._select_journal_mode()
        with self._transaction() as db:
            for statement in SCHEMA_SQL.split(";\n"):
                if statement.strip():
                    db.execute(statement)
            if version == 2:
                self._migrate_v2(db)
            if version in (2, 3):
                self._migrate_v3(db)
            if version in (2, 3, 4):
                db.execute(
                    "INSERT OR IGNORE INTO schema_migrations VALUES (4, 5, ?, ?)",
                    (
                        datetime.now(UTC).isoformat(),
                        "Added request_records and decision_attempts (M06). Additive only; "
                        "no history was rewritten.",
                    ),
                )
            if version in (2, 3, 4, 5):
                db.execute(
                    "INSERT OR IGNORE INTO schema_migrations VALUES (5, 6, ?, ?)",
                    (
                        datetime.now(UTC).isoformat(),
                        "Added event_memory_changes (M08). Additive only; existing runs keep "
                        "their state schema and no history was rewritten.",
                    ),
                )
            if version in (2, 3, 4, 5, 6):
                db.execute(
                    "INSERT OR IGNORE INTO schema_migrations VALUES (6, 7, ?, ?)",
                    (
                        datetime.now(UTC).isoformat(),
                        "Added event_message_changes (M09). Additive only; existing runs keep "
                        "their state schema and no history was rewritten.",
                    ),
                )
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            active = db.execute("SELECT run_id FROM active_sim_run").fetchone()
            if active is None:
                if db.execute("SELECT COUNT(*) FROM run_manifest").fetchone()[0]:
                    raise StoreError("Active run reference is missing; runs were preserved")
                self._insert_run(db, WorldConfig.for_run(42))
        self.load_active()  # Rejects corrupt or incompatible state without overwriting it.

    def _migrate_v2(self, db: sqlite3.Connection) -> None:
        columns = {row[1] for row in db.execute("PRAGMA table_info(run_manifest)")}
        for name, declaration in (
            ("manifest_version", "INTEGER NOT NULL DEFAULT 1"),
            ("manifest_json", "TEXT"),
            ("manifest_hash", "TEXT"),
            ("initial_rng_state", "TEXT"),
        ):
            if name not in columns:
                db.execute(f"ALTER TABLE run_manifest ADD COLUMN {name} {declaration}")
        rows = db.execute(
            "SELECT run_id, created_at, schema_version, engine_version, engine_source_hash, "
            "rules_version, rng_algorithm, config_json, initial_cells_json, initial_state_hash "
            "FROM run_manifest WHERE manifest_json IS NULL"
        ).fetchall()
        for row in rows:
            manifest = _legacy_manifest(row)
            db.execute(
                "UPDATE run_manifest SET manifest_version = 1, manifest_json = ?, "
                "manifest_hash = ? WHERE run_id = ?",
                (manifest.text, manifest.digest, row[0]),
            )
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES (2, 3, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                f"Added manifest/snapshot/technical tables; sealed {len(rows)} existing run "
                "manifest(s) as version 1. No events, ticks or cells were rewritten.",
            ),
        )

    def _migrate_v3(self, db: sqlite3.Connection) -> None:
        """M03 -> M04: organism tables and event actor/payload columns. Additive only."""
        added = []
        for table, name, declaration in (
            ("run_manifest", "initial_organisms_json", "TEXT"),
            ("events", "actor_id", "TEXT"),
            ("events", "payload_json", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if name not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                added.append(f"{table}.{name}")
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES (3, 4, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                "Added organism/projection tables and columns "
                + ", ".join(added)
                + ". Existing runs keep their state schema; no history was rewritten.",
            ),
        )

    def _select_journal_mode(self) -> None:
        """Enable WAL only if this SQLite runtime is a fixed build and the file supports it."""
        version = ".".join(map(str, sqlite3.sqlite_version_info))
        if not wal_runtime_safe():
            self.journal_note = f"SQLite {version} lacks the WAL-reset fix; using rollback journal"
        else:
            with closing(self._connect()) as db:
                mode = db.execute("PRAGMA journal_mode = WAL").fetchone()[0].lower()
            if mode == "wal":
                self.journal_mode, self.journal_note = "wal", f"SQLite {version}: WAL verified"
                return
            self.journal_note = f"SQLite {version}: WAL unavailable for this file ({mode})"
        with closing(self._connect()) as db:
            self.journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0].lower()

    # -- runs -------------------------------------------------------------------------
    def _insert_run(
        self,
        db: sqlite3.Connection,
        config: WorldConfig,
        life: LifeConfig | None = None,
        provider_profile: dict | None = None,
    ) -> str:
        world = create_world(config, life)
        run_id = str(uuid4())
        created = datetime.now(UTC).isoformat()
        source = source_hash()
        cells_json = json.dumps(
            [[c.energy, c.loose_material, c.packed_material] for c in world.cells],
            separators=(",", ":"),
        )
        organisms_json = canonical_json([o.to_dict(world.schema) for o in world.organisms])
        manifest = Manifest.seal(
            {
                "manifest_version": MANIFEST_VERSION,
                "run_id": run_id,
                "created_at": created,
                "state_schema": STATE_SCHEMA,
                "db_schema": SCHEMA_VERSION,
                "engine_version": __version__,
                "engine_source_hash": source,
                "rules_version": RULES_VERSION,
                "rng_algorithm": RNG_ALGORITHM,
                "config": asdict(config),
                "initial_cells_sha256": sha256(cells_json),
                "initial_rng_state_sha256": sha256(world.rng_state),
                "initial_state_hash": world.state_hash(),
                "snapshot_format": snapshots.FORMAT_VERSION,
                "snapshot_interval": snapshots.SNAPSHOT_INTERVAL,
                "life": world.life.to_dict(world.schema),
                "initial_organisms_sha256": sha256(organisms_json),
                **({"provider_profile": provider_profile} if provider_profile else {}),
            }
        )
        db.execute(
            "INSERT INTO run_manifest (run_id, created_at, schema_version, engine_version, "
            "engine_source_hash, rules_version, rng_algorithm, config_json, initial_cells_json, "
            "initial_state_hash, manifest_version, manifest_json, manifest_hash, "
            "initial_rng_state, initial_organisms_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                created,
                STATE_SCHEMA,
                __version__,
                source,
                RULES_VERSION,
                RNG_ALGORITHM,
                canonical_json(asdict(config)),
                cells_json,
                world.state_hash(),
                MANIFEST_VERSION,
                manifest.text,
                manifest.digest,
                world.rng_state,
                organisms_json,
            ),
        )
        db.executemany(
            "INSERT INTO organisms VALUES (?,?,?)",
            [(run_id, o.id, canonical_json(o.to_dict(world.schema))) for o in world.organisms],
        )
        db.executemany(
            "INSERT INTO cells VALUES (?,?,?,?,?,?,?)",
            [
                (run_id, i, c.x, c.y, c.energy, c.loose_material, c.packed_material)
                for i, c in enumerate(world.cells)
            ],
        )
        db.execute("INSERT INTO ticks VALUES (?,?,?)", (run_id, 0, world.state_hash()))
        sequence = 0
        for event in [creation_event(world), *birth_events(world)]:
            sequence += 1
            self._insert_event(db, run_id, sequence, event, world.config.width)
        snapshots.write(db, run_id, world, "initial", sequence)
        db.execute("INSERT OR REPLACE INTO active_sim_run VALUES (1, ?)", (run_id,))
        return run_id

    def create_run(
        self,
        config: WorldConfig,
        life: LifeConfig | None = None,
        provider_profile: dict | None = None,
    ) -> LoadedRun:
        with self._transaction() as db:
            run_id = self._insert_run(db, config, life, provider_profile)
        return self.load_run(run_id)

    def load_active(self) -> LoadedRun:
        with closing(self._connect()) as db:
            row = db.execute("SELECT run_id FROM active_sim_run").fetchone()
        if row is None:
            raise StoreError("Active run reference is missing; no replacement was created")
        return self.load_run(row[0])

    def _record(self, db: sqlite3.Connection, run_id: str) -> _RunRecord:
        """Verified manifest and initial state (cached; both are immutable)."""
        if run_id in self._records:
            return self._records[run_id]
        row = db.execute(
            "SELECT created_at, schema_version, config_json, initial_cells_json, "
            "initial_state_hash, manifest_version, manifest_json, manifest_hash, "
            "initial_rng_state, initial_organisms_json FROM run_manifest WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise StoreError("Run manifest is missing; original data was preserved")
        (created, schema, config_json, cells_json, init_hash, mversion, mtext, mhash, rng, orgs) = (
            row
        )
        try:
            manifest = Manifest.verify(mtext, mhash)
            data = manifest.data
            if (
                data["run_id"] != run_id
                or data["manifest_version"] != mversion
                or data["state_schema"] != schema
                or data["initial_state_hash"] != init_hash
                or data["config"] != json.loads(config_json)
            ):
                raise ValueError("Manifest disagrees with the run's stored columns")
            initial = initial_world(manifest, cells_json, rng, orgs)
        except (ValueError, TypeError, KeyError, HistoryError) as exc:
            raise StoreError(f"Run manifest failed validation: {exc}") from exc
        meta = RunMeta(
            run_id=run_id,
            created_at=datetime.fromisoformat(created),
            engine_version=data["engine_version"],
            engine_source_hash=data["engine_source_hash"],
            initial_state_hash=init_hash,
            state_schema=schema,
            manifest_version=mversion,
            manifest_hash=mhash,
            rules_version=data["rules_version"],
            provider_profile=data.get("provider_profile"),
        )
        record = _RunRecord(meta, manifest, initial)
        self._records[run_id] = record
        return record

    def load_run(self, run_id: str) -> LoadedRun:
        """Rebuild the run from history (latest valid snapshot + recorded tail).

        The rebuilt state must match the recorded tick hash and the cell projection;
        otherwise loading stops with StoreError and nothing is changed.
        """
        with self._read() as db:
            record = self._record(db, run_id)
            try:
                tick = committed_tick(db, run_id)
                result = reconstruct(
                    db, run_id, tick, record.manifest, record.initial, verify_each_tick=True
                )
            except (HistoryError, InvariantViolation) as exc:
                raise StoreError(f"Recorded history failed verification: {exc}") from exc
            projection = db.execute(
                "SELECT idx, x, y, energy, loose_material, packed_material FROM cells "
                "WHERE run_id = ? ORDER BY idx",
                (run_id,),
            ).fetchall()
            organism_rows = db.execute(
                "SELECT organism_id, record_json FROM organisms WHERE run_id = ? "
                "ORDER BY organism_id",
                (run_id,),
            ).fetchall()
            started = db.execute(
                "SELECT 1 FROM control_log WHERE run_id = ? AND command = 'start'", (run_id,)
            ).fetchone()
            last_sequence = event_store.last_sequence(db, run_id)
        expected = [
            (i, c.x, c.y, c.energy, c.loose_material, c.packed_material)
            for i, c in enumerate(result.world.cells)
        ]
        if projection != expected:
            raise StoreError(
                "Committed cell state disagrees with recorded history; original data preserved"
            )
        if organism_rows != [
            (o.id, canonical_json(o.to_dict(result.world.schema))) for o in result.world.organisms
        ]:
            raise StoreError(
                "Committed organism state disagrees with recorded history; data preserved"
            )
        if last_sequence != result.last_sequence:
            raise StoreError("Events exist beyond the last committed tick; data preserved")
        for skipped_tick, reason in result.skipped_snapshots:
            self._technical(run_id, "snapshot_invalid", f"tick {skipped_tick}", reason)
        restore = RestoreInfo(
            source=result.source,
            snapshot_tick=result.snapshot_tick,
            ticks_replayed=result.ticks_replayed,
            events_applied=result.events_applied,
            skipped_snapshots=result.skipped_snapshots,
        )
        return LoadedRun(record.meta, result.world, started is not None, last_sequence, restore)

    def history(self, run_id: str, tick: int, *, use_snapshots: bool = True) -> ReplayResult:
        """Read-only reconstruction of an earlier tick from recorded data."""
        with self._read() as db:
            record = self._record(db, run_id)
            try:
                return reconstruct(
                    db, run_id, tick, record.manifest, record.initial, use_snapshots=use_snapshots
                )
            except HistoryUnavailable:
                raise
            except InvariantViolation as exc:
                raise HistoryError(f"Replayed ledger does not reconcile: {exc}") from exc

    def initial_cells(self, run_id: str) -> list[list[int]]:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT initial_cells_json FROM run_manifest WHERE run_id = ?", (run_id,)
            ).fetchone()
        return json.loads(row[0])

    # -- snapshots and technical notes --------------------------------------------------
    def write_snapshot(self, run_id: str, world: World, reason: str) -> bool:
        """Snapshot a committed tick. Refuses state that was never committed."""
        with self._transaction() as db:
            recorded = db.execute(
                "SELECT state_hash FROM ticks WHERE run_id = ? AND tick = ?", (run_id, world.tick)
            ).fetchone()
            if recorded is None or recorded[0] != world.state_hash():
                raise StoreError("Refusing to snapshot state that is not the committed tick")
            sequence = event_store.last_sequence(db, run_id, world.tick)
            return snapshots.write(db, run_id, world, reason, sequence)

    def list_snapshots(self, run_id: str) -> list[dict]:
        with self._read() as db:
            rows = snapshots.list_rows(db, run_id)
        return [
            {
                "tick": tick,
                "reason": reason,
                "created_at": created,
                "checksum_ok": sha256(payload) == checksum and fmt in snapshots.SUPPORTED_FORMATS,
            }
            for tick, fmt, reason, _seq, _hash, payload, checksum, created in rows
        ]

    def _technical(self, run_id: str | None, kind: str, subject: str, detail: str) -> None:
        log.warning("%s %s: %s", kind, subject, detail)
        try:
            with self._transaction() as db:
                db.execute(
                    "INSERT OR IGNORE INTO technical_log (run_id, at, kind, subject, detail) "
                    "VALUES (?,?,?,?,?)",
                    (run_id, datetime.now(UTC).isoformat(), kind, subject, detail),
                )
        except sqlite3.Error:
            log.exception("Could not record technical note")

    def technical_log(self, run_id: str) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT at, kind, subject, detail FROM technical_log WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(zip(("at", "kind", "subject", "detail"), r, strict=True)) for r in rows]

    # -- tick commit ------------------------------------------------------------------
    @staticmethod
    def _insert_event(
        db: sqlite3.Connection, run_id: str, sequence: int, event: WorldEvent, width: int
    ) -> None:
        event_store.insert_event(db, run_id, sequence, event, width)

    def _write_events(self, db: sqlite3.Connection, run_id: str, result: TickResult) -> int:
        sequence = event_store.last_sequence(db, run_id)
        for event in result.events:
            sequence += 1
            self._insert_event(db, run_id, sequence, event, result.world.config.width)
        return sequence

    def _write_state(self, db: sqlite3.Connection, run_id: str, result: TickResult) -> None:
        width = result.world.config.width
        db.executemany(
            "UPDATE cells SET energy = ?, loose_material = ?, packed_material = ? "
            "WHERE run_id = ? AND idx = ?",
            [
                (
                    cell.energy,
                    cell.loose_material,
                    cell.packed_material,
                    run_id,
                    cell.y * width + cell.x,
                )
                for cell in self._changed_cells(result)
            ],
        )
        changed = {c.organism_id for e in result.events for c in e.organism_changes}
        db.executemany(
            "INSERT OR REPLACE INTO organisms VALUES (?,?,?)",
            [
                (run_id, o.id, canonical_json(o.to_dict(result.world.schema)))
                for o in result.world.organisms
                if o.id in changed
            ],
        )
        db.execute(
            "INSERT INTO ticks VALUES (?,?,?)",
            (run_id, result.world.tick, result.world.state_hash()),
        )

    @staticmethod
    def _changed_cells(result: TickResult) -> list[Cell]:
        width = result.world.config.width
        indices = {c.y * width + c.x for e in result.events for c in e.changes}
        return [result.world.cells[i] for i in sorted(indices)]

    def _write_control(self, db: sqlite3.Connection, run_id: str, control: ControlRecord) -> None:
        db.execute(
            "INSERT INTO control_log (run_id, command_id, command, params_json, logical_tick, "
            "wall_time) VALUES (?,?,?,?,?,?)",
            (
                run_id,
                control.command_id,
                control.command,
                json.dumps(control.params, sort_keys=True),
                control.logical_tick,
                datetime.now(UTC).isoformat(),
            ),
        )

    def commit_tick(
        self,
        run_id: str,
        result: TickResult,
        control: ControlRecord | None = None,
        attempts: tuple[DecisionAttempt, ...] = (),
    ) -> int:
        """Atomically persist events, deltas, ledger entries, state and tick hash.

        Returns the last event sequence. Any failure rolls everything back.
        """
        with self._transaction() as db:
            last = db.execute("SELECT MAX(tick) FROM ticks WHERE run_id = ?", (run_id,)).fetchone()[
                0
            ]
            if last != result.world.tick - 1:
                raise StoreError(f"Tick {result.world.tick} does not follow committed tick {last}")
            sequence = self._write_events(db, run_id, result)
            self._write_state(db, run_id, result)
            if snapshots.due(result.world):
                snapshots.write(db, run_id, result.world, "periodic", sequence)
            if control is not None:
                self._write_control(db, run_id, control)
            self._write_attempts(db, run_id, result, attempts)
            return sequence

    @staticmethod
    def _write_attempts(
        db: sqlite3.Connection,
        run_id: str,
        result: TickResult,
        attempts: tuple[DecisionAttempt, ...],
    ) -> None:
        """Consume each request exactly once, linked to the event holding its outcome."""
        if not attempts:
            return
        first = db.execute(
            "SELECT MIN(sequence) FROM events WHERE run_id = ? AND tick = ?",
            (run_id, result.world.tick),
        ).fetchone()[0]
        outcome_events = {
            e.actor_id: first + i
            for i, e in enumerate(result.events)
            if e.type in ("action_accepted", "action_rejected")
        }
        for a in attempts:
            if a.tick != result.world.tick:
                raise StoreError("Decision attempt does not belong to this tick")
            db.execute(
                "INSERT INTO decision_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    a.request_id,
                    a.tick,
                    a.actor_id,
                    a.input_hash,
                    canonical_json(a.provider),
                    a.raw_output,
                    a.parse_status,
                    a.parse_error,
                    None if a.proposal is None else canonical_json(a.proposal),
                    a.outcome,
                    a.outcome_rule,
                    a.outcome_reason,
                    outcome_events[a.actor_id],
                    canonical_json(a.usage),
                ),
            )
            updated = db.execute(
                "UPDATE request_records SET status = 'consumed', updated_at = ? "
                "WHERE run_id = ? AND request_id = ? AND input_hash = ? "
                "AND status = 'completed'",
                (datetime.now(UTC).isoformat(), run_id, a.request_id, a.input_hash),
            ).rowcount
            if updated != 1:
                raise StoreError(f"Request {a.request_id} was not completed and unconsumed")

    # -- request inbox (technical) ---------------------------------------------------------
    def record_requests(self, run_id: str, requests: list) -> dict[str, dict]:
        """Persist requests before dispatch. Returns already-completed responses that match
        exactly (same request id and input hash) so recovery never asks twice. Rows for
        the same request id with different input are abandoned, not deleted."""
        now = datetime.now(UTC).isoformat()
        reused: dict[str, dict] = {}
        with self._transaction() as db:
            for request in requests:
                db.execute(
                    "UPDATE request_records SET status = 'abandoned', updated_at = ? "
                    "WHERE run_id = ? AND request_id = ? AND input_hash != ? "
                    "AND status IN ('requested', 'completed', 'failed')",
                    (now, run_id, request.request_id, request.input_hash),
                )
                row = db.execute(
                    "SELECT status, response_raw, response_meta_json FROM request_records "
                    "WHERE run_id = ? AND request_id = ? AND input_hash = ?",
                    (run_id, request.request_id, request.input_hash),
                ).fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO request_records (run_id, request_id, input_hash, tick, "
                        "actor_id, request_json, status, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,'requested',?,?)",
                        (
                            run_id,
                            request.request_id,
                            request.input_hash,
                            request.target_tick,
                            request.actor_id,
                            canonical_json(request.to_dict()),
                            now,
                            now,
                        ),
                    )
                elif row[0] == "completed":
                    reused[request.request_id] = {
                        "raw": row[1],
                        "meta": json.loads(row[2] or "{}"),
                    }
                elif row[0] == "consumed":
                    raise StoreError(f"Request {request.request_id} was already consumed")
                elif row[0] == "failed":
                    db.execute(
                        "UPDATE request_records SET status = 'requested', updated_at = ? "
                        "WHERE run_id = ? AND request_id = ? AND input_hash = ?",
                        (now, run_id, request.request_id, request.input_hash),
                    )
        return reused

    def complete_request(self, run_id: str, request, raw: str, meta: dict) -> bool:
        """Store a response durably. False for a stale or duplicate response (ignored)."""
        with self._transaction() as db:
            updated = db.execute(
                "UPDATE request_records SET status = 'completed', response_raw = ?, "
                "response_meta_json = ?, attempts = attempts + 1, updated_at = ? "
                "WHERE run_id = ? AND request_id = ? AND input_hash = ? "
                "AND status = 'requested'",
                (
                    raw,
                    canonical_json(meta),
                    datetime.now(UTC).isoformat(),
                    run_id,
                    request.request_id,
                    request.input_hash,
                ),
            ).rowcount
        return updated == 1

    def fail_request(self, run_id: str, request, error: str) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE request_records SET status = 'failed', error = ?, "
                "attempts = attempts + 1, updated_at = ? "
                "WHERE run_id = ? AND request_id = ? AND input_hash = ? "
                "AND status = 'requested'",
                (
                    error,
                    datetime.now(UTC).isoformat(),
                    run_id,
                    request.request_id,
                    request.input_hash,
                ),
            )

    def abandon_requests(self, run_id: str, reason: str) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE request_records SET status = 'abandoned', error = ?, updated_at = ? "
                "WHERE run_id = ? AND status IN ('requested', 'completed', 'failed')",
                (reason, datetime.now(UTC).isoformat(), run_id),
            )

    def request_status(self, run_id: str) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT request_id, tick, actor_id, status, attempts, error FROM request_records "
                "WHERE run_id = ? ORDER BY tick, request_id",
                (run_id,),
            ).fetchall()
        return [
            dict(
                zip(
                    ("request_id", "tick", "actor_id", "status", "attempts", "error"),
                    r,
                    strict=True,
                )
            )
            for r in rows
        ]

    def usage_totals(self, run_id: str, actors: tuple[str, ...] | None = None) -> tuple[int, int]:
        """Consumed decision attempts and their known tokens (for API budgets on reload),
        for the whole run or only the given organisms."""
        where, args = "run_id = ?", [run_id]
        if actors is not None:
            where += f" AND actor_id IN ({','.join('?' * len(actors))})" if actors else " AND 0"
            args += list(actors)
        with closing(self._connect()) as db:
            count, tokens = db.execute(
                "SELECT COUNT(*), COALESCE(SUM("
                "COALESCE(json_extract(usage_json, '$.input_tokens'), 0) + "
                "COALESCE(json_extract(usage_json, '$.output_tokens'), 0)), 0) "
                f"FROM decision_attempts WHERE {where}",
                args,
            ).fetchone()
        return int(count), int(tokens)

    def previous_results(self, run_id: str) -> dict[str, dict]:
        """Each actor's latest committed decision outcome (input for its next context)."""
        from genesis.cognition.context import previous_result_from

        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT e.actor_id, e.type, e.rule_id, e.summary_json, e.tick FROM events e "
                "JOIN (SELECT actor_id, MAX(sequence) AS seq FROM events WHERE run_id = ? "
                "AND type IN ('action_accepted', 'action_rejected') GROUP BY actor_id) last "
                "ON e.sequence = last.seq WHERE e.run_id = ?",
                (run_id, run_id),
            ).fetchall()
        return {
            actor: previous_result_from(type_, rule, json.loads(summary), tick)
            for actor, type_, rule, summary, tick in rows
        }

    def list_decisions(
        self,
        run_id: str,
        actor_id: str | None = None,
        limit: int = 20,
        max_tick: int | None = None,
    ) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT {self._DECISION_COLUMNS} FROM decision_attempts "
                "WHERE run_id = ? AND (? IS NULL OR actor_id = ?) "
                "AND (? IS NULL OR tick <= ?) ORDER BY tick DESC, request_id LIMIT ?",
                (run_id, actor_id, actor_id, max_tick, max_tick, limit),
            ).fetchall()
        return [self._decision_row(r) for r in rows]

    def get_decision(self, run_id: str, request_id: str) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute(
                f"SELECT {self._DECISION_COLUMNS} FROM decision_attempts "
                "WHERE run_id = ? AND request_id = ?",
                (run_id, request_id),
            ).fetchone()
            request = db.execute(
                "SELECT request_json, response_meta_json FROM request_records "
                "WHERE run_id = ? AND request_id = ? AND status = 'consumed'",
                (run_id, request_id),
            ).fetchone()
        if row is None:
            return None
        decision = self._decision_row(row)
        decision["request"] = None if request is None else json.loads(request[0])
        meta = {} if request is None else json.loads(request[1] or "{}")
        decision["prompt"] = (meta.get("detail") or {}).get("messages")
        decision["detail"] = {
            k: v for k, v in (meta.get("detail") or {}).items() if k != "messages"
        }
        return decision

    _DECISION_COLUMNS = (
        "request_id, tick, actor_id, input_hash, provider_json, raw_output, parse_status, "
        "parse_error, proposal_json, outcome, outcome_rule, outcome_reason, event_sequence, "
        "usage_json"
    )

    @staticmethod
    def _decision_row(row: tuple) -> dict:
        (
            rid,
            tick,
            actor,
            digest,
            provider,
            raw,
            status,
            error,
            proposal,
            outcome,
            rule,
            reason,
            sequence,
            usage,
        ) = row
        return {
            "request_id": rid,
            "tick": tick,
            "actor_id": actor,
            "input_hash": digest,
            "provider": json.loads(provider),
            "raw_output": raw,
            "parse_status": status,
            "parse_error": error,
            "proposal": None if proposal is None else json.loads(proposal),
            "outcome": outcome,
            "outcome_rule": rule,
            "outcome_reason": reason,
            "event_sequence": sequence,
            "usage": json.loads(usage),
        }

    def log_control(self, run_id: str, control: ControlRecord) -> None:
        with self._transaction() as db:
            self._write_control(db, run_id, control)

    def control_seen(self, run_id: str, command_id: str) -> bool:
        with closing(self._connect()) as db:
            return (
                db.execute(
                    "SELECT 1 FROM control_log WHERE run_id = ? AND command_id = ?",
                    (run_id, command_id),
                ).fetchone()
                is not None
            )

    # -- reads for the observer -------------------------------------------------------
    _EVENT_COLUMNS = "sequence, tick, phase, type, rule_id, cause, summary_json, actor_id"

    def list_events(
        self,
        run_id: str,
        limit: int,
        before: int | None,
        max_tick: int | None = None,
        organism_id: str | None = None,
    ) -> tuple[list[dict], bool]:
        """Newest first. With organism_id: events it acted in or whose deltas touch it."""
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT {self._EVENT_COLUMNS} FROM events e "
                "WHERE run_id = ? AND (? IS NULL OR sequence < ?) AND (? IS NULL OR tick <= ?) "
                "AND (? IS NULL OR actor_id = ? OR EXISTS (SELECT 1 FROM event_organism_changes c "
                "WHERE c.run_id = e.run_id AND c.event_sequence = e.sequence "
                "AND c.organism_id = ?)) ORDER BY sequence DESC LIMIT ?",
                (
                    run_id,
                    before,
                    before,
                    max_tick,
                    max_tick,
                    organism_id,
                    organism_id,
                    organism_id,
                    limit + 1,
                ),
            ).fetchall()
        events = [self._event_row(r) for r in rows[:limit]]
        return events, len(rows) > limit

    @staticmethod
    def _event_row(row: tuple) -> dict:
        return {
            "sequence": row[0],
            "tick": row[1],
            "phase": row[2],
            "type": row[3],
            "rule_id": row[4],
            "cause": row[5],
            "summary": json.loads(row[6]),
            "actor_id": row[7],
        }

    def get_event(self, run_id: str, sequence: int) -> dict | None:
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            row = db.execute(
                f"SELECT {self._EVENT_COLUMNS}, payload_json FROM events "
                "WHERE run_id = ? AND sequence = ?",
                (run_id, sequence),
            ).fetchone()
            if row is None:
                return None
            changes = db.execute(
                "SELECT x, y, field, before, after FROM event_cell_changes "
                "WHERE run_id = ? AND event_sequence = ? ORDER BY idx, field",
                (run_id, sequence),
            ).fetchall()
            ledger = db.execute(
                "SELECT resource, category, amount FROM ledger_entries "
                "WHERE run_id = ? AND event_sequence = ? ORDER BY resource, category",
                (run_id, sequence),
            ).fetchall()
            organism_changes = db.execute(
                "SELECT organism_id, field, before_json, after_json FROM event_organism_changes "
                "WHERE run_id = ? AND event_sequence = ? ORDER BY position",
                (run_id, sequence),
            ).fetchall()
            memory_changes = db.execute(
                "SELECT op, record_json FROM event_memory_changes "
                "WHERE run_id = ? AND event_sequence = ? ORDER BY position",
                (run_id, sequence),
            ).fetchall()
            message_changes = db.execute(
                "SELECT op, reason, record_json FROM event_message_changes "
                "WHERE run_id = ? AND event_sequence = ? ORDER BY position",
                (run_id, sequence),
            ).fetchall()
        event = self._event_row(row)
        event["payload"] = json.loads(row[8])
        event["organism_changes"] = [
            {
                "organism_id": o,
                "field": f,
                "before": json.loads(b),
                "after": _without_lifespan(json.loads(a)) if f == "record" else json.loads(a),
            }
            for o, f, b, a in organism_changes
        ]
        event["changes"] = [
            {"x": x, "y": y, "field": f, "before": b, "after": a, "delta": a - b}
            for x, y, f, b, a in changes
        ]
        event["ledger"] = [{"resource": r, "category": c, "amount": a} for r, c, a in ledger]
        event["memory_changes"] = [{"op": op, "record": json.loads(r)} for op, r in memory_changes]
        event["message_changes"] = [
            {"op": op, "reason": reason, "record": json.loads(r)}
            for op, reason, r in message_changes
        ]
        return event

    def message_history(
        self, run_id: str, organism_id: str, max_tick: int | None = None, limit: int = 40
    ) -> list[dict]:
        """Items an organism sent or received, newest first: one row per item with its
        latest recorded state (observer only; text is shown, never interpreted)."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT message_id, tick, op, reason, record_json FROM event_message_changes "
                "WHERE run_id = ? AND (sender_id = ? OR recipient_id = ?) "
                "AND (? IS NULL OR tick <= ?) ORDER BY event_sequence, position",
                (run_id, organism_id, organism_id, max_tick, max_tick),
            ).fetchall()
        items: dict[str, dict] = {}
        for message_id, tick, op, reason, record in rows:
            data = json.loads(record)
            item = items.setdefault(message_id, {**data, "state": "pending"})
            if op == "add":
                item.update(data)
                item["state"] = data["status"]
            elif reason != "delivered":
                item["state"] = reason
                item["ended_tick"] = tick
        ordered = sorted(items.values(), key=lambda m: (m["sent_tick"], m["id"]), reverse=True)
        return ordered[:limit]

    def behaviour_stats(self, run_id: str, max_tick: int | None = None) -> dict[str, dict]:
        """Per-organism counts derived only from recorded events (observer view, M10).

        Decisions by outcome, accepted actions by type, distinct cells occupied (birth cell
        plus every accepted move), energy/material extracted, energy spent on its own
        accepted actions (fees and entry costs), gifts and messages given and received,
        and material packed/unpacked. Descriptive counts, not measures of intent."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT type, rule_id, actor_id, summary_json FROM events WHERE run_id = ? "
                "AND type IN ('organism_born', 'action_accepted', 'action_rejected') "
                "AND (? IS NULL OR tick <= ?) ORDER BY sequence",
                (run_id, max_tick, max_tick),
            ).fetchall()
        stats: dict[str, dict] = {}

        def entry(actor: str) -> dict:
            return stats.setdefault(
                actor,
                {
                    "decisions": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "unparsed": 0,
                    "actions": {},
                    "cells": set(),
                    "energy_extracted": 0,
                    "material_extracted": 0,
                    "energy_spent_on_actions": 0,
                    "gifts_given": 0,
                    "gifts_received": 0,
                    "messages_sent": 0,
                    "messages_received": 0,
                    "material_packed": 0,
                    "material_unpacked": 0,
                    "children": 0,
                },
            )

        for type_, rule, actor, summary_json in rows:
            s = json.loads(summary_json)
            a = entry(actor)
            if type_ == "organism_born":
                a["cells"].add((s["x"], s["y"]))
                continue
            a["decisions"] += 1
            if type_ == "action_rejected":
                a["unparsed" if rule == "cognition.parse.v1" else "rejected"] += 1
                continue
            a["accepted"] += 1
            action = s.get("action")
            a["actions"][action] = a["actions"].get(action, 0) + 1
            a["energy_spent_on_actions"] += s.get("energy_cost", 0)
            if action == "move":
                a["cells"].add((s["to_x"], s["to_y"]))
            elif action == "extract":
                a[f"{s['resource']}_extracted"] += s["amount"]
            elif action == "modify":
                a["material_packed" if s["mode"] == "pack" else "material_unpacked"] += s["amount"]
            elif action == "transfer":
                a["gifts_given"] += 1
                entry(s["target"])["gifts_received"] += 1
            elif action == "communicate":
                a["messages_sent"] += 1
                entry(s["target"])["messages_received"] += 1
            elif action == "reproduce":
                a["children"] += 1
        for a in stats.values():
            a["cells_visited"] = len(a.pop("cells"))
        return stats

    def birth_record(self, run_id: str, organism_id: str) -> dict | None:
        """How a run-born organism came to be (M12/M13): parent, tick and the logged
        inheritance of tendencies and memories. None for seed organisms."""
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT sequence, tick, summary_json FROM events WHERE run_id = ? "
                "AND actor_id = ? AND rule_id = 'life.birth.v1'",
                (run_id, organism_id),
            ).fetchone()
        if row is None:
            return None
        return {"event_sequence": row[0], "tick": row[1], **json.loads(row[2])}

    def interactions_at_tick(self, run_id: str, tick: int) -> list[dict]:
        """Accepted transfers and messages committed at one tick (for the grid overlay)."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT actor_id, summary_json FROM events WHERE run_id = ? AND tick = ? "
                "AND type = 'action_accepted' AND rule_id IN "
                "('action.transfer.v1', 'action.communicate.v1', 'action.reproduce.v1') "
                "ORDER BY sequence",
                (run_id, tick),
            ).fetchall()
        return [interaction(actor, json.loads(summary)) for actor, summary in rows]

    def memory_history(self, run_id: str, owner_id: str, max_tick: int | None = None) -> list[dict]:
        """Every memory record an organism ever gained or lost, in history order (observer
        only: forgotten records stay readable here after leaving active memory)."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT event_sequence, tick, op, record_json FROM event_memory_changes "
                "WHERE run_id = ? AND owner_id = ? AND (? IS NULL OR tick <= ?) "
                "ORDER BY event_sequence, position",
                (run_id, owner_id, max_tick, max_tick),
            ).fetchall()
        return [
            {"event_sequence": seq, "tick": tick, "op": op, "record": json.loads(record)}
            for seq, tick, op, record in rows
        ]

    def events_at_tick(self, run_id: str, tick: int) -> list[int]:
        """Cell indices changed by events at one tick, in ascending order."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT DISTINCT c.idx FROM event_cell_changes c JOIN events e "
                "ON e.run_id = c.run_id AND e.sequence = c.event_sequence "
                "WHERE e.run_id = ? AND e.tick = ? ORDER BY c.idx",
                (run_id, tick),
            ).fetchall()
        return [r[0] for r in rows]

    def cell_history(
        self, run_id: str, index: int, limit: int = 8, max_tick: int | None = None
    ) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT e.sequence, e.tick, e.type, e.rule_id, e.actor_id, c.field, c.before, "
                "c.after "
                "FROM event_cell_changes c JOIN events e "
                "ON e.run_id = c.run_id AND e.sequence = c.event_sequence "
                "WHERE c.run_id = ? AND c.idx = ? AND (? IS NULL OR e.tick <= ?) "
                "ORDER BY e.sequence DESC LIMIT ?",
                (run_id, index, max_tick, max_tick, limit),
            ).fetchall()
        return [
            {
                "event_sequence": s,
                "tick": t,
                "type": ty,
                "rule_id": r,
                "actor_id": actor,
                "field": f,
                "before": b,
                "after": a,
                "delta": a - b,
            }
            for s, t, ty, r, actor, f, b, a in rows
        ]

    def latest_decision(
        self, run_id: str, organism_id: str, max_tick: int | None = None
    ) -> dict | None:
        """The last recorded observation/proposal/outcome for one organism."""
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT sequence FROM events WHERE run_id = ? AND actor_id = ? "
                "AND type IN ('action_accepted', 'action_rejected') "
                "AND (? IS NULL OR tick <= ?) ORDER BY sequence DESC LIMIT 1",
                (run_id, organism_id, max_tick, max_tick),
            ).fetchone()
        return None if row is None else self.get_event(run_id, row[0])

    def changed_organisms_at_tick(self, run_id: str, tick: int) -> list[str]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT DISTINCT c.organism_id FROM event_organism_changes c JOIN events e "
                "ON e.run_id = c.run_id AND e.sequence = c.event_sequence "
                "WHERE e.run_id = ? AND e.tick = ? ORDER BY c.organism_id",
                (run_id, tick),
            ).fetchall()
        return [r[0] for r in rows]
