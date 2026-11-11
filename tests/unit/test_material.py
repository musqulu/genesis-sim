"""M05 engine: extraction, packing/unpacking, conservation and effective geography."""

from dataclasses import replace

import pytest
from genesis.cognition import fixture_policy
from genesis.domain.actions import Proposal
from genesis.domain.config import FIXTURES, LifeConfig, WorldConfig
from genesis.domain.state import Cell, Ledger, Organism, World
from genesis.engine import actions
from genesis.engine.ledger import reconcile
from genesis.engine.world import advance, apply_event, create_world


def org(energy=60, carried=0, x=2, y=2, id="org-0001"):
    return Organism(id, "TEST", "wait", 0, 0, x, y, energy, 500, carried_material=carried)


def lab(organisms, *, loose=0, packed=0, energy=10, schema=5, life=None):
    """5x5 world with no inflow; every cell holds the same resources."""
    life = life or LifeConfig(upkeep=0)
    config = WorldConfig(width=5, height=5, inflow_amount=0)
    cells = tuple(
        Cell(i % 5, i // 5, energy=energy, loose_material=loose, packed_material=packed)
        for i in range(25)
    )
    return World(
        config,
        cells,
        Ledger(
            initial_energy=energy * 25 + sum(o.energy for o in organisms),
            initial_material=(loose + packed) * 25 + sum(o.carried_material for o in organisms),
        ),
        schema=schema,
        life=life,
        organisms=tuple(organisms),
    )


def act(world, proposal, organism_id="org-0001"):
    return actions.resolve(world, world.organism(organism_id), proposal, {})


def totals(world):
    return world.total_energy, world.total_material


# -- extraction --------------------------------------------------------------------------


def test_extract_material_moves_units_and_charges_fee():
    world = lab([org()], loose=6)
    event = act(world, Proposal("extract", resource="material", amount=4))
    after = apply_event(world, event)
    o, cell = after.organism("org-0001"), after.cell_at(2, 2)
    assert (o.carried_material, cell.loose_material, o.energy) == (4, 2, 59)
    assert [(e.category, e.amount) for e in event.ledger] == [("extraction_fee", 1)]
    assert after.total_material == world.total_material  # material conserved exactly
    assert after.total_energy == world.total_energy - 1  # only the fee left the system
    assert reconcile(apply_event(world, event)).balanced


def test_extract_energy_moves_energy_from_cell_to_organism():
    world = lab([org(energy=50)], energy=30)
    event = act(world, Proposal("extract", resource="energy", amount=10))
    after = apply_event(world, event)
    assert (after.organism("org-0001").energy, after.cell_at(2, 2).energy) == (59, 20)
    assert after.total_energy == world.total_energy - 1


@pytest.mark.parametrize(
    "proposal,setup,reason",
    [
        (Proposal("extract", resource="material", amount=11), {}, "over_action_limit"),
        (Proposal("extract", resource="material", amount=0), {}, "invalid_arguments"),
        (Proposal("extract", resource="material", amount=-1), {}, "invalid_arguments"),
        (Proposal("extract", resource="material", amount=True), {}, "invalid_arguments"),
        (Proposal("extract", resource="material", amount=2.0), {}, "invalid_arguments"),
        (Proposal("extract", resource="gold", amount=1), {}, "invalid_arguments"),
        (Proposal("extract", resource="material"), {}, "invalid_arguments"),
        (Proposal("extract", resource="material", amount=1, mode="pack"), {}, "invalid_arguments"),
        (Proposal("extract", resource="material", amount=7), {}, "insufficient_source"),
        (Proposal("extract", resource="material", amount=3), {"carried": 18}, "over_capacity"),
        (Proposal("extract", resource="energy", amount=5), {"energy": 97}, "over_capacity"),
        (Proposal("extract", resource="energy", amount=11), {}, "over_action_limit"),
        (Proposal("extract", resource="material", amount=1), {"energy": 0}, "insufficient_energy"),
    ],
)
def test_invalid_extraction_is_rejected_whole_never_clamped(proposal, setup, reason):
    organism = org(energy=setup.get("energy", 60), carried=setup.get("carried", 0))
    if organism.energy == 0:
        organism = replace(organism, energy=0)
    world = lab([organism], loose=6, energy=10)
    event = act(world, proposal)
    assert event.type == "action_rejected" and event.summary["reason"] == reason
    assert event.changes == () and event.organism_changes == () and event.ledger == ()
    assert apply_event(world, event) == world


def test_fee_must_be_affordable_before_extracting_energy():
    world = lab([org(energy=0)], energy=50, life=LifeConfig(upkeep=0, extract_fee=1))
    event = act(world, Proposal("extract", resource="energy", amount=5))
    assert event.summary["reason"] == "insufficient_energy"  # cannot pay from the gain


# -- packing / unpacking ------------------------------------------------------------------


def test_pack_moves_carried_material_into_the_cell_and_raises_entry_cost():
    world = lab([org(carried=5)])
    assert actions.move_cost(world, 2, 2) == 1
    event = act(world, Proposal("modify", mode="pack", amount=2))
    after = apply_event(world, event)
    assert after.cell_at(2, 2).packed_material == 2
    assert after.organism("org-0001").carried_material == 3
    assert after.organism("org-0001").energy == 56  # 2 per unit
    assert after.total_material == world.total_material
    assert actions.move_cost(after, 2, 2) == 2
    assert event.summary["entry_cost_after"] == 2


@pytest.mark.parametrize(
    "proposal,setup,reason",
    [
        (Proposal("modify", mode="pack", amount=3), {}, "over_action_limit"),
        (Proposal("modify", mode="pack", amount=2), {"carried": 1}, "insufficient_source"),
        (Proposal("modify", mode="pack", amount=2), {"packed": 7}, "over_capacity"),
        (Proposal("modify", mode="unpack", amount=1), {}, "insufficient_source"),
        (
            Proposal("modify", mode="unpack", amount=2),
            {"packed": 4, "carried": 19},
            "over_capacity",
        ),
        (Proposal("modify", mode="pack", amount=2), {"energy": 3}, "insufficient_energy"),
        (Proposal("modify", mode="smash", amount=1), {}, "invalid_arguments"),
        (Proposal("modify", mode="pack", amount=0), {}, "invalid_arguments"),
        (Proposal("modify", mode="pack", amount=1, direction="east"), {}, "invalid_arguments"),
    ],
)
def test_invalid_modification_changes_nothing(proposal, setup, reason):
    world = lab(
        [org(energy=setup.get("energy", 60), carried=setup.get("carried", 5))],
        packed=setup.get("packed", 0),
    )
    event = act(world, proposal)
    assert event.summary["reason"] == reason
    assert apply_event(world, event) == world


def test_pack_unpack_round_trip_returns_material_and_dissipates_energy():
    world = lab([org(carried=4)])
    packed = apply_event(world, act(world, Proposal("modify", mode="pack", amount=2)))
    back = apply_event(packed, act(packed, Proposal("modify", mode="unpack", amount=2)))
    assert back.cells == world.cells
    assert back.organism("org-0001").carried_material == 4
    assert back.organism("org-0001").energy == 60 - 8
    assert back.ledger.dissipation == 8 and back.total_material == world.total_material
    assert reconcile(back).balanced


def test_movement_cost_depends_on_destination_packing():
    for packed, expected in [(0, 1), (1, 1), (2, 2), (3, 2), (8, 5)]:
        world = lab([org(x=1, y=2)], packed=packed)
        event = act(world, Proposal("move", direction="east"))
        assert event.summary["energy_cost"] == expected
        assert event.summary["entered_packed_material"] == packed


def test_entering_a_fully_packed_cell_can_become_unaffordable():
    world = lab([org(x=1, y=2, energy=4)], packed=8)
    assert act(world, Proposal("move", direction="east")).summary["reason"] == (
        "insufficient_energy"
    )


# -- death and persistence of change ----------------------------------------------------------


def test_death_retires_carried_material_and_leaves_packed_cells_intact():
    world = lab([org(energy=1, carried=7)], packed=3, life=LifeConfig(upkeep=1))
    result = advance(world, lambda policy, obs: Proposal("wait"))
    o = result.world.organism("org-0001")
    assert (o.alive, o.carried_material, o.retired_material) == (False, 0, 7)
    death = next(e for e in result.events if e.type == "organism_died")
    assert ("material", "retired", 7) in [(e.resource, e.category, e.amount) for e in death.ledger]
    assert result.world.cells == world.cells  # death never touches a cell
    assert result.world.ledger.retired_material == 7
    assert reconcile(result.world).balanced


def test_material_ledger_closes_after_every_event_of_the_workshop_fixture():
    world = create_world(
        WorldConfig.for_run(42, "material_workshop"), FIXTURES["material_workshop"].life
    )
    assert world.cell_at(10, 10).loose_material == 25  # fixture stockpile, recorded
    start_material = world.ledger.initial_material
    while not world.extinct:
        result = advance(world, fixture_policy.decide)
        replayed = world
        for event in result.events:
            replayed = replace(apply_event(replayed, event), tick=result.world.tick)
            assert reconcile(replayed).balanced
        world = result.world
        loose = sum(c.loose_material for c in world.cells)
        packed = sum(c.packed_material for c in world.cells)
        assert loose + packed + world.carried_material + world.ledger.retired_material == (
            start_material
        )
    assert world.cell_at(10, 10).packed_material == 8  # outlives its maker
    assert world.organisms[0].retired_material == 12


def test_workshop_script_exercises_every_rejection_reason():
    world = create_world(
        WorldConfig.for_run(42, "material_workshop"), FIXTURES["material_workshop"].life
    )
    reasons, rules = [], []
    while not world.extinct:
        result = advance(world, fixture_policy.decide)
        for e in result.events:
            if e.type == "action_rejected":
                reasons.append(e.summary["reason"])
            elif e.type == "action_accepted":
                rules.append(e.rule_id)
        world = result.world
    assert set(reasons) == {"over_action_limit", "over_capacity", "insufficient_energy"}
    assert {"action.extract.v1", "action.modify.v1", "action.move.v1"} <= set(rules)


def test_schema_4_worlds_keep_m04_rules_and_hashes():
    old = lab([org()], loose=6, schema=4, life=LifeConfig())
    event = act(old, Proposal("extract", resource="material", amount=1))
    assert event.summary["reason"] == "unknown_action"
    m04_dict = old.organisms[0].to_dict(4)
    assert "carried_material" not in m04_dict and "material_capacity" not in old.life.to_dict(4)
    assert old.state_hash() != replace(old, schema=5).state_hash()
    with pytest.raises(ValueError):
        replace(old, organisms=(org(carried=1),))
