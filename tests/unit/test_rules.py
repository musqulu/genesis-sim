from dataclasses import replace

import pytest
from genesis.domain.config import WorldConfig
from genesis.domain.events import ENERGY_INFLOW, INFLOW_RULE
from genesis.domain.state import Cell, Ledger, World
from genesis.engine.ledger import InvariantViolation, reconcile, require_balanced
from genesis.engine.rules import energy_inflow
from genesis.engine.world import advance, apply_event, create_world


def run_to(world, tick):
    events = []
    while world.tick < tick:
        result = advance(world)
        world = result.world
        events.extend(result.events)
    return world, events


def tiny(energies, **config):
    """A hand-built world; the oracle below is independent of the production rule."""
    cfg = WorldConfig(width=len(energies), height=1, **config)
    cells = tuple(Cell(i, 0, energy=e) for i, e in enumerate(energies))
    return World(cfg, cells, Ledger(initial_energy=sum(energies), initial_material=0))


def test_seed_repeatability_and_difference():
    a, b = create_world(WorldConfig(seed=42)), create_world(WorldConfig(seed=42))
    assert a == b and a.state_hash() == b.state_hash()
    assert create_world(WorldConfig(seed=43)).state_hash() != a.state_hash()
    assert len({c.energy for c in a.cells}) > 50  # genuinely varied, not constant
    assert any(c.loose_material for c in a.cells)
    assert all(c.packed_material == 0 for c in a.cells)  # packing is inactive


def test_same_seed_same_tick_same_hash_across_independent_runs():
    first, _ = run_to(create_world(WorldConfig(seed=42)), 100)
    second, _ = run_to(create_world(WorldConfig(seed=42)), 100)
    assert first == second and first.state_hash() == second.state_hash()
    assert first.tick == 100


def test_inflow_occurs_exactly_on_configured_ticks():
    world = create_world(WorldConfig(seed=42))
    ticks_with_events = []
    for _ in range(35):
        result = advance(world)
        world = result.world
        ticks_with_events += [e.tick for e in result.events]
    assert ticks_with_events == [10, 20, 30]


def test_tick_9_has_no_event_and_tick_10_credits_every_non_full_cell_by_one():
    world, events = run_to(create_world(WorldConfig(seed=42)), 9)
    assert events == []
    before = world
    result = advance(world)
    (event,) = result.events
    assert (event.type, event.rule_id, event.tick) == (ENERGY_INFLOW, INFLOW_RULE, 10)
    for old, new in zip(before.cells, result.world.cells, strict=True):
        assert new.energy == min(old.energy + 1, 100)
        assert (new.loose_material, new.packed_material) == (
            old.loose_material,
            old.packed_material,
        )
    expected = sum(1 for c in before.cells if c.energy < 100)
    assert event.summary["credited_total"] == len(event.changes) == expected > 0


def test_full_cells_receive_zero_credited_inflow():
    world = create_world(WorldConfig(seed=42))
    full = [i for i, c in enumerate(world.cells) if c.energy == 100]
    assert full, "seed 42 should contain full cells for this test to be meaningful"
    world, events = run_to(world, 10)
    (event,) = events
    changed = {(c.x, c.y) for c in event.changes}
    assert all((world.cells[i].x, world.cells[i].y) not in changed for i in full)
    assert all(world.cells[i].energy == 100 for i in full)
    assert event.summary["cells_at_cap"] >= len(full)
    assert (
        event.summary["blocked_total"]
        == event.summary["requested_total"] - event.summary["credited_total"]
    )


def test_all_full_world_credits_zero_and_still_reports_the_rule_firing():
    world = tiny([100, 100, 100])
    event = energy_inflow(world, 10)
    assert event is not None
    assert event.changes == () and event.ledger == ()
    assert event.summary["credited_total"] == 0
    assert event.summary["requested_total"] == 3 and event.summary["blocked_total"] == 3


def test_cap_and_boundary_accounting_uses_actual_amounts():
    # Larger requested amount: 0 -> +5, 98 -> +2 (capped), 100 -> +0, 99 -> +1 (capped).
    world = tiny([0, 98, 100, 99], inflow_amount=5)
    event = energy_inflow(world, 10)
    gains = {(c.x): c.delta for c in event.changes}
    assert gains == {0: 5, 1: 2, 3: 1}
    assert event.summary["requested_total"] == 20
    assert event.summary["credited_total"] == 8
    assert event.summary["blocked_total"] == 12
    assert event.summary["cells_at_cap"] == 1
    assert [(e.category, e.amount) for e in event.ledger] == [("environmental_inflow", 8)]
    after = apply_event(world, event)
    assert [c.energy for c in after.cells] == [5, 100, 100, 100]
    assert after.ledger.environmental_inflow == 8


def test_zero_inflow_fixture_changes_nothing_and_emits_no_events():
    config = WorldConfig.for_run(42, "zero_inflow")
    assert config.inflow_amount == 0 and config.fixture == "zero_inflow"
    start = create_world(config)
    end, events = run_to(start, 100)
    assert events == []
    assert end.cells == start.cells
    assert end.ledger == start.ledger and end.tick == 100
    assert reconcile(end).balanced


def test_ledger_balances_after_every_tick_and_matches_event_sums():
    world = create_world(WorldConfig(seed=7))
    credited = 0
    for _ in range(60):
        result = advance(world)
        world = result.world
        credited += sum(e.summary["credited_total"] for e in result.events)
        report = reconcile(world)
        assert report.balanced
        assert report.energy_actual == world.ledger.initial_energy + credited
    assert world.ledger.environmental_inflow == credited
    assert report.material_actual == world.ledger.initial_material  # no regeneration


def test_replaying_recorded_deltas_reconstructs_state_without_rules():
    initial = create_world(WorldConfig(seed=42))
    final, events = run_to(initial, 50)
    rebuilt = replace(initial, tick=0)
    for event in events:
        rebuilt = apply_event(rebuilt, event)
    assert [c for c in rebuilt.cells] == [c for c in final.cells]
    assert rebuilt.ledger == final.ledger


def test_delta_computed_from_different_state_is_refused():
    world = tiny([1, 2])
    event = energy_inflow(world, 10)
    with pytest.raises(ValueError):
        apply_event(tiny([5, 2]), event)


def test_unbalanced_ledger_is_a_technical_failure():
    world = tiny([1, 2])
    broken = replace(world, ledger=replace(world.ledger, environmental_inflow=1))
    with pytest.raises(InvariantViolation):
        require_balanced(broken)
