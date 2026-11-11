"""Resource accounting. Totals are checked against actual cell and organism contents."""

from dataclasses import dataclass, replace

from genesis.domain.events import LedgerEntry
from genesis.domain.state import Ledger, World

# Ledger entry category -> cumulative Ledger field. Upkeep and movement are both
# dissipation; the entry keeps the finer category for inspection.
ENERGY_CATEGORIES = {
    "environmental_inflow": "environmental_inflow",
    "dissipation": "dissipation",
    "upkeep": "dissipation",
    "movement": "dissipation",
    "extraction_fee": "dissipation",
    "modification_fee": "dissipation",
    "cognition_fee": "dissipation",
    "memory_write_fee": "dissipation",
    "memory_upkeep": "dissipation",
    "transfer_fee": "dissipation",
    "message_fee": "dissipation",
    "death_retirement": "death_retirement",
}


class InvariantViolation(RuntimeError):
    """The ledger disagrees with the world. A technical failure, never repaired silently."""


@dataclass(frozen=True, slots=True)
class Reconciliation:
    energy_expected: int
    energy_actual: int
    material_expected: int
    material_actual: int

    @property
    def balanced(self) -> bool:
        return (
            self.energy_expected == self.energy_actual
            and self.material_expected == self.material_actual
        )


def apply_entries(ledger: Ledger, entries: tuple[LedgerEntry, ...]) -> Ledger:
    for entry in entries:
        if entry.resource == "energy" and entry.category in ENERGY_CATEGORIES:
            name = ENERGY_CATEGORIES[entry.category]
        elif entry.resource == "material" and entry.category == "retired":
            name = "retired_material"
        else:
            raise ValueError(f"Unknown ledger category: {entry.resource}/{entry.category}")
        ledger = replace(ledger, **{name: getattr(ledger, name) + entry.amount})
    return ledger


def reconcile(world: World) -> Reconciliation:
    return Reconciliation(
        energy_expected=world.ledger.expected_energy,
        energy_actual=world.total_energy,
        material_expected=world.ledger.expected_material,
        material_actual=world.total_material,
    )


def require_balanced(world: World) -> None:
    report = reconcile(world)
    if not report.balanced:
        raise InvariantViolation(f"Ledger does not reconcile: {report}")
