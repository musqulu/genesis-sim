"""M13: selective, imperfect memory inheritance at birth."""

import json
import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.cognition.context import build_request
from genesis.cognition.session import decision_payload, parse_for
from genesis.domain.actions import Proposal
from genesis.domain.config import FIXTURES, LifeConfig, WorldConfig
from genesis.domain.state import Cell, Ledger, MemoryRecord, Organism, World
from genesis.engine.actions import available_actions
from genesis.engine.ledger import reconcile
from genesis.engine.world import advance, apply_event, create_world, finish_tick, prepare_tick
from genesis.providers.scripted import ScriptedProvider

QUIET = {
    "decision_interval": 1,
    "cognition_fee": 0,
    "trigger_threshold": 0,
    "reflection_enabled": False,
    "exchange_enabled": False,
}


def rec(owner, n, text="extracted 5 energy from this cell; fee 1 energy", kind="outcome", x=2):
    return MemoryRecord(
        f"{owner}-m{n:05d}", owner, kind, n, n, x, 4, text, (kind, "extract", "energy"), 2
    )


def lab(memories=(), energy=100, life=None, others=(), seed=7, retention=100):
    life = life or LifeConfig(**QUIET, inherit_memory_retention_percent=retention)
    parent = Organism(
        "org-0001",
        "TEST",
        "wait",
        0,
        0,
        4,
        4,
        energy,
        500,
        memories_written=max(
            (int(m.id[-5:]) for m in memories if m.owner_id == "org-0001"), default=0
        ),
        tendencies=(50,) * 6,
    )
    organisms = (parent, *others)
    return World(
        WorldConfig(width=9, height=9, inflow_amount=0, seed=seed),
        tuple(Cell(i % 9, i // 9) for i in range(81)),
        Ledger(initial_energy=sum(o.energy for o in organisms), initial_material=0),
        tick=20,
        life=life,
        organisms=tuple(sorted(organisms, key=lambda o: o.id)),
        memories=tuple(sorted(memories, key=lambda m: m.id)),
    )


def born(ids, endowment=30):
    return Proposal("reproduce", direction="east", endowment=endowment, memory_ids=ids)


def step(world, proposal):
    result = advance(
        world, lambda policy, obs: proposal if obs.organism_id == "org-0001" else Proposal("wait")
    )
    assert reconcile(result.world).balanced
    return result


def outcome(result):
    (event,) = [
        e for e in result.events if e.actor_id == "org-0001" and e.type.startswith("action")
    ]
    return event


def child_records(world):
    return [m for m in world.memories if m.owner_id == "org-0002"]


def test_selected_records_are_copied_with_origin_and_parent_records_unchanged():
    parents = [
        rec("org-0001", 1),
        rec("org-0001", 2, "cell: energy 90, loose material 25", "observation"),
    ]
    world = lab(parents)
    result = step(world, born(["org-0001-m00001", "org-0001-m00002"]))
    event = outcome(result)
    assert event.type == "action_accepted"
    copies = [m for m in child_records(result.world) if m.kind == "inherited"]
    assert [m.origin["source_id"] for m in copies] == ["org-0001-m00001", "org-0001-m00002"]
    first = copies[0]
    assert first.text.startswith("[from org-0001] ") and first.tick == 21 and first.age == 0
    assert (first.x, first.y) == (2, 4)  # where the parent experienced it
    assert first.origin == {
        "from": "org-0001",
        "source_id": "org-0001-m00001",
        "source_kind": "outcome",
        "source_tick": 1,
        "ancestor_id": "org-0001",
    }
    assert "inherited" in first.keywords and first.evidence == ()
    for original in parents:  # the parent keeps its own records untouched
        assert original in result.world.memories
    assert result.world.organism("org-0002").memories_written == 2


def test_transmission_fee_is_charged_per_started_kib_and_included_in_affordability():
    world = lab([rec("org-0001", 1)])
    event = outcome(step(world, born(["org-0001-m00001"])))
    assert ("inheritance_fee", 1) in [(e.category, e.amount) for e in event.ledger]
    assert event.summary["transmission_fee"] == 1 and event.summary["energy_cost"] == 11
    # Upkeep leaves 50 = fee 10 + endowment 30 + keep 10: affordable without memories only.
    poor = lab([rec("org-0001", 1)], energy=51)
    assert outcome(step(poor, born([]))).type == "action_accepted"
    assert outcome(step(poor, born(["org-0001-m00001"]))).summary["reason"] == "insufficient_energy"


@pytest.mark.parametrize(
    "ids,reason",
    [
        (["org-0001-m09999"], "invalid_memory"),  # unknown
        (["org-0003-m00001"], "invalid_memory"),  # another organism's
        (["org-0001-m00001", "org-0001-m00002", "org-0001-m00003"], "over_action_limit"),
        (["org-0001-m00001", "org-0001-m00001"], "invalid_arguments"),  # duplicate
    ],
)
def test_invalid_selections_reject_the_whole_birth(ids, reason):
    other = Organism("org-0003", "TEST", "wait", 0, 0, 8, 8, 60, 500, memories_written=1)
    world = lab(
        [rec("org-0001", n) for n in (1, 2, 3)] + [rec("org-0003", 1, x=8)], others=(other,)
    )
    result = step(world, born(ids))
    event = outcome(result)
    assert event.summary["reason"] == reason
    assert event.organism_changes == () and event.memory_changes == ()
    assert len(result.world.organisms) == 2


def test_forgotten_records_cannot_be_passed():
    world = lab([rec("org-0001", 2)])  # m00001 was forgotten (not active)
    assert outcome(step(world, born(["org-0001-m00001"]))).summary["reason"] == "invalid_memory"


def test_retention_is_seeded_partial_and_may_be_zero():
    parents = [rec("org-0001", 1), rec("org-0001", 2)]
    outcomes = set()
    for seed in range(30):
        result = step(
            lab(parents, seed=seed, retention=50), born(["org-0001-m00001", "org-0001-m00002"])
        )
        log = outcome(result).summary["inheritance"]["memories"]
        kept = [m.origin["source_id"] for m in child_records(result.world) if m.kind == "inherited"]
        assert kept == log["retained"] and set(log["retained"]) | set(log["dropped"]) == {
            "org-0001-m00001",
            "org-0001-m00002",
        }
        outcomes.add(len(kept))
        again = step(
            lab(parents, seed=seed, retention=50), born(["org-0001-m00001", "org-0001-m00002"])
        )
        assert outcome(again).summary["inheritance"] == outcome(result).summary["inheritance"]
    assert outcomes == {0, 1, 2}  # none, some and all occur across seeds


def test_zero_selection_gives_a_memoryless_child():
    result = step(lab([rec("org-0001", 1)]), born([]))
    assert child_records(result.world) == []
    assert outcome(result).summary["inheritance"]["memories"]["selected"] == []


def test_copies_are_truncated_to_the_record_limit():
    long = rec("org-0001", 1, "x" * 250)
    result = step(lab([long]), born(["org-0001-m00001"]))
    (copy,) = [m for m in child_records(result.world) if m.kind == "inherited"]
    assert len(copy.text.encode()) <= 256 and copy.text.startswith("[from org-0001] x")


def test_inherited_of_inherited_keeps_the_first_ancestor():
    grand = MemoryRecord(
        "org-0001-m00001",
        "org-0001",
        "inherited",
        5,
        0,
        2,
        4,
        "[from org-0000] old news",
        ("extract", "inherited"),
        2,
        origin={
            "from": "org-0000",
            "source_id": "org-0000-m00004",
            "source_kind": "outcome",
            "source_tick": 3,
            "ancestor_id": "org-0000",
        },
    )
    result = step(lab([grand]), born(["org-0001-m00001"]))
    (copy,) = [m for m in child_records(result.world) if m.kind == "inherited"]
    assert copy.origin["ancestor_id"] == "org-0000" and copy.origin["source_kind"] == "inherited"
    assert copy.text.startswith("[from org-0001] [from org-0000] old news")


def test_child_sees_inherited_claims_as_offsets_and_its_memory_evolves_alone():
    result = step(lab([rec("org-0001", 1)]), born(["org-0001-m00001"]))
    world = result.world
    prepared = prepare_tick(world)
    slot = next(s for s in prepared.slots if s.organism_id == "org-0002")
    request = build_request("run", prepared.world, slot, None)
    (given,) = [m for m in request.context["recalled"] if m["kind"] == "inherited"]
    assert given["inherited_from"] == "org-0001" and given["originally"] == "outcome"
    assert (given["dx"], given["dy"]) == (2 - 5, 0)  # from the child's cell (5, 4)
    assert "passed on by your parent" in request.context["memory"]["rules"]
    parent_before = [m for m in world.memories if m.owner_id == "org-0001"]
    later = step(world, Proposal("wait")).world
    assert len(child_records(later)) > 1  # its own experience accumulates
    assert [m for m in later.memories if m.owner_id == "org-0001" and m.tick <= 20] == [
        m for m in parent_before if m.tick <= 20
    ]


def test_no_private_parent_state_leaks_into_the_child():
    result = step(lab([rec("org-0001", 1)]), born(["org-0001-m00001"]))
    child = result.world.organism("org-0002")
    copies = [m.to_dict() for m in child_records(result.world)]
    allowed = {
        "id",
        "owner_id",
        "kind",
        "tick",
        "age",
        "x",
        "y",
        "text",
        "keywords",
        "salience",
        "origin",
    }
    assert copies and all(set(c) <= allowed for c in copies)  # no lifespan, PRNG or plans
    assert all(
        set(c["origin"]) == {"from", "source_id", "source_kind", "source_tick", "ancestor_id"}
        for c in copies
    )
    # Fresh cognition state: nothing of the parent's schedule, reflection or inbox carries over.
    assert child.last_decision_tick is None and child.reflected_at_tick is None
    assert child.messages_sent == 0 and result.world.inbox_of(child.id) == ()


def test_dead_ancestors_are_reachable_only_through_copied_content():
    result = step(lab([rec("org-0001", 1)]), born(["org-0001-m00001"]))
    world = replace(
        result.world,
        organisms=tuple(
            replace(o, alive=False, energy=0, death_tick=21, death_cause="age")
            if o.id == "org-0001"
            else o
            for o in result.world.organisms
        ),
    )
    prepared = prepare_tick(replace(world, tick=21))
    slot = next(s for s in prepared.slots if s.organism_id == "org-0002")
    request = build_request("run", prepared.world, slot, None)
    text = json.dumps(request.context)
    assert "[from org-0001]" in text  # the copy
    assert '"source_id"' not in text and "org-0001-m00001" not in text  # no live reference


def test_fixture_shows_invalid_paths_partial_inheritance_and_replays_exactly(tmp_path):
    world = create_world(WorldConfig.for_run(42, "birth_memory"), FIXTURES["birth_memory"].life)
    provider = ScriptedProvider({o.id: o.policy for o in world.organisms})
    initial, history = world, []
    for _ in range(14):
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
            if event.type == "organism_born" and event.tick > 0:
                provider.adopt(event.actor_id, event.summary["parent_id"], event.summary["policy"])
        history.append(result)
        world = result.world
    reasons = [
        e.summary.get("reason")
        for r in history
        for e in r.events
        if e.actor_id == "org-0001" and e.summary.get("action") == "reproduce"
    ]
    assert reasons == ["invalid_memory", "over_action_limit", None]
    log = next(
        e.summary["inheritance"]["memories"]
        for r in history
        for e in r.events
        if e.type == "organism_born" and e.tick > 0
    )
    assert len(log["selected"]) == 2 and len(log["retained"]) == 1 and len(log["dropped"]) == 1
    replayed = initial
    for result in history:
        for event in result.events:
            replayed = apply_event(replayed, event)
        replayed = replace(replayed, tick=result.world.tick)
    assert replayed == world and replayed.state_hash() == world.state_hash()
    with TestClient(create_app(tmp_path / "db.sqlite3", dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "birth_memory"})
        for n in range(12):
            client.post("/api/control", json={"command": "step", "command_id": f"inh-{n:05d}"})
        detail = client.get("/api/organisms/org-0002").json()
        assert detail["inheritance"]["parent_id"] == "org-0001"
        assert detail["inheritance"]["inheritance"]["memories"]["retained"] == log["retained"]
        inherited = [m for m in detail["memory"]["active"] if m["kind"] == "inherited"]
        assert inherited and inherited[0]["origin"]["from"] == "org-0001"
        assert client.get("/api/organisms/org-0001").json()["inheritance"] is None
