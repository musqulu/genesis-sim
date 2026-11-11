"""M12: agent-initiated birth and imperfect trait inheritance."""

import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.cognition.context import build_request
from genesis.cognition.policy import parse
from genesis.cognition.session import decision_payload, parse_for
from genesis.domain.actions import Proposal
from genesis.domain.config import FIXTURES, LifeConfig, WorldConfig
from genesis.domain.state import Cell, Ledger, Organism, World
from genesis.engine import inheritance
from genesis.engine.actions import available_actions
from genesis.engine.ledger import reconcile
from genesis.engine.world import advance, apply_event, create_world, finish_tick, prepare_tick
from genesis.providers.scripted import ScriptedProvider

QUIET = {
    "decision_interval": 1,
    "cognition_fee": 0,
    "trigger_threshold": 0,
    "memory_enabled": False,
    "reflection_enabled": False,
}
TEND = (80, 20, 60, 40, 70, 30)


def org(id="org-0001", x=4, y=4, energy=100, carried=0, tendencies=TEND, **kw):
    return Organism(
        id,
        "TEST",
        "wait",
        0,
        0,
        x,
        y,
        energy,
        500,
        carried_material=carried,
        tendencies=tendencies,
        **kw,
    )


def lab(organisms, life=None, tick=0):
    life = life or LifeConfig(**QUIET)
    config = WorldConfig(width=9, height=9, inflow_amount=0, seed=7)
    cells = tuple(Cell(i % 9, i // 9) for i in range(81))
    return World(
        config,
        cells,
        Ledger(
            initial_energy=sum(o.energy for o in organisms),
            initial_material=sum(o.carried_material for o in organisms),
        ),
        tick=tick,
        life=life,
        organisms=tuple(sorted(organisms, key=lambda o: o.id)),
    )


def plan(by_actor):
    return lambda policy, obs: by_actor.get(obs.organism_id, Proposal("wait"))


def born(direction="east", endowment=40, material=None):
    return Proposal("reproduce", direction=direction, endowment=endowment, material=material)


def step(world, by_actor):
    result = advance(world, plan(by_actor))
    assert reconcile(result.world).balanced
    return result


def outcome(result, actor="org-0001"):
    (event,) = [e for e in result.events if e.actor_id == actor and e.type.startswith("action_")]
    return event


def test_birth_is_atomic_and_charges_exact_costs():
    result = step(lab([org(carried=6)]), {"org-0001": born(material=4)})
    event = outcome(result)
    assert event.rule_id == "action.reproduce.v1" and event.summary["child_id"] == "org-0002"
    parent, child = result.world.organism("org-0001"), result.world.organism("org-0002")
    assert parent.energy == 100 - 1 - 10 - 40 and parent.carried_material == 2  # upkeep, fee
    assert (child.energy, child.carried_material, child.x, child.y) == (40, 4, 5, 4)
    assert child.generation == 1 and child.parent_ids == ("org-0001",) and child.birth_tick == 1
    assert [(e.category, e.amount) for e in event.ledger] == [("reproduction_fee", 10)]
    assert parent.last_reproduced_tick == 1
    (birth,) = [e for e in result.events if e.type == "organism_born"]
    assert birth.actor_id == "org-0002" and birth.summary["parent_id"] == "org-0001"
    assert "lifespan" not in str(birth.summary)  # the child's deadline stays hidden


def test_child_is_independent_and_first_acts_next_tick():
    first = step(lab([org()]), {"org-0001": born()})
    assert {s.organism_id for s in prepare_tick(lab([org()])).slots} == {"org-0001"}
    child = first.world.organism("org-0002")
    assert child.memories_written == 0 and child.last_decision_tick is None
    second = prepare_tick(first.world)
    assert {s.organism_id for s in second.slots} == {"org-0001", "org-0002"}
    after = step(first.world, {"org-0002": Proposal("move", "east")})
    assert after.world.organism("org-0001").x == 4  # moving the child never moves the parent


@pytest.mark.parametrize(
    "proposal,setup,reason",
    [
        (born(endowment=19), {}, "endowment_too_small"),
        (born(endowment=101), {}, "over_capacity"),
        (born(material=21), {"carried": 20}, "over_capacity"),
        (born(direction="up"), {}, "invalid_arguments"),
        (Proposal("reproduce", direction="east", endowment="40"), {}, "invalid_arguments"),
        (born(), {"last_reproduced_tick": 0}, "cooldown"),
        (born(), {"energy": 60}, "insufficient_energy"),  # upkeep -> 59 < 10 + 40 + 10
        (born(material=3), {"carried": 2}, "insufficient_source"),
        (born(direction="north"), {"y": 0}, "out_of_bounds"),
    ],
)
def test_invalid_births_change_nothing(proposal, setup, reason):
    world = lab([org(**setup)], tick=5)
    result = step(world, {"org-0001": proposal})
    event = outcome(result)
    assert event.summary["reason"] == reason
    assert event.organism_changes == () and not event.ledger
    assert len(result.world.organisms) == 1


def test_cooldown_boundary_and_retained_energy_boundary():
    world = lab([org(last_reproduced_tick=0)], tick=19)  # next tick 20: 20 - 0 >= 20
    assert outcome(step(world, {"org-0001": born()})).type == "action_accepted"
    exact = lab([org(energy=61)])  # after upkeep 60 = 10 + 40 + 10: allowed, keeps 10
    result = step(exact, {"org-0001": born()})
    assert outcome(result).type == "action_accepted"
    assert result.world.organism("org-0001").energy == 10


def test_occupied_target_and_two_parents_contesting_one_cell():
    blocked = lab([org(), org("org-0002", 5, 4)])
    assert outcome(step(blocked, {"org-0001": born()})).summary["reason"] == "occupied"
    a, b = org("org-0001", 3, 4), org("org-0002", 5, 4)
    result = step(lab([a, b]), {"org-0001": born("east"), "org-0002": born("west")})
    events = [e for e in result.events if e.type.startswith("action_")]
    accepted = [e for e in events if e.type == "action_accepted"]
    rejected = [e for e in events if e.type == "action_rejected"]
    assert len(accepted) == 1 and rejected[0].summary["reason"] == "occupied"
    assert len(result.world.organisms) == 3 and result.world.organism("org-0003").x == 4


def test_inheritance_is_partial_perturbed_clipped_and_reproducible():
    world = lab([org(tendencies=(100, 0, 50, 50, 50, 50))])
    a = step(world, {"org-0001": born()})
    b = step(world, {"org-0001": born()})
    log = outcome(a).summary["inheritance"]
    assert log == outcome(b).summary["inheritance"]  # same seed and child id, same draws
    child = a.world.organism("org-0002").tendencies
    assert child == tuple(log["child"]) and all(0 <= v <= 100 for v in child)
    for keep, parent, value, draw in zip(
        log["mask"], log["parent"], child, log["draws"], strict=True
    ):
        if keep:
            assert abs(value - parent) <= 10 or value in (0, 100)
            assert value == max(0, min(100, parent + draw["delta"]))
        else:
            assert value == draw["fresh"]
    rates = []
    for seed in range(40):
        w = replace(world, config=replace(world.config, seed=seed))
        rates += outcome(step(w, {"org-0001": born()})).summary["inheritance"]["mask"]
    assert 0.3 < sum(rates) / len(rates) < 0.7  # about half inherited over many births
    none = lab([org(tendencies=None)])
    assert step(none, {"org-0001": born()}).world.organism("org-0002").tendencies is None


def test_inheritance_settings_are_respected():
    life = LifeConfig(**QUIET, inherit_probability_percent=100, inherit_perturbation=0)
    child = step(lab([org()], life), {"org-0001": born()}).world.organism("org-0002")
    assert child.tendencies == TEND
    life = LifeConfig(**QUIET, inherit_probability_percent=0)
    log = outcome(step(lab([org()], life), {"org-0001": born()})).summary["inheritance"]
    assert log["mask"] == [False] * 6


def test_no_birth_without_a_choice_and_no_reseed_on_extinction():
    lonely = lab([org(energy=3)])
    world = lonely
    for _ in range(5):
        if world.extinct:
            break
        world = step(world, {}).world
    assert world.extinct and len(world.organisms) == 1  # nobody is created to rescue it
    off = lab([org()], LifeConfig(**QUIET, reproduction_enabled=False))
    assert "reproduce" not in available_actions(off)
    assert (
        parse(
            '{"action":"reproduce","direction":"east","endowment":40}',
            off.life,
            available_actions(off),
        ).status
        == "schema_invalid"
    )


def test_parent_death_leaves_child_and_ancestry_intact():
    first = step(lab([org(energy=61)]), {"org-0001": born()})  # parent keeps exactly 10
    world = first.world
    parent = world.organism("org-0001")
    world = replace(
        world,
        organisms=tuple(
            replace(o, lifespan=world.tick + 1 - o.birth_tick) if o.id == "org-0001" else o
            for o in world.organisms
        ),
    )
    after = step(world, {}).world
    assert not after.organism("org-0001").alive and after.organism("org-0002").alive
    assert after.organism("org-0002").parent_ids == ("org-0001",)
    assert parent.energy == 10 and after.organism("org-0001").death_cause == "age"


def test_child_ids_follow_the_highest_existing_id():
    world = lab([org("org-0001"), org("org-0007", 7, 7)])
    assert inheritance.child_id(world) == "org-0008"


def test_birth_fixture_replays_identically_with_lineage():
    world = create_world(WorldConfig.for_run(42, "birth_pair"), FIXTURES["birth_pair"].life)
    provider = ScriptedProvider({o.id: o.policy for o in world.organisms})
    initial, history = world, []
    for _ in range(60):
        if world.extinct:
            break
        prepared = prepare_tick(world)
        outcomes = {}
        for slot in prepared.slots:
            request = build_request("run", prepared.world, slot, None)
            raw = provider.decide(request, threading.Event()).raw_text
            parsed = parse_for(request, raw, prepared.world.life, available_actions(prepared.world))
            outcomes[slot.organism_id] = (
                parsed.outcome,
                decision_payload(slot, request, raw, parsed),
            )
        result = finish_tick(prepared, outcomes)
        for event in result.events:
            if event.type == "organism_born":
                provider.adopt(event.actor_id, event.summary["parent_id"], event.summary["policy"])
        history.append(result)
        world = result.world
    reasons = [
        e.summary["reason"]
        for r in history
        for e in r.events
        if e.actor_id == "org-0001" and e.type == "action_rejected"
    ]
    assert reasons == ["cooldown", "endowment_too_small", "occupied", "insufficient_energy"]
    child = world.organism("org-0002")
    assert child.parent_ids == ("org-0001",) and child.generation == 1
    assert world.extinct and len(world.organisms) == 2  # extinction is not followed by reseeding
    replayed = initial
    for result in history:
        for event in result.events:
            replayed = apply_event(replayed, event)
        replayed = replace(replayed, tick=result.world.tick)
    assert replayed == world and replayed.state_hash() == world.state_hash()


def test_api_frame_shows_lineage_birth_arrow_and_hides_child_lifespan(tmp_path):
    database = tmp_path / "db.sqlite3"
    with TestClient(create_app(database, dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "birth_pair"})
        frame = client.post(
            "/api/control", json={"command": "step", "command_id": "birth-00001"}
        ).json()
        assert frame["schema_version"] == 9
        (arrow,) = frame["interactions"]
        assert arrow["kind"] == "reproduce" and arrow["to_id"] == "org-0002"
        child = next(o for o in frame["organisms"] if o["id"] == "org-0002")
        assert child["parent_ids"] == ["org-0001"] and child["generation"] == 1
        assert child["lifespan"] is None
        events = client.get("/api/events", params={"limit": 50}).json()["events"]
        accepted = next(e for e in events if e["rule_id"] == "action.reproduce.v1")
        detail = client.get(f"/api/events/{accepted['sequence']}").json()
        record = next(c for c in detail["organism_changes"] if c["field"] == "record")
        assert record["after"]["lifespan"] is None  # not revealed while it lives
        stats = client.get("/api/population").json()["organisms"]
        assert next(o for o in stats if o["id"] == "org-0001")["stats"]["children"] == 1
        client.post("/api/control", json={"command": "start", "command_id": "birth-00002"})
        deadline = time.monotonic() + 30
        while not client.get("/api/world").json()["extinct"] and time.monotonic() < deadline:
            time.sleep(0.02)
        live_hash = client.get("/api/world").json()["state_hash"]
    from genesis.persistence.sqlite import SqliteStore

    store = SqliteStore(database)
    store.initialize()
    loaded = store.load_active()
    assert loaded.world.state_hash() == live_hash and len(loaded.world.organisms) == 2
