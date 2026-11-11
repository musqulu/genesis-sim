"""M08: costly active memory and bounded recall (engine + cognition, no persistence)."""

import json
from dataclasses import replace

import pytest
from genesis.cognition.context import build_request
from genesis.cognition.fixture_policy import recall_return
from genesis.cognition.prompts import PROMPT_V1, PROMPT_V2, prompt_version, render
from genesis.cognition.retrieval import query_keywords, recall
from genesis.domain.actions import Proposal
from genesis.domain.config import FIXTURES, LifeConfig, SeedOrganism, WorldConfig
from genesis.domain.state import Cell, Ledger, MemoryRecord, Organism, World
from genesis.engine import memory
from genesis.engine.ledger import reconcile
from genesis.engine.memory import MemoryView, truncate
from genesis.engine.world import advance, apply_event, create_world, finish_tick, prepare_tick

EVERY_TICK = {
    "decision_interval": 1,
    "cognition_fee": 0,
    "trigger_threshold": 0,
    "exchange_enabled": False,  # M08 memory tests; exchange is M09
}
SENTINEL = "SENTINEL-PRIVATE-MEMORY-7731"


def lab(organisms, life=None, width=7, height=7, energy=10, memories=()):
    life = life or LifeConfig(**EVERY_TICK)
    config = WorldConfig(width=width, height=height, inflow_amount=0)
    cells = tuple(Cell(i % width, i // width, energy=energy) for i in range(width * height))
    return World(
        config,
        cells,
        ledger=Ledger(
            initial_energy=energy * width * height + sum(o.energy for o in organisms),
            initial_material=0,
        ),
        life=life,
        organisms=tuple(sorted(organisms, key=lambda o: o.id)),
        memories=tuple(sorted(memories, key=lambda m: m.id)),
    )


def org(id="org-0001", x=3, y=3, energy=60, written=0):
    return Organism(id, "TEST", "wait", 0, 0, x, y, energy, 500, memories_written=written)


def rec(owner="org-0001", n=1, tick=0, x=3, y=3, text="cell: energy 1", kind="observation"):
    return MemoryRecord(
        f"{owner}-m{n:05d}",
        owner,
        kind,
        tick,
        tick,
        x,
        y,
        text,
        ("observation", "energy"),
        memory.SALIENCE[kind],
    )


def always(proposal):
    return lambda policy, observation: proposal


def step(world, proposal):
    result = advance(world, always(proposal))
    assert reconcile(result.world).balanced
    return result


def of_type(result, kind):
    return [e for e in result.events if e.type == kind]


# -- writing ------------------------------------------------------------------------------


def test_a_decision_writes_an_observation_and_an_accepted_outcome_for_a_fee():
    result = step(lab([org()]), Proposal("wait"))
    (written,) = of_type(result, "memory_written")
    assert [c.op for c in written.memory_changes] == ["add", "add"]
    kinds = [c.record["kind"] for c in written.memory_changes]
    assert kinds == ["observation", "outcome"]
    assert written.memory_changes[0].record["text"].startswith("cell: energy 10")
    assert written.memory_changes[1].record["text"] == "waited"
    assert [(e.category, e.amount) for e in written.ledger] == [("memory_write_fee", 1)]
    o = result.world.organism("org-0001")
    assert o.energy == 60 - 1 - 1 and o.memories_written == 2  # upkeep 1 + write fee 1
    assert [m.id for m in result.world.memories] == ["org-0001-m00001", "org-0001-m00002"]
    assert written.phase == "memory" and written.rule_id == "memory.write.v1"


def test_write_fee_is_charged_per_started_kib():
    life = LifeConfig(**EVERY_TICK, memory_write_fee_per_kib=3)
    result = step(lab([org()], life), Proposal("wait"))
    (written,) = of_type(result, "memory_written")
    assert written.summary["bytes"] < 1024 and written.summary["fee"] == 3


def test_rejected_and_unparsed_proposals_create_no_outcome_memory():
    world = lab([org(x=0, y=0)])
    result = step(world, Proposal("move", "west"))  # off-grid: rejected
    (written,) = of_type(result, "memory_written")
    assert [c.record["kind"] for c in written.memory_changes] == ["observation"]


def test_identical_observation_of_the_same_cell_is_not_written_again():
    world = step(lab([org()]), Proposal("wait")).world
    result = step(world, Proposal("wait"))
    (written,) = of_type(result, "memory_written")
    assert [c.record["kind"] for c in written.memory_changes] == ["outcome"]


def test_unaffordable_write_is_skipped_with_a_reason_and_changes_nothing():
    world = lab([org(energy=2)], LifeConfig(**EVERY_TICK, upkeep=1))
    result = step(world, Proposal("wait"))  # upkeep leaves 1 energy; the fee would empty it
    (skipped,) = of_type(result, "memory_skipped")
    assert skipped.phase == "diagnostic" and skipped.summary["reason"] == "insufficient_energy"
    assert skipped.organism_changes == () and skipped.memory_changes == () and not skipped.ledger
    assert result.world.memories == () and result.world.organism("org-0001").energy == 1


def test_eviction_forgets_oldest_acquisitions_first_with_stable_id_ties():
    life = LifeConfig(**EVERY_TICK, memory_max_records=3)
    old = [rec(n=2, tick=0, x=0), rec(n=1, tick=0, x=1), rec(n=3, tick=1, x=2)]
    world = replace(lab([org(written=3)], life, memories=old), tick=2)
    result = step(world, Proposal("wait"))
    (written,) = of_type(result, "memory_written")
    forgotten = [c.record["id"] for c in written.memory_changes if c.op == "forget"]
    assert forgotten == ["org-0001-m00001", "org-0001-m00002"]  # tick 0, then id order
    assert written.summary["forgotten"] == forgotten
    assert len(result.world.memories_of("org-0001")) == 3
    # The forget delta carries the full record, so history keeps what active state lost.
    assert written.memory_changes[0].record == old[1].to_dict()


def test_byte_budget_also_forces_eviction():
    life = LifeConfig(**EVERY_TICK, memory_max_bytes=520)
    old = [rec(n=i, tick=i, x=i % 7, text="x" * 250) for i in (1, 2)]
    world = replace(lab([org(written=2)], life, memories=old), tick=3)
    result = step(world, Proposal("wait"))
    assert sum(m.size for m in result.world.memories_of("org-0001")) <= 520
    assert "org-0001-m00001" not in {m.id for m in result.world.memories}


def test_truncation_respects_utf8_boundaries():
    assert truncate("ab€", 4) == "ab"  # € is three bytes and would be split
    assert truncate("ab€", 5) == "ab€"
    assert len(truncate("é" * 300, 256).encode()) <= 256


def test_world_rejects_memory_beyond_its_limits_or_without_owner():
    life = LifeConfig(**EVERY_TICK, memory_max_records=2)
    with pytest.raises(ValueError, match="exceeds its limits"):
        lab([org()], life, memories=[rec(n=i, tick=i) for i in (1, 2, 3)])
    with pytest.raises(ValueError, match="no owner"):
        lab([org()], memories=[rec(owner="org-0009")])
    with pytest.raises(ValueError, match="per-record"):
        lab([org()], memories=[rec(text="y" * 300)])


# -- upkeep -------------------------------------------------------------------------------


def test_upkeep_charges_per_started_kib_on_its_interval_only():
    memories = [rec(n=i, tick=0, x=i, text="z" * 200) for i in range(1, 7)]  # 1200 bytes
    world = replace(lab([org(written=6)], memories=memories), tick=8)
    quiet = step(world, Proposal("wait"))  # tick 9
    assert not of_type(quiet, "memory_upkeep")
    charged = step(quiet.world, Proposal("wait"))  # tick 10
    (upkeep,) = of_type(charged, "memory_upkeep")
    # 1200 bytes plus the records written at tick 9: two started KiB.
    assert 1200 < upkeep.payload["charges"]["org-0001"]["bytes"] <= 2048
    assert upkeep.summary["charged_total"] == 2
    assert [(e.category, e.amount) for e in upkeep.ledger] == [("memory_upkeep", 2)]


def test_upkeep_that_empties_an_organism_starves_it():
    memories = [rec(n=1, tick=0, text="z" * 100)]
    life = LifeConfig(**EVERY_TICK, upkeep=0, memory_upkeep_per_kib=5)
    world = replace(lab([org(energy=3, written=1)], life, memories=memories), tick=9)
    result = step(world, Proposal("wait"))
    (upkeep,) = of_type(result, "memory_upkeep")
    assert upkeep.payload["charges"]["org-0001"] == {"bytes": 100, "due": 5, "charged": 3}
    o = result.world.organism("org-0001")
    assert (o.alive, o.death_cause) == (False, "starvation")


# -- perception boundary and isolation ----------------------------------------------------


def test_cognition_sees_only_its_own_records_as_offsets_without_coordinates():
    others = rec(owner="org-0002", n=1, text=SENTINEL, x=6, y=6)
    mine = rec(n=1, x=1, y=3, tick=0)
    world = replace(
        lab([org(written=1), org("org-0002", x=6, y=6, written=1)], memories=[mine, others]),
        tick=4,
    )
    prepared = prepare_tick(world)
    slot = next(s for s in prepared.slots if s.organism_id == "org-0001")
    request = build_request("run", prepared.world, slot, None)
    text = json.dumps(request.context)
    assert SENTINEL not in text
    (recalled,) = request.context["recalled"]
    assert recalled == {
        "id": "org-0001-m00001",
        "kind": "observation",
        "ticks_ago": 5,
        "age_then": 0,
        "dx": -2,
        "dy": 0,
        "text": "cell: energy 1",
    }
    assert '"x"' not in json.dumps(request.context["recalled"])
    assert request.context["memory"]["active_records"] == 1


def test_remembered_values_are_never_refreshed_from_the_world():
    world = step(lab([org()]), Proposal("wait")).world  # remembers energy 10 at (3, 3)
    cells = list(world.cells)
    cells[3 * 7 + 3] = replace(cells[3 * 7 + 3], energy=0)  # the world changes there later
    changed = replace(
        world,
        cells=tuple(cells),
        ledger=replace(world.ledger, initial_energy=world.ledger.initial_energy - 10),
    )
    (record,) = [m for m in changed.memories if m.kind == "observation"]
    view = memory.views(changed, changed.organism("org-0001"))
    assert "energy 10" in record.text and any("energy 10" in v.text for v in view)


# -- recall -------------------------------------------------------------------------------


def view(id, ticks_ago=0, keywords=("observation",), salience=1, size=10):
    return MemoryView(id, "observation", ticks_ago, 0, 0, 0, "t" * size, keywords, salience, size)


def test_recall_with_no_memory_is_empty():
    result = recall((), ("energy",), 8, 2048)
    assert result.selected == () and result.to_dict()["candidates"] == 0


def test_equal_scores_fall_back_to_stable_id_order():
    views = (view("a-m00003"), view("a-m00001"), view("a-m00002"))
    result = recall(views, (), 8, 2048)
    assert [v.id for v in result.selected] == ["a-m00001", "a-m00002", "a-m00003"]
    assert all(s.recency == 1.0 and s.relevance == 0.0 for s in result.ranked)


def test_recency_relevance_and_salience_rank_records():
    old_relevant = view("a-m00001", ticks_ago=10, keywords=("observation", "material"))
    new_plain = view("a-m00002", ticks_ago=0)
    salient = view("a-m00003", ticks_ago=10, salience=2)
    result = recall((old_relevant, new_plain, salient), ("material",), 8, 2048)
    scores = {s.view.id: s for s in result.ranked}
    assert scores["a-m00001"].relevance == 1.0 and scores["a-m00002"].recency == 1.0
    assert scores["a-m00003"].salience == 1.0 and scores["a-m00001"].salience == 0.5
    # m00001 (0 + 1 + 0.5) ties m00002 (1 + 0 + 0.5): fewer ticks ago wins the tie.
    assert [s.view.id for s in result.ranked] == ["a-m00002", "a-m00001", "a-m00003"]


def test_recall_is_bounded_by_count_and_bytes():
    views = tuple(view(f"a-m{n:05d}", ticks_ago=n, size=100) for n in range(1, 21))
    assert len(recall(views, (), 8, 10_000).selected) == 8
    assert len(recall(views, (), 8, 350).selected) == 3


def test_query_uses_the_current_cell_and_the_last_accepted_action():
    prepared = prepare_tick(lab([org()]))
    obs = prepared.slots[0].observation
    accepted = {"tick": 1, "outcome": "accepted", "action": "move"}
    rejected = {"tick": 1, "outcome": "rejected", "reason": "occupied"}
    assert query_keywords(obs, accepted) == ("energy", "move")
    assert query_keywords(obs, rejected) == ("energy",)


# -- request context, prompt and schema compatibility ------------------------------------


def test_memory_free_runs_keep_the_m06_context_and_prompt():
    world = lab([org()], LifeConfig(**EVERY_TICK, memory_enabled=False))
    prepared = prepare_tick(world)
    slot = prepared.slots[0]
    assert slot.memories is None
    request = build_request("run", prepared.world, slot, None)
    assert set(request.context) == {
        "observation",
        "previous_result",
        "action_schema",
        "response_format",
    }
    assert prompt_version(request) == PROMPT_V1 and request.recall is None
    with_memory = build_request(
        "run", prepare_tick(lab([org()])).world, prepare_tick(lab([org()])).slots[0], None
    )
    assert prompt_version(with_memory) == PROMPT_V2
    messages = render(with_memory)
    assert "limited memory" in messages[0]["content"]
    assert '"recalled":[]' in messages[1]["content"]


def test_schema_6_worlds_have_no_memory_rules_applied():
    world = replace(lab([org()]), schema=6)
    result = step(world, Proposal("wait"))
    assert not of_type(result, "memory_written") and result.world.memories == ()


# -- memory-sensitive fixture policy ------------------------------------------------------


def test_recorded_memory_policy_chooses_differently_with_and_without_a_recalled_record():
    stockpile = {
        "id": "org-0001-m00001",
        "kind": "observation",
        "ticks_ago": 4,
        "age_then": 1,
        "dx": -3,
        "dy": 0,
        "text": "cell: energy 4, loose material 25, packed material 0",
    }
    observation = {
        "age": 5,
        "carried_material": 0,
        "cells": [{"dx": 0, "dy": 0, "energy": 3, "loose_material": 0, "packed_material": 0}],
    }
    assert recall_return({"observation": observation, "recalled": [stockpile]}) == Proposal(
        "move", direction="west"
    )
    assert recall_return({"observation": observation, "recalled": []}) == Proposal("wait")


def test_memory_return_fixture_walks_back_through_recall_and_replays_exactly():
    from genesis.cognition.context import build_request as build
    from genesis.cognition.policy import parse
    from genesis.cognition.session import decision_payload
    from genesis.engine.actions import available_actions

    world = create_world(WorldConfig.for_run(42, "memory_return"), FIXTURES["memory_return"].life)
    initial, history, previous = world, [], {}
    for _ in range(8):
        prepared = prepare_tick(world)
        outcomes = {}
        for slot in prepared.slots:
            request = build("run", prepared.world, slot, previous.get(slot.organism_id))
            raw = json.dumps(recall_return(request.context).to_dict())
            parsed = parse(raw, prepared.world.life, available_actions(prepared.world))
            outcomes[slot.organism_id] = (
                parsed.outcome,
                decision_payload(slot, request, raw, parsed),
            )
        result = finish_tick(prepared, outcomes)
        history.append(result)
        for event in result.events:
            if event.type.startswith("action_"):
                previous[event.actor_id] = {
                    "outcome": "accepted",
                    "action": event.summary["action"],
                }
        world = result.world
    o = world.organism("org-0001")
    assert (o.x, o.y, o.carried_material) == (10, 10, 5)  # back at the remembered stockpile
    # Replay from recorded deltas only reproduces the state, active memory included.
    replayed = initial
    for result in history:
        for event in result.events:
            replayed = apply_event(replayed, event)
        replayed = replace(replayed, tick=result.world.tick)
    assert replayed == world and replayed.state_hash() == world.state_hash()


def test_memory_config_requires_room_for_one_decision():
    with pytest.raises(ValueError):
        LifeConfig(memory_max_records=1)
    with pytest.raises(ValueError):
        LifeConfig(memory_max_bytes=100)
    assert FIXTURES["memory_budget"].life.memory_max_records == 6
    assert FIXTURES["corner_perception"].life.memory_enabled is False
    assert LifeConfig().to_dict(6).keys() < LifeConfig().to_dict(7).keys()
    assert SeedOrganism(0, 0, "x")  # unchanged contract
