import hashlib
import json
from dataclasses import asdict, dataclass

from genesis.domain.config import (
    RULES_BY_SCHEMA,
    TENDENCIES,
    TENDENCY_MAX,
    LifeConfig,
    WorldConfig,
)


def _nonnegative(name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class Cell:
    x: int
    y: int
    energy: int = 0
    loose_material: int = 0
    packed_material: int = 0

    def __post_init__(self) -> None:
        for name in ("x", "y", "energy", "loose_material", "packed_material"):
            _nonnegative(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class Ledger:
    """Cumulative resource accounting, using actual (credited) amounts only.

    energy:   cells + living organisms = initial + inflow - dissipation - death retirement
    material: loose + packed + carried + retired = initial
    Dissipation aggregates upkeep and movement costs (categories stay in ledger entries).
    Organisms carry no material until M05.
    """

    initial_energy: int
    initial_material: int
    environmental_inflow: int = 0
    dissipation: int = 0
    death_retirement: int = 0
    retired_material: int = 0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            _nonnegative(name, value)

    @property
    def expected_energy(self) -> int:
        return (
            self.initial_energy
            + self.environmental_inflow
            - self.dissipation
            - self.death_retirement
        )

    @property
    def expected_material(self) -> int:
        return self.initial_material - self.retired_material


DEATH_CAUSES = ("age", "starvation", "exhaustion")
# "message": read from the inbox (M09); "reflection": the organism's own interpretation (M11)
MEMORY_KINDS = ("observation", "outcome", "message", "reflection")
MESSAGE_KINDS = ("message", "transfer")  # words, or a receipt for a transfer
MESSAGE_STATES = ("pending", "delivered")


@dataclass(frozen=True, slots=True)
class Message:
    """One inbox item between two organisms (state schema 8).

    `pending` until the tick after it was sent, then `delivered` into the recipient's
    inbox until it is read at a decision or expires. Text is the sender's own claim; the
    engine never interprets it. A `transfer` item is the engine's receipt of a gift.
    """

    id: str
    kind: str
    sender_id: str
    recipient_id: str
    text: str
    sent_tick: int
    status: str
    delivered_tick: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.startswith(f"{self.sender_id}-s"):
            raise ValueError("message id must be sender-scoped")
        if self.kind not in MESSAGE_KINDS or self.status not in MESSAGE_STATES:
            raise ValueError("unknown message kind or status")
        if self.sender_id == self.recipient_id:
            raise ValueError("an organism cannot message itself")
        if not isinstance(self.text, str):
            raise ValueError("message text must be a string")
        _nonnegative("sent_tick", self.sent_tick)
        if (self.status == "delivered") != (self.delivered_tick is not None):
            raise ValueError("only a delivered message has a delivery tick")

    @property
    def size(self) -> int:
        return len(self.text.encode())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(**data)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One active memory record, owned by exactly one organism (state schema 7).

    `x`, `y` are where it was acquired; they stay engine/observer-only (cognition receives an
    offset from the owner's current position). The text is a fixed account of that moment
    and is never refreshed: it can become stale.
    """

    id: str
    owner_id: str
    kind: str
    tick: int  # acquisition tick
    age: int  # owner's age at acquisition
    x: int
    y: int
    text: str
    keywords: tuple[str, ...]
    salience: int  # fixed per category
    # Reflections only: ids of the owner's records cited as support (M11). The links stay
    # in history even if the cited records are later forgotten.
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.startswith(f"{self.owner_id}-m"):
            raise ValueError("memory id must be owner-scoped")
        if self.kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind {self.kind!r}")
        for name in ("tick", "age", "x", "y", "salience"):
            _nonnegative(name, getattr(self, name))
        if not isinstance(self.text, str):
            raise ValueError("memory text must be a string")
        if not isinstance(self.keywords, tuple) or not all(
            isinstance(k, str) for k in self.keywords
        ):
            raise ValueError("memory keywords must be a tuple of strings")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(e, str) and e.startswith(f"{self.owner_id}-m") for e in self.evidence
        ):
            raise ValueError("evidence must cite the owner's own records")
        if (self.kind == "reflection") != bool(self.evidence):
            raise ValueError("a reflection, and only a reflection, cites evidence")

    @property
    def size(self) -> int:
        """Bytes of UTF-8 text: the unit memory costs and limits are measured in."""
        return len(self.text.encode())

    def to_dict(self) -> dict:
        data = asdict(self)
        data["keywords"] = list(self.keywords)
        if self.evidence:  # absent for earlier kinds, so their records stay identical
            data["evidence"] = list(self.evidence)
        else:
            del data["evidence"]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryRecord":
        return cls(
            **{
                **data,
                "keywords": tuple(data["keywords"]),
                "evidence": tuple(data.get("evidence", ())),
            }
        )


@dataclass(frozen=True, slots=True)
class Organism:
    """One agent instance. `lifespan` is engine-private and never enters an observation."""

    id: str
    label: str
    policy: str
    birth_tick: int
    generation: int
    x: int
    y: int
    energy: int
    lifespan: int
    alive: bool = True
    death_tick: int | None = None
    death_cause: str | None = None
    retired_energy: int = 0
    parent_ids: tuple[str, ...] = ()
    carried_material: int = 0  # schema 5
    retired_material: int = 0  # schema 5: carried material retired at death
    # Schema 6 scheduler state (part of the hash: it determines future decisions).
    last_decision_tick: int | None = None
    view_total_at_decision: int | None = None
    memories_written: int = 0  # schema 7: records ever written (names the next record)
    messages_sent: int = 0  # schema 8: inbox items ever sent (names the next one)
    # Schema 9: starting tendencies in TENDENCIES order, 0-100, fixed for life (None: none).
    tendencies: tuple[int, ...] | None = None
    # Schema 10: tick of its last reflection opportunity (cooldown and trigger window).
    reflected_at_tick: int | None = None
    # Schema 11: tick of its last birth as a parent (reproduction cooldown).
    last_reproduced_tick: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("organism id must be a nonempty string")
        for name in (
            "birth_tick",
            "generation",
            "x",
            "y",
            "energy",
            "retired_energy",
            "carried_material",
            "retired_material",
            "memories_written",
            "messages_sent",
        ):
            _nonnegative(name, getattr(self, name))
        if type(self.lifespan) is not int or self.lifespan < 1:
            raise ValueError("lifespan must be a positive integer")
        if self.tendencies is not None and (
            not isinstance(self.tendencies, tuple)
            or len(self.tendencies) != len(TENDENCIES)
            or any(type(v) is not int or not 0 <= v <= TENDENCY_MAX for v in self.tendencies)
        ):
            raise ValueError("tendencies must be six integers 0-100")
        if type(self.alive) is not bool:
            raise ValueError("alive must be a boolean")
        if self.alive and (self.death_tick is not None or self.death_cause is not None):
            raise ValueError("a living organism has no death record")
        if not self.alive:
            if self.death_cause not in DEATH_CAUSES or type(self.death_tick) is not int:
                raise ValueError("a dead organism needs a death tick and a known cause")
            if self.energy != 0 or self.carried_material != 0:
                raise ValueError("a dead organism's resources are retired, not kept")

    @property
    def deadline(self) -> int:
        """First tick at which the organism no longer lives (engine-private)."""
        return self.birth_tick + self.lifespan

    def to_dict(self, schema: int = 11) -> dict:
        data = asdict(self)
        data["parent_ids"] = list(self.parent_ids)
        if schema < 11:  # earlier records never reproduced
            if self.last_reproduced_tick is not None:
                raise ValueError("reproduction state requires state schema 11")
            del data["last_reproduced_tick"]
        if schema < 10:  # earlier records never reflected
            if self.reflected_at_tick is not None:
                raise ValueError("reflection state requires state schema 10")
            del data["reflected_at_tick"]
        if schema < 9:  # earlier records never had tendencies
            if self.tendencies is not None:
                raise ValueError("tendencies require state schema 9")
            del data["tendencies"]
        elif self.tendencies is not None:
            data["tendencies"] = list(self.tendencies)
        if schema < 8:  # earlier records never sent anything
            if self.messages_sent:
                raise ValueError("message fields require state schema 8")
            del data["messages_sent"]
        if schema < 7:  # earlier records never had memory
            if self.memories_written:
                raise ValueError("memory fields require state schema 7")
            del data["memories_written"]
        if schema < 6:  # earlier records never had scheduler fields
            if self.last_decision_tick is not None or self.view_total_at_decision is not None:
                raise ValueError("scheduler fields require state schema 6")
            del data["last_decision_tick"], data["view_total_at_decision"]
        if schema < 5:  # M04 records never had material fields
            if self.carried_material or self.retired_material:
                raise ValueError("schema 4 organisms cannot carry material")
            del data["carried_material"], data["retired_material"]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Organism":
        tendencies = data.get("tendencies")
        return cls(
            **{
                **data,
                "parent_ids": tuple(data.get("parent_ids", ())),
                "tendencies": None if tendencies is None else tuple(tendencies),
            }
        )

    def tendency_map(self) -> dict[str, int] | None:
        return (
            None if self.tendencies is None else dict(zip(TENDENCIES, self.tendencies, strict=True))
        )


@dataclass(frozen=True, slots=True)
class World:
    config: WorldConfig
    cells: tuple[Cell, ...]
    ledger: Ledger
    tick: int = 0
    # Canonical JSON of the seeded PRNG state (schema >= 3). None for schema 2 (M02) runs.
    rng_state: str | None = None
    # 2: M02 (cells only); 3: M03 (+ PRNG state); 4: M04 (+ lifecycle rules and organisms);
    # 5: M05 (+ carried/retired material and material action rules);
    # 6: M06 (+ decision cadence, cognition fee and per-organism scheduler state);
    # 7: M08 (+ memory rules and every organism's active memory records);
    # 8: M09 (+ exchange rules and every pending or unread inbox item);
    # 9: M10 (+ tendency mode and each organism's starting tendencies);
    # 10: M11 (+ reflection rules, last reflection tick, reflection records);
    # 11: M12 (+ reproduction rules and each organism's last birth tick).
    # Each schema hashes exactly the fields it defines, so older runs keep their hashes.
    schema: int = 11
    life: LifeConfig | None = None
    organisms: tuple[Organism, ...] = ()
    memories: tuple[MemoryRecord, ...] = ()  # active records only, ordered by id
    messages: tuple[Message, ...] = ()  # pending or unread inbox items, ordered by id

    def __post_init__(self) -> None:
        _nonnegative("tick", self.tick)
        if self.rng_state is not None and not isinstance(self.rng_state, str):
            raise ValueError("rng_state must be canonical JSON text")
        if self.schema not in RULES_BY_SCHEMA:
            raise ValueError(f"unsupported state schema {self.schema}")
        if self.schema == 2 and self.rng_state is not None:
            raise ValueError("schema 2 worlds have no recorded PRNG state")
        if self.schema == 3 and self.rng_state is None:
            raise ValueError("schema 3 worlds require a PRNG state")
        if self.schema < 5 and any(
            o.carried_material or o.retired_material for o in self.organisms
        ):
            raise ValueError("carried material requires state schema 5")
        if self.schema < 6 and any(
            o.last_decision_tick is not None or o.view_total_at_decision is not None
            for o in self.organisms
        ):
            raise ValueError("scheduler state requires state schema 6")
        if self.schema < 7 and (self.memories or any(o.memories_written for o in self.organisms)):
            raise ValueError("memory requires state schema 7")
        if self.schema < 8 and (self.messages or any(o.messages_sent for o in self.organisms)):
            raise ValueError("messages require state schema 8")
        if self.schema < 11 and any(o.last_reproduced_tick is not None for o in self.organisms):
            raise ValueError("reproduction requires state schema 11")
        if self.schema < 10 and (
            any(o.reflected_at_tick is not None for o in self.organisms)
            or any(m.kind == "reflection" for m in self.memories)
        ):
            raise ValueError("reflection requires state schema 10")
        if self.schema < 9 and any(o.tendencies is not None for o in self.organisms):
            raise ValueError("tendencies require state schema 9")
        if self.messages:
            self._check_messages()
        if self.schema < 4 and (self.life is not None or self.organisms):
            raise ValueError("organisms require state schema 4")
        if self.organisms and self.life is None:
            raise ValueError("organisms require lifecycle rules")
        ids = [o.id for o in self.organisms]
        if ids != sorted(set(ids)):
            raise ValueError("organisms must be unique and ordered by id")
        living = [(o.x, o.y) for o in self.organisms if o.alive]
        if len(set(living)) != len(living):
            raise ValueError("two living organisms occupy one cell")
        for organism in self.organisms:
            if not (0 <= organism.x < self.config.width and 0 <= organism.y < self.config.height):
                raise ValueError("organism is outside the world")
            if organism.energy > self.life.energy_capacity:
                raise ValueError("organism energy exceeds its capacity")
            if organism.carried_material > self.life.material_capacity:
                raise ValueError("organism carried material exceeds its capacity")
        if self.memories:
            self._check_memories()
        if len(self.cells) != self.config.width * self.config.height:
            raise ValueError("World must contain exactly one cell per coordinate")
        for i, cell in enumerate(self.cells):
            if (cell.x, cell.y) != (i % self.config.width, i // self.config.width):
                raise ValueError("Cells must use canonical row-major coordinate order")
            if cell.energy > self.config.energy_cap:
                raise ValueError("Cell energy exceeds the configured cap")
            if cell.packed_material > self.config.packed_cap:
                raise ValueError("Cell packed material exceeds the configured cap")

    def _check_memories(self) -> None:
        ids = [m.id for m in self.memories]
        if ids != sorted(set(ids)):
            raise ValueError("memory records must be unique and ordered by id")
        owners = {o.id: o for o in self.organisms}
        counts: dict[str, int] = {}
        sizes: dict[str, int] = {}
        life = self.life
        for record in self.memories:
            if record.owner_id not in owners:
                raise ValueError("a memory record has no owner")
            if record.size > life.memory_record_max_bytes:
                raise ValueError("a memory record exceeds the per-record limit")
            counts[record.owner_id] = counts.get(record.owner_id, 0) + 1
            sizes[record.owner_id] = sizes.get(record.owner_id, 0) + record.size
        for owner in counts:
            if counts[owner] > life.memory_max_records or sizes[owner] > life.memory_max_bytes:
                raise ValueError("active memory exceeds its limits")

    def _check_messages(self) -> None:
        ids = [m.id for m in self.messages]
        if ids != sorted(set(ids)):
            raise ValueError("messages must be unique and ordered by id")
        known = {o.id for o in self.organisms}
        inbox: dict[str, int] = {}
        for message in self.messages:
            if message.sender_id not in known or message.recipient_id not in known:
                raise ValueError("a message names an unknown organism")
            if message.size > self.life.message_max_bytes:
                raise ValueError("a message exceeds the size limit")
            if message.kind == "message":
                inbox[message.recipient_id] = inbox.get(message.recipient_id, 0) + 1
        if any(count > self.life.inbox_capacity for count in inbox.values()):
            raise ValueError("an inbox exceeds its capacity")

    def inbox_of(self, organism_id: str) -> tuple[Message, ...]:
        """Pending and delivered items addressed to one organism, oldest first."""
        return tuple(
            sorted(
                (m for m in self.messages if m.recipient_id == organism_id),
                key=lambda m: (m.sent_tick, m.id),
            )
        )

    def memories_of(self, organism_id: str) -> tuple[MemoryRecord, ...]:
        """One organism's active records, oldest acquisition first (stable id tie-break)."""
        return tuple(
            sorted(
                (m for m in self.memories if m.owner_id == organism_id),
                key=lambda m: (m.tick, m.id),
            )
        )

    def cell_at(self, x: int, y: int) -> Cell:
        if (
            type(x) is not int
            or type(y) is not int
            or not 0 <= x < self.config.width
            or not 0 <= y < self.config.height
        ):
            raise IndexError("Cell is outside the world")
        return self.cells[y * self.config.width + x]

    @property
    def living(self) -> tuple[Organism, ...]:
        return tuple(o for o in self.organisms if o.alive)

    @property
    def extinct(self) -> bool:
        """A population existed and none of it lives. Never true for organism-free runs."""
        return bool(self.organisms) and not self.living

    def organism(self, organism_id: str) -> Organism:
        for organism in self.organisms:
            if organism.id == organism_id:
                return organism
        raise KeyError(organism_id)

    def occupant(self, x: int, y: int) -> Organism | None:
        for organism in self.organisms:
            if organism.alive and (organism.x, organism.y) == (x, y):
                return organism
        return None

    @property
    def cell_energy(self) -> int:
        return sum(cell.energy for cell in self.cells)

    @property
    def organism_energy(self) -> int:
        return sum(o.energy for o in self.organisms if o.alive)

    @property
    def total_energy(self) -> int:
        return self.cell_energy + self.organism_energy

    @property
    def cell_material(self) -> int:
        return sum(cell.loose_material + cell.packed_material for cell in self.cells)

    @property
    def carried_material(self) -> int:
        return sum(o.carried_material for o in self.organisms if o.alive)

    @property
    def total_material(self) -> int:
        """loose + packed + carried by the living (retired material is in the ledger)."""
        return self.cell_material + self.carried_material

    @property
    def state_schema(self) -> int:
        return self.schema

    def state_hash(self) -> str:
        """Canonical hash of everything that defines the world's future.

        Excludes run identity, creation time and all wall-clock/pacing data. Schema 3 adds
        the PRNG state; schema 4 adds lifecycle rules and every organism (living or dead,
        including engine-private lifespans). Older payloads are byte-identical to their
        milestone, so recorded hashes keep verifying.
        """
        payload = {
            "schema": self.schema,
            "rules": RULES_BY_SCHEMA[self.schema],
            "config": asdict(self.config),
            "tick": self.tick,
            "ledger": asdict(self.ledger),
            "cells": [[c.energy, c.loose_material, c.packed_material] for c in self.cells],
        }
        if self.schema >= 3:
            payload["rng_sha256"] = (
                None
                if self.rng_state is None
                else hashlib.sha256(self.rng_state.encode()).hexdigest()
            )
        if self.schema >= 4:
            payload["life"] = None if self.life is None else self.life.to_dict(self.schema)
            payload["organisms"] = [o.to_dict(self.schema) for o in self.organisms]
        if self.schema >= 7:
            payload["memories"] = [m.to_dict() for m in self.memories]
        if self.schema >= 8:
            payload["messages"] = [m.to_dict() for m in self.messages]
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
