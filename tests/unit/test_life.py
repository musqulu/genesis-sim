"""M04 engine: perception, validation, lifecycle and accounting (no persistence)."""

from dataclasses import replace

import pytest
from genesis.cognition import fixture_policy
from genesis.domain.actions import Proposal
from genesis.domain.config import FIXTURES, LifeConfig, SeedOrganism, WorldConfig
from genesis.domain.state import Cell, Ledger, Organism, World
from genesis.engine.ledger import reconcile
from genesis.engine.perception import Observation, observe
from genesis.engine.world import ExtinctRun, advance, apply_event, create_world

SENTINEL_MATERIAL = 987_654
SENTINEL_LIFESPAN = 13_579


def fixture_world(name: str, seed: int = 42) -> World:
    return create_world(WorldConfig.for_run(seed, name), FIXTURES[name].life)


def scripted(*proposals):
    """A decide() that replays fixed proposals and records what it was shown."""
    seen = []

    def decide(policy, observation):
        seen.append(observation)
        return proposals[min(len(seen) - 1, len(proposals) - 1)]

    decide.seen = seen
    return decide


# M04 mechanics under M06 rules: a decision every tick, no cognition fee, no triggers.
EVERY_TICK = {
    "decision_interval": 1,
    "cognition_fee": 0,
    "trigger_threshold": 0,
    "memory_enabled": False,
}


def lab(organisms, life=None, width=5, height=5, energy=10, sentinel_outside=None):
    """Hand-built world with explicit organisms; independent of seeding."""
    life = life or LifeConfig(**EVERY_TICK)
    config = WorldConfig(width=width, height=height, inflow_amount=0)
    cells = tuple(Cell(i % width, i // width, energy=energy) for i in range(width * height))
    world = World(
        config,
        cells,
        ledger=Ledger(
            initial_energy=energy * width * height + sum(o.energy for o in organisms),
            initial_material=0,
        ),
        life=life,
        organisms=tuple(sorted(organisms, key=lambda o: o.id)),
    )
    return world


def org(id="org-0001", x=2, y=2, energy=60, lifespan=200, birth=0):
    return Organism(id, "TEST", "wait", birth, 0, x, y, energy, lifespan)


def run(world, decide, ticks):
    events = []
    for _ in range(ticks):
        result = advance(world, decide)
        world = result.world
        events.extend(result.events)
    return world, events


# -- perception --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "x,y,expected",
    [(0, 0, 6), (31, 31, 6), (0, 15, 9), (31, 15, 9), (15, 0, 9), (15, 15, 13), (1, 1, 11)],
)
def test_perception_is_manhattan_radius_two_clipped_at_edges(x, y, expected):
    world = create_world(WorldConfig(), LifeConfig(seeds=(SeedOrganism(x, y, "wait"),)))
    obs = observe(world, world.organisms[0])
    assert len(obs.cells) == expected
    assert all(abs(c.dx) + abs(c.dy) <= 2 for c in obs.cells)
    for c in obs.cells:
        cell = world.cell_at(x + c.dx, y + c.dy)
        assert (c.energy, c.loose_material, c.packed_material) == (
            cell.energy,
            cell.loose_material,
            cell.packed_material,
        )


def test_corner_observation_offsets_are_exact():
    world = fixture_world("corner_perception")
    obs = observe(world, world.organisms[0])
    assert {(c.dx, c.dy) for c in obs.cells} == {
        (0, 0),
        (1, 0),
        (2, 0),
        (0, 1),
        (1, 1),
        (0, 2),
    }


def test_hidden_fields_never_leak_into_observations():
    me = org(lifespan=SENTINEL_LIFESPAN)
    near = org("org-0002", x=3, y=2)  # distance 1: visible
    far = org("org-0003", x=8, y=8)  # outside radius: invisible
    world = lab([me, near, far], width=12, height=12)
    cells = list(world.cells)
    for i, cell in enumerate(cells):
        if abs(cell.x - 2) + abs(cell.y - 2) > 2:
            cells[i] = replace(cell, loose_material=SENTINEL_MATERIAL)
    world = replace(
        world,
        cells=tuple(cells),
        ledger=replace(
            world.ledger,
            initial_material=SENTINEL_MATERIAL * sum(1 for c in cells if c.loose_material),
        ),
        tick=77,
    )
    obs = observe(world, world.organism("org-0001"))
    text = obs.canonical()
    assert str(SENTINEL_MATERIAL) not in text
    assert str(SENTINEL_LIFESPAN) not in text and str(me.deadline) not in text
    assert "org-0003" not in text and "org-0002" in text
    assert set(obs.to_dict()) == {
        "organism_id",
        "age",
        "energy",
        "energy_capacity",
        "carried_material",
        "material_capacity",
        "upkeep_per_tick",
        "perception_radius",
        "life_note",
        "actions",
        "cells",
        "organisms",
    }  # no tick, seed, lifespan/deadline, ledger, PRNG, absolute position or world size
    assert set(obs.to_dict()["cells"][0]) == {
        "dx",
        "dy",
        "energy",
        "loose_material",
        "packed_material",
        "occupied",
    }
    assert obs.age == 77 and "unknown" in obs.life_note
    assert [(o.id, o.dx, o.dy) for o in obs.organisms] == [("org-0002", 1, 0)]
    assert next(c for c in obs.cells if (c.dx, c.dy) == (1, 0)).occupied


def test_policies_receive_only_observation_dtos():
    decide = scripted(Proposal("wait"))
    world = fixture_world("corner_perception")
    run(world, decide, 3)
    assert decide.seen and all(type(o) is Observation for o in decide.seen)
    with pytest.raises(TypeError):
        fixture_policy.POLICIES["wait"](world)  # a world object is refused


# -- validation ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "proposal,reason",
    [
        (Proposal("move", "west"), "out_of_bounds"),
        (Proposal("move", "north"), "out_of_bounds"),
        (Proposal("move", "up"), "invalid_arguments"),
        (Proposal("move", None), "invalid_arguments"),
        (Proposal("wait", "east"), "invalid_arguments"),
        (Proposal("teleport"), "unknown_action"),
        (Proposal(None), "unknown_action"),
        (Proposal(["move"]), "unknown_action"),
    ],
)
def test_invalid_proposals_change_nothing_but_produce_a_diagnostic(proposal, reason):
    world = lab([org(x=0, y=0)])
    rejected_tick = advance(world, scripted(proposal))
    waited_tick = advance(world, scripted(Proposal("wait")))
    (rejection,) = [e for e in rejected_tick.events if e.type == "action_rejected"]
    assert rejection.summary["reason"] == reason and rejection.phase == "diagnostic"
    assert rejection.changes == () and rejection.organism_changes == () and rejection.ledger == ()
    # Whole-tick comparison: identical to waiting (upkeep is independent, not a penalty).
    assert rejected_tick.world == waited_tick.world


