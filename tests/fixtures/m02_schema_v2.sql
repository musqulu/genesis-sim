-- Frozen M02 database (user_version 2) produced by M02 code: 4x4 seed 7, 25 ticks.
PRAGMA user_version = 2;
BEGIN TRANSACTION;
CREATE TABLE active_sim_run (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id)
);
INSERT INTO "active_sim_run" VALUES(1,'3512623a-72fb-44fb-b521-2cd916508b35');
CREATE TABLE cells (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    idx INTEGER NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    energy INTEGER NOT NULL CHECK (energy >= 0),
    loose_material INTEGER NOT NULL CHECK (loose_material >= 0),
    packed_material INTEGER NOT NULL CHECK (packed_material >= 0),
    PRIMARY KEY (run_id, idx)
) WITHOUT ROWID;
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',0,0,0,43,7,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',1,1,0,85,2,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,2,0,70,6,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,3,0,76,9,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',4,0,1,29,2,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',5,1,1,57,0,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',6,2,1,10,0,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',7,3,1,13,0,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',8,0,2,56,10,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',9,1,2,17,0,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',10,2,2,82,0,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',11,3,2,76,10,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',12,0,3,76,0,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',13,1,3,8,0,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',14,2,3,7,0,0);
INSERT INTO "cells" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',15,3,3,19,0,0);
CREATE TABLE control_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    command_id TEXT NOT NULL,
    command TEXT NOT NULL,
    params_json TEXT NOT NULL,
    logical_tick INTEGER NOT NULL,
    wall_time TEXT NOT NULL,
    UNIQUE (run_id, command_id)
);
INSERT INTO "control_log" VALUES(1,'3512623a-72fb-44fb-b521-2cd916508b35','fixture-start','start','{"until_tick": null}',0,'2026-11-08T20:04:03.339680+00:00');
CREATE TABLE event_cell_changes (
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
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,0,0,0,'energy',41,42);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,1,1,0,'energy',83,84);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,2,2,0,'energy',68,69);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,3,3,0,'energy',74,75);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,4,0,1,'energy',27,28);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,5,1,1,'energy',55,56);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,6,2,1,'energy',8,9);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,7,3,1,'energy',11,12);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,8,0,2,'energy',54,55);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,9,1,2,'energy',15,16);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,10,2,2,'energy',80,81);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,11,3,2,'energy',74,75);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,12,0,3,'energy',74,75);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,13,1,3,'energy',6,7);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,14,2,3,'energy',5,6);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,15,3,3,'energy',17,18);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,0,0,0,'energy',42,43);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,1,1,0,'energy',84,85);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,2,2,0,'energy',69,70);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,3,3,0,'energy',75,76);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,4,0,1,'energy',28,29);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,5,1,1,'energy',56,57);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,6,2,1,'energy',9,10);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,7,3,1,'energy',12,13);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,8,0,2,'energy',55,56);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,9,1,2,'energy',16,17);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,10,2,2,'energy',81,82);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,11,3,2,'energy',75,76);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,12,0,3,'energy',75,76);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,13,1,3,'energy',7,8);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,14,2,3,'energy',6,7);
INSERT INTO "event_cell_changes" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,15,3,3,'energy',18,19);
CREATE TABLE events (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    sequence INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    phase TEXT NOT NULL,
    type TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    cause TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
) WITHOUT ROWID;
INSERT INTO "events" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',1,0,'initialization','run_created','init.seeded.v1','operator','{"cells_total": 16, "initial_energy": 692, "initial_material": 46, "seed": 7}');
INSERT INTO "events" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,10,'environment','energy_inflow','env.energy_inflow.v1','environment','{"blocked_total": 0, "cells_at_cap": 0, "cells_credited": 16, "cells_total": 16, "credited_total": 16, "requested_per_cell": 1, "requested_total": 16}');
INSERT INTO "events" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,20,'environment','energy_inflow','env.energy_inflow.v1','environment','{"blocked_total": 0, "cells_at_cap": 0, "cells_credited": 16, "cells_total": 16, "credited_total": 16, "requested_per_cell": 1, "requested_total": 16}');
CREATE TABLE ledger_entries (
    run_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    tick INTEGER NOT NULL,
    resource TEXT NOT NULL,
    category TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (run_id, event_sequence, resource, category),
    FOREIGN KEY (run_id, event_sequence) REFERENCES events(run_id, sequence)
) WITHOUT ROWID;
INSERT INTO "ledger_entries" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,10,'energy','environmental_inflow',16);
INSERT INTO "ledger_entries" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,20,'energy','environmental_inflow',16);
CREATE TABLE run_manifest (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    engine_version TEXT NOT NULL,
    engine_source_hash TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    rng_algorithm TEXT NOT NULL,
    config_json TEXT NOT NULL,
    initial_cells_json TEXT NOT NULL,   -- [[energy, loose, packed], ...] row-major
    initial_state_hash TEXT NOT NULL
);
INSERT INTO "run_manifest" VALUES('3512623a-72fb-44fb-b521-2cd916508b35','2026-11-08T20:04:03.337882+00:00',2,'0.2.0','f5a92dedcc87c79af8754f9c7e7df1606632589c264da84c6512bda53b99866e','m02-rules-1','python-random-mt19937','{"energy_cap": 100, "fixture": null, "height": 4, "inflow_amount": 1, "inflow_interval": 10, "material_density_percent": 25, "material_max": 10, "packed_cap": 8, "seed": 7, "width": 4}','[[41,7,0],[83,2,0],[68,6,0],[74,9,0],[27,2,0],[55,0,0],[8,0,0],[11,0,0],[54,10,0],[15,0,0],[80,0,0],[74,10,0],[74,0,0],[6,0,0],[5,0,0],[17,0,0]]','990c99080c4411ccc7b4120d3db9cdf079539fc08dce4ee192823d05cc9aea62');
CREATE TABLE ticks (
    run_id TEXT NOT NULL REFERENCES run_manifest(run_id),
    tick INTEGER NOT NULL,
    state_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, tick)
) WITHOUT ROWID;
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',0,'990c99080c4411ccc7b4120d3db9cdf079539fc08dce4ee192823d05cc9aea62');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',1,'f4f25ff557c3f5c7db7cd995492b6a5b90f40658e27a8935d3cc60f6b92445d5');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',2,'3a32b74faae38e81898b18376090766f1156c5aad52a53e1bbcc71086051e774');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',3,'df9e20719893c44994e20acd83d572c441b5882d96f18e670329d71c7064e6f6');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',4,'af37d619e001a26a2628ead3eda75db5d18feb28b2cb67f0e08ee44ebe408024');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',5,'93de449fa8e3bb91d272a6aac41d9b989b5a8ea75720a3290f97a07bbc44342b');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',6,'526bb48e953d8318de12e251f31f2ed850dfa6fb2e3b8c737ab1ca1f3bd81329');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',7,'1d1c051b04da7d9d582c52ccd74dac11ef4e53f0b6d49e4294707d18132c785d');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',8,'221268130644290f6dbfcb635b43b06c6e0a3692f22cd26299bc3961522c86cb');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',9,'3d2f711167841b4add8806194cd01c55798f43d6a0cbaeeb22e302930256ec40');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',10,'f2a2901959866d27ea2c2c2ec00094d72277e122a9d53319ce46d7ae78b50828');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',11,'2dfe9ea95f175dee4d5d25c9216caff94753108e02eb103e738a991195ae5fdb');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',12,'a7fb53b815d0023ab7df432f91e2fce4d5039dcdf8410efa292c3f169504964b');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',13,'c878c5f52354f0ccec5725fe5c812c4a6da535373c752c1c8b90682c79105947');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',14,'385941bcc87e1c736ff72bd13310145c807b363c0b933ba5860b32e5341f1306');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',15,'0cd1880059bfab61a2deb423689fe281e5160b83005a95e2d956afb467f3932b');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',16,'e536a6eda2f1bb5b9dc8aee59fd52a5a3e2ea3b10d80ad078183b1c6cdcf1b75');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',17,'0320ac6c21ae39630cd0b72973b2f0a2ea5a2c5e06e10e127f06587866c07858');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',18,'ddd33a19b6091e19c5b87b581c0146f3990bdeebf6dd2e4d8c42f06422b9f925');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',19,'b6084fb8c364c3f3aa15659abf1e42fd130e6d9f746b208fbffb91d324606824');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',20,'62504fbd94035ca5412002b33d9356f46b03210018c5dcf3b5d0c312da4ea694');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',21,'bbe62acb50bbcefc448a12d4288561027d5c5a5b6eb305e4deffa296e5a18f87');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',22,'5455a17a9c5ef5f8c6711ce54a403bf6c260d8f91433ef8c48ebf005f2182e31');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',23,'34db044bf50a1ab9491df226aaa3ea7be6356d9590c5114267a6201959695045');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',24,'04c97e4f5bf74f516ff13635c8a32709592d93b400d0983f311d0d822763e8bb');
INSERT INTO "ticks" VALUES('3512623a-72fb-44fb-b521-2cd916508b35',25,'b1383cddfac08047c345f06bf4d01faf1c94cb82caeb1275960d5e4e5ea08b36');
CREATE INDEX event_cell_changes_by_cell
    ON event_cell_changes (run_id, idx, event_sequence);
COMMIT;
