"""Explicit environmental rules. Each returns an event with exact deltas, or None."""

from genesis.domain.events import ENERGY_INFLOW, INFLOW_RULE, CellChange, LedgerEntry, WorldEvent
from genesis.domain.state import World


def energy_inflow(world: World, tick: int) -> WorldEvent | None:
    """Every `inflow_interval` ticks each cell may gain up to `inflow_amount` energy.

    Credit is the actual gain after the cap. Full cells are credited zero; the
    uncredited remainder is reported as blocked, never added to any cell. Inflow is an
    open environmental source (not a population-sensitive rule).
    """
    config = world.config
    if config.inflow_amount == 0 or tick % config.inflow_interval != 0:
        return None
    changes = tuple(
        CellChange(cell.x, cell.y, "energy", cell.energy, cell.energy + gain)
        for cell in world.cells
        if (gain := min(config.inflow_amount, config.energy_cap - cell.energy)) > 0
    )
    credited = sum(change.delta for change in changes)
    requested = config.inflow_amount * len(world.cells)
    return WorldEvent(
        tick=tick,
        phase="environment",
        type=ENERGY_INFLOW,
        rule_id=INFLOW_RULE,
        cause="environment",
        summary={
            "requested_per_cell": config.inflow_amount,
            "cells_total": len(world.cells),
            "cells_credited": len(changes),
            "cells_at_cap": len(world.cells) - len(changes),
            "requested_total": requested,
            "credited_total": credited,
            "blocked_total": requested - credited,
        },
        changes=changes,
        ledger=(LedgerEntry("energy", "environmental_inflow", credited),) if credited else (),
    )


# Ordered; each rule sees the state left by the previous one.
RULES = (energy_inflow,)
