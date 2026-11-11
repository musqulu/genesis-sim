"""Event contracts: exact deltas with an attributable cause."""

from dataclasses import dataclass, field
from typing import Any

RUN_CREATED = "run_created"
ENERGY_INFLOW = "energy_inflow"
ORGANISM_BORN = "organism_born"
UPKEEP = "upkeep"
ORGANISM_DIED = "organism_died"
ACTION_ACCEPTED = "action_accepted"
ACTION_REJECTED = "action_rejected"
EXTINCTION = "extinction"
DECISION_ROUND = "decision_round"
MEMORY_WRITTEN = "memory_written"
MEMORY_SKIPPED = "memory_skipped"
MEMORY_UPKEEP = "memory_upkeep"
MESSAGES_DELIVERED = "messages_delivered"
MESSAGES_READ = "messages_read"

INIT_RULE = "init.seeded.v1"
INFLOW_RULE = "env.energy_inflow.v1"
SEED_BIRTH_RULE = "life.seed_birth.v1"
UPKEEP_RULE = "life.upkeep.v1"
DEATH_RULES = {
    "age": "life.age_limit.v1",
    "starvation": "life.starvation.v1",
    "exhaustion": "life.exhaustion.v1",
}
MOVE_RULE = "action.move.v1"
WAIT_RULE = "action.wait.v1"
EXTRACT_RULE = "action.extract.v1"
MODIFY_RULE = "action.modify.v1"
VALIDATION_RULE = "action.validation.v1"
EXTINCTION_RULE = "life.extinction.v1"
SCHEDULE_RULE = "cognition.schedule.v1"
PARSE_RULE = "cognition.parse.v1"
MEMORY_WRITE_RULE = "memory.write.v1"
MEMORY_UPKEEP_RULE = "memory.upkeep.v1"
TRANSFER_RULE = "action.transfer.v1"
COMMUNICATE_RULE = "action.communicate.v1"
DELIVERY_RULE = "message.delivery.v1"
READ_RULE = "message.read.v1"


@dataclass(frozen=True, slots=True)
class CellChange:
    x: int
    y: int
    field: str  # "energy" | "loose_material" | "packed_material"
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before


@dataclass(frozen=True, slots=True)
class OrganismChange:
    """Exact before/after of one organism field. Field "record" creates an organism."""

    organism_id: str
    field: str
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class MemoryChange:
    """Active-memory membership change. `record` is the full record (MemoryRecord.to_dict),
    so a forgotten record stays readable in history after it leaves active state."""

    op: str  # "add" | "forget"
    record: dict


@dataclass(frozen=True, slots=True)
class MessageChange:
    """Inbox membership change (schema 8). `record` is the full Message.to_dict(), so a
    read, expired or undeliverable item stays readable in history."""

    op: str  # "add" | "remove"
    record: dict
    reason: str | None = None  # remove: delivered | read | expired | recipient_dead


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    resource: str  # "energy" | "material"
    category: str  # environmental_inflow | upkeep | movement | death_retirement | ...
    amount: int  # actual amount moved, never the requested amount


@dataclass(frozen=True, slots=True)
class WorldEvent:
    tick: int
    phase: str  # initialization | environment | lifecycle | action | diagnostic
    type: str
    rule_id: str
    cause: str  # operator | environment | lifecycle | organism
    summary: dict[str, Any] = field(default_factory=dict)
    changes: tuple[CellChange, ...] = ()
    ledger: tuple[LedgerEntry, ...] = ()
    organism_changes: tuple[OrganismChange, ...] = ()
    actor_id: str | None = None
    memory_changes: tuple[MemoryChange, ...] = ()
    message_changes: tuple[MessageChange, ...] = ()
    # Observer-only detail (e.g. the observation supplied and the proposal received). It
    # never affects state; replay ignores it.
    payload: dict[str, Any] = field(default_factory=dict)
