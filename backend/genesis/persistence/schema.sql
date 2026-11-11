-- Genesis database layout, user_version 7 (M09). M01's `runs` / `active_run` tables, if
-- present in an older file, are legacy and are never read, altered or dropped. A version-2
-- (M02) or 3 (M03) file is migrated by adding columns/tables; no recorded history is
-- rewritten. Columns added by migrations are declared at the end of each table.

CREATE TABLE IF NOT EXISTS run_manifest (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    engine_version TEXT NOT NULL,
    engine_source_hash TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    rng_algorithm TEXT NOT NULL,
    config_json TEXT NOT NULL,
    initial_cells_json TEXT NOT NULL,   -- [[energy, loose, packed], ...] row-major
    initial_state_hash TEXT NOT NULL,
    manifest_version INTEGER NOT NULL DEFAULT 1,
    manifest_json TEXT,                 -- canonical JSON; the authority for this run
    manifest_hash TEXT,                 -- SHA-256 of manifest_json
    initial_rng_state TEXT,             -- canonical PRNG state after seeding (schema >= 3)
    initial_organisms_json TEXT         -- seed organisms incl. sampled lifespans (schema 4)
);

CREATE TABLE IF NOT EXISTS active_sim_run (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id)
);

-- Committed state projection; updated only inside a tick transaction.
CREATE TABLE IF NOT EXISTS cells (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    idx INTEGER NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    energy INTEGER NOT NULL CHECK (energy >= 0),
    loose_material INTEGER NOT NULL CHECK (loose_material >= 0),
    packed_material INTEGER NOT NULL CHECK (packed_material >= 0),
    PRIMARY KEY (run_id, idx)
) WITHOUT ROWID;

-- One append-only row per committed tick (tick 0 = initial state).
CREATE TABLE IF NOT EXISTS ticks (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    tick INTEGER NOT NULL,
    state_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, tick)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    sequence INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    phase TEXT NOT NULL,
    type TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    cause TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    actor_id TEXT,                      -- organism that proposed/underwent it (M04)
    payload_json TEXT NOT NULL DEFAULT '{}',  -- observer-only detail; never affects state
    PRIMARY KEY (run_id, sequence)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS events_by_tick ON events (run_id, tick);

-- Exact per-cell deltas for each event.
CREATE TABLE IF NOT EXISTS event_cell_changes (
    run_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    field TEXT NOT NULL,
    before INTEGER NOT NULL,
    after INTEGER NOT NULL,
    PRIMARY KEY (run_id, event_sequence, idx, field),
    FOREIGN KEY (run_id, event_sequence) REFERENCES events(run_id, sequence)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS event_cell_changes_by_cell
    ON event_cell_changes (run_id, idx, event_sequence);

CREATE TABLE IF NOT EXISTS ledger_entries (
    run_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    resource TEXT NOT NULL,
    category TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (run_id, event_sequence, resource, category),
    FOREIGN KEY (run_id, event_sequence) REFERENCES events(run_id, sequence)
) WITHOUT ROWID;

-- Operator control metadata (wall-clock/pacing); excluded from state hashes.
CREATE TABLE IF NOT EXISTS control_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    command_id TEXT NOT NULL,
    command TEXT NOT NULL,
    params_json TEXT NOT NULL,
    logical_tick INTEGER NOT NULL,
    wall_time TEXT NOT NULL,
    UNIQUE (run_id, command_id)
);

-- Checksummed state at a committed tick. Disposable: history can always be replayed from
-- the manifest's initial state, so a bad snapshot is skipped, never trusted.
CREATE TABLE IF NOT EXISTS snapshots (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    tick INTEGER NOT NULL,
    format_version INTEGER NOT NULL,
    reason TEXT NOT NULL,               -- initial | periodic | pause | auto_pause | close
    last_sequence INTEGER NOT NULL,
    state_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, tick)
) WITHOUT ROWID;

-- Technical operations record (recovery notes, invalid snapshots). Never ecological.
CREATE TABLE IF NOT EXISTS technical_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    detail TEXT NOT NULL,
    UNIQUE (run_id, kind, subject)
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    applied_at TEXT NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (from_version, to_version)
);

-- Exact per-organism deltas (JSON values). Field "record" creates an organism.
CREATE TABLE IF NOT EXISTS event_organism_changes (
    run_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    position INTEGER NOT NULL,
    organism_id TEXT NOT NULL,
    field TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    PRIMARY KEY (run_id, event_sequence, position),
    FOREIGN KEY (run_id, event_sequence) REFERENCES events(run_id, sequence)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS event_organism_changes_by_organism
    ON event_organism_changes (run_id, organism_id, event_sequence);

-- Committed organism projection (rebuildable; verified against replay on load).
CREATE TABLE IF NOT EXISTS organisms (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    organism_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (run_id, organism_id)
) WITHOUT ROWID;

-- Technical request inbox (M06). Written before dispatch, completed when a response
-- arrives, consumed in the tick transaction. A recomputed request with different input
-- abandons the old row. Never ecological state.
CREATE TABLE IF NOT EXISTS request_records (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    request_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    tick INTEGER NOT NULL,
    actor_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL,                -- requested | completed | failed | consumed | abandoned
    response_raw TEXT,
    response_meta_json TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, request_id, input_hash)
) WITHOUT ROWID;

-- Append-only decision attempts, written in the tick transaction. The unique
-- (run_id, request_id) pair is the consumption key: one consumed proposal per request.
CREATE TABLE IF NOT EXISTS decision_attempts (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    request_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    actor_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    provider_json TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    parse_status TEXT NOT NULL,           -- valid | empty_output | malformed_json | ...
    parse_error TEXT,
    proposal_json TEXT,
    outcome TEXT NOT NULL,                -- accepted | rejected | unparsed
    outcome_rule TEXT NOT NULL,
    outcome_reason TEXT,
    event_sequence INTEGER NOT NULL,
    usage_json TEXT NOT NULL,
    PRIMARY KEY (run_id, request_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS decision_attempts_by_actor
    ON decision_attempts (run_id, actor_id, tick);

-- Active-memory membership deltas (M08). Each row carries the full record, so a forgotten
-- record remains readable in history after it leaves an organism's active memory.
CREATE TABLE IF NOT EXISTS event_memory_changes (
    run_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    position INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('add', 'forget')),
    record_json TEXT NOT NULL,
    PRIMARY KEY (run_id, event_sequence, position),
    FOREIGN KEY (run_id, event_sequence) REFERENCES events(run_id, sequence)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS event_memory_changes_by_owner
    ON event_memory_changes (run_id, owner_id, event_sequence);

-- Inbox deltas between organisms (M09). Each row carries the full item, so a read, expired
-- or undeliverable message stays readable in history after it leaves the inbox.
CREATE TABLE IF NOT EXISTS event_message_changes (
    run_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    position INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    message_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('add', 'remove')),
    reason TEXT,
    record_json TEXT NOT NULL,
    PRIMARY KEY (run_id, event_sequence, position),
    FOREIGN KEY (run_id, event_sequence) REFERENCES events(run_id, sequence)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS event_message_changes_by_sender
    ON event_message_changes (run_id, sender_id, event_sequence);
CREATE INDEX IF NOT EXISTS event_message_changes_by_recipient
    ON event_message_changes (run_id, recipient_id, event_sequence);