def test_move_into_occupied_cell_and_unaffordable_move_are_rejected():
    world = lab([org(x=2, y=2), org("org-0002", x=3, y=2)])
    by_id = {"org-0001": Proposal("move", "east"), "org-0002": Proposal("wait")}
    result = advance(world, lambda policy, obs: by_id[obs.organism_id])
    # Resolution order rotates by tick: at tick 1, org-0002 resolves first.
    acted = [e.actor_id for e in result.events if e.type.startswith("action")]
    assert acted == ["org-0002", "org-0001"]
    rejected = [e for e in result.events if e.type == "action_rejected"]
    assert [e.summary["reason"] for e in rejected] == ["occupied"]
    assert result.world.organism("org-0001").x == 2
    poor = lab([org(energy=1)], life=LifeConfig(**EVERY_TICK, upkeep=0, move_base_cost=2))
    result = advance(poor, scripted(Proposal("move", "east")))
    assert result.events[-1].summary["reason"] == "insufficient_energy"
    assert result.world.organism("org-0001").energy == 1


def test_valid_move_costs_entry_fee_and_moves_exactly_one_cell():
    world = lab([org(energy=10)])
    result = advance(world, scripted(Proposal("move", "south")))
    o = result.world.organism("org-0001")
    assert (o.x, o.y, o.energy) == (2, 3, 8)  # upkeep 1 + move 1
    move = next(e for e in result.events if e.rule_id == "action.move.v1")
    assert [(c.field, c.before, c.after) for c in move.organism_changes] == [
        ("x", 2, 2),
        ("y", 2, 3),
        ("energy", 9, 8),
    ]
    assert [(e.category, e.amount) for e in move.ledger] == [("movement", 1)]
    packed = list(world.cells)
    packed[3 * 5 + 2] = replace(packed[3 * 5 + 2], packed_material=5)
    costly = replace(
        world,
        cells=tuple(packed),
        ledger=replace(world.ledger, initial_material=5),
    )
    result = advance(costly, scripted(Proposal("move", "south")))
    assert result.world.organism("org-0001").energy == 10 - 1 - (1 + 5 // 2)


# -- lifecycle ----------------------------------------------------------------------------


def test_age_death_happens_exactly_at_the_hidden_deadline():
    world = lab([org(lifespan=5, energy=60)])
    world, _ = run(world, scripted(Proposal("wait")), 4)
    assert world.organism("org-0001").alive
    result = advance(world, scripted(Proposal("wait")))
    o = result.world.organism("org-0001")
    assert (o.alive, o.death_tick, o.death_cause) == (False, 5, "age")
    death = next(e for e in result.events if e.type == "organism_died")
    assert death.rule_id == "life.age_limit.v1" and o.retired_energy == 56
    assert [(e.category, e.amount) for e in death.ledger] == [("death_retirement", 56)]
    assert not any(e.type == "upkeep" and e.summary["charged_total"] for e in result.events)


def test_starvation_when_upkeep_takes_the_last_energy():
    world = lab([org(energy=2)])
    world, events = run(world, scripted(Proposal("wait")), 2)
    o = world.organism("org-0001")
    assert (o.alive, o.death_cause, o.death_tick, o.retired_energy) == (False, "starvation", 2, 0)


def test_exhaustion_when_a_valid_action_spends_the_last_energy():
    world = lab([org(energy=2)])
    result = advance(world, scripted(Proposal("move", "east")))
    o = result.world.organism("org-0001")
    assert (o.x, o.alive, o.death_cause) == (3, False, "exhaustion")
    assert [e.type for e in result.events] == [
        "upkeep",
        "decision_round",
        "action_accepted",
        "organism_died",
        "extinction",
    ]


def test_no_decisions_or_actions_after_death_and_dead_ids_are_terminal():
    world = lab([org(energy=3)])
    decide = scripted(Proposal("move", "east"))
    world, _ = run(world, decide, 2)  # tick1: 3-1-1=1; tick2: upkeep ->0 starvation
    assert not world.organism("org-0001").alive and len(decide.seen) == 1
    with pytest.raises(ExtinctRun):
        advance(world, decide)
    assert len(decide.seen) == 1
    dead = world.organism("org-0001")
    with pytest.raises(ValueError):
        replace(dead, alive=True)  # a dead record cannot be revived with its death record


def test_death_never_changes_any_cell():
    world = lab([org(energy=1)])
    result = advance(world, scripted(Proposal("wait")))
    assert result.world.cells == world.cells
    assert not result.world.organism("org-0001").alive


def test_ledger_balances_after_every_event_through_death():
    world = fixture_world("starvation")
    decide = fixture_policy.decide
    while not world.extinct:
        result = advance(world, decide)
        replayed = world
        for event in result.events:
            replayed = replace(apply_event(replayed, event), tick=result.world.tick)
            assert reconcile(replayed).balanced
        world = result.world
    ledger = world.ledger
    assert ledger.dissipation > 0
    assert world.total_energy == ledger.expected_energy


def test_extinction_is_recorded_exactly_once_and_never_reseeded():
    world = fixture_world("age_limit")
    events = []
    while not world.extinct:
        result = advance(world, fixture_policy.decide)
        world = result.world
        events += result.events
    assert [e.type for e in events].count("extinction") == 1
    assert len(world.organisms) == 1 and not world.living
    for _ in range(3):
        with pytest.raises(ExtinctRun):
            advance(world, fixture_policy.decide)
    assert len(world.organisms) == 1


def test_organism_free_runs_are_never_extinct_and_need_no_cognition():
    world = create_world(WorldConfig())
    assert not world.extinct
    world, events = run(world, None, 20)
    assert world.tick == 20 and {e.type for e in events} == {"energy_inflow"}


def test_lifespans_are_sampled_within_bounds_and_hidden_but_recorded():
    lifespans = {
        create_world(WorldConfig(seed=s), LifeConfig(seeds=(SeedOrganism(1, 1, "wait"),)))
        .organisms[0]
        .lifespan
        for s in range(200)
    }
    assert min(lifespans) >= 160 and max(lifespans) <= 240 and len(lifespans) > 30
    a = fixture_world("age_limit")
    assert 20 <= a.organisms[0].lifespan <= 30
    assert a == fixture_world("age_limit")  # same seed -> same recorded draw
    assert a.state_hash() != fixture_world("age_limit", seed=7).state_hash()


def test_fixture_runs_are_deterministic_to_extinction():
    finals = []
    for _ in range(2):
        world = fixture_world("starvation")
        hashes = []
        while not world.extinct:
            world = advance(world, fixture_policy.decide).world
            hashes.append(world.state_hash())
        finals.append(hashes)
    assert finals[0] == finals[1]


def test_initial_energy_includes_seed_endowment():
    world = fixture_world("corner_perception")
    assert world.ledger.initial_energy == world.cell_energy + 60
    assert reconcile(world).balanced
