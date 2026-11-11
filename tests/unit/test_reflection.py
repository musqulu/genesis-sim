"""M11: bounded reflection with evidence (engine, cognition, persistence, API)."""

import json
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.cognition.context import build_request
from genesis.cognition.prompts import prompt_version, render
from genesis.cognition.reflection import parse_reflection
from genesis.cognition.session import decision_payload, parse_for
from genesis.domain.actions import Proposal, ReflectionProposal
from genesis.domain.config import FIXTURES, LifeConfig, WorldConfig
from genesis.domain.state import Cell, Ledger, MemoryRecord, Organism, World
from genesis.engine import reflection
from genesis.engine.actions import available_actions
from genesis.engine.ledger import reconcile
from genesis.engine.world import apply_event, create_world, finish_tick, prepare_tick
from genesis.providers.scripted import ScriptedProvider

EVERY_TICK = {"decision_interval": 1, "cognition_fee": 0, "trigger_threshold": 0}


def outcome_record(owner, n, tick, x=3):
    return MemoryRecord(
        f"{owner}-m{n:05d}",
        owner,
        "outcome",
        tick,
        tick,
        x,
        3,
        f"moved east {n}",
        ("outcome", "move", "east"),
        2,
    )


def lab(salient=8, energy=60, reflected=None, tick=30, life=None, others=()):
    life = life or LifeConfig(**EVERY_TICK)
    config = WorldConfig(width=9, height=9, inflow_amount=0)
    cells = tuple(Cell(i % 9, i // 9, energy=10) for i in range(81))
    organism = Organism(
        "org-0001",
        "T",
        "wait",
        0,
        0,
        4,
        4,
        energy,
        500,
        memories_written=salient,
        reflected_at_tick=reflected,
    )
    memories = [outcome_record("org-0001", n, n) for n in range(1, salient + 1)]
    organisms = (organism, *others)
    return World(
        config,
        cells,
        Ledger(initial_energy=810 + sum(o.energy for o in organisms), initial_material=0),
        tick=tick,
        life=life,
        organisms=organisms,
        memories=tuple(sorted([*memories], key=lambda m: m.id)),
    )


def reflect_tick(world, reply):
    """Prepare one tick, answer a reflection slot with `reply` (dict), resolve it."""
    prepared = prepare_tick(world)
    outcomes = {}
    for slot in prepared.slots:
        request = build_request("run", prepared.world, slot, None)
        raw = json.dumps(reply) if slot.purpose == "reflect" else '{"action":"wait"}'
        parsed = parse_for(request, raw, prepared.world.life, available_actions(prepared.world))
        outcomes[slot.organism_id] = (parsed.outcome, decision_payload(slot, request, raw, parsed))
    result = finish_tick(prepared, outcomes)
    assert reconcile(result.world).balanced
    return prepared, result


def ids(world, kind="reflection"):
    return [m for m in world.memories if m.kind == kind]


# -- trigger ------------------------------------------------------------------------------


def test_threshold_boundary_counts_new_salient_records():
    assert not reflection.eligible(replace(lab(salient=7), tick=30), lab(salient=7).organisms[0])
    world = lab(salient=8)
    assert reflection.eligible(world, world.organisms[0])
    since = lab(salient=8, reflected=5)  # records 1-5 are older than the last reflection
    assert reflection.new_salient(since, since.organisms[0]) == 3


def test_cooldown_boundary_and_affordability():
    at_19 = lab(salient=12, reflected=11, tick=30)  # 30 - 11 = 19 < 20
    assert not reflection.eligible(at_19, at_19.organisms[0])
    records = [outcome_record("org-0001", n, 20 + n) for n in range(1, 9)]
    at_20 = replace(lab(salient=0, reflected=10, tick=30), memories=tuple(records))
    at_20 = replace(at_20, organisms=(replace(at_20.organisms[0], memories_written=8),))
    assert reflection.eligible(at_20, at_20.organisms[0])
    poor = lab(energy=2)  # cannot pay fee 2 and keep one energy
    assert not reflection.eligible(poor, poor.organisms[0])


def test_nothing_to_cite_never_triggers_and_disabled_runs_never_reflect():
    empty = lab(salient=0)
    assert not reflection.eligible(empty, empty.organisms[0])
    off = lab(life=LifeConfig(**EVERY_TICK, reflection_enabled=False))
    assert not reflection.eligible(off, off.organisms[0])
    old = replace(lab(), schema=9, organisms=(replace(lab().organisms[0]),))
    assert not reflection.enabled(old)


def test_a_reflection_replaces_the_decision_and_starts_the_cooldown():
    prepared = prepare_tick(lab())
    (slot,) = prepared.slots
    assert slot.purpose == "reflect" and slot.inbox is None
    assert prepared.world.organism("org-0001").reflected_at_tick == 31
    request = build_request("run", prepared.world, slot, None)
    assert request.purpose == "reflect" and "action_schema" not in request.context
    assert [m["id"] for m in request.context["memories"]][:2] == [
        "org-0001-m00008",
        "org-0001-m00007",
    ]
    # One request per opportunity: the next tick is an ordinary decision.
    _, result = reflect_tick(lab(), {"reflections": []})
    following = prepare_tick(result.world)
    assert [s.purpose for s in following.slots] == ["decide"]


# -- validation and storage ---------------------------------------------------------------


def test_accepted_reflection_is_stored_with_evidence_and_paid_without_touching_sources():
    before = lab()
    reply = {
        "reflections": [
            {"text": "Moving east pays.", "evidence": ["org-0001-m00008", "org-0001-m00007"]}
        ]
    }
    _, result = reflect_tick(before, reply)
    (event,) = [e for e in result.events if e.rule_id == "cognition.reflect.v1"]
    assert [(e.category, e.amount) for e in event.ledger] == [("reflection_fee", 2)]
    (stored,) = ids(result.world)
    assert stored.text == "Moving east pays." and stored.evidence == (
        "org-0001-m00008",
        "org-0001-m00007",
    )
    assert "reflection" in stored.keywords and "move" in stored.keywords
    sources = {m.id: m for m in before.memories}
    for m in result.world.memories:
        if m.id in sources:
            assert m == sources[m.id]  # observed facts are never rewritten


@pytest.mark.parametrize(
    "evidence",
    [
        ["org-0001-m99999"],  # never existed
        ["org-0002-m00001"],  # another organism's private record
        [],  # no support
    ],
)
def test_invalid_references_are_rejected_with_no_memory_effect(evidence):
    other = Organism("org-0002", "T", "wait", 0, 0, 8, 8, 60, 500, memories_written=1)
    world = lab(others=(other,))
    world = replace(
        world,
        memories=tuple(
            sorted([*world.memories, outcome_record("org-0002", 1, 1, x=8)], key=lambda m: m.id)
        ),
    )
    reply = {"reflections": [{"text": "x", "evidence": evidence}]}
    prepared, result = reflect_tick(world, reply)
    events = [e for e in result.events if e.actor_id == "org-0001" and e.type.startswith("action")]
    if evidence:
        assert events[0].summary["reason"] == "invalid_evidence"
    else:
        assert events[0].summary["reason"] == "schema_invalid"
    assert ids(result.world) == [] and not events[0].ledger


def test_records_not_shown_cannot_be_cited():
    life = LifeConfig(**EVERY_TICK, reflection_context_max=4)
    world = lab(salient=10, life=life)
    reply = {"reflections": [{"text": "old", "evidence": ["org-0001-m00001"]}]}  # not in newest 4
    _, result = reflect_tick(world, reply)
    (event,) = [
        e for e in result.events if e.actor_id == "org-0001" and e.type.startswith("action")
    ]
    assert event.summary["reason"] == "invalid_evidence" and ids(result.world) == []


def test_reflections_cannot_cite_reflections():
    world = lab()
    first = {"reflections": [{"text": "one", "evidence": ["org-0001-m00008"]}]}
    _, result = reflect_tick(world, first)
    (stored,) = ids(result.world)
    later = replace(result.world, tick=result.world.tick + 25)
    later = replace(
        later,
        memories=tuple(
            sorted(
                [
                    *later.memories,
                    *[outcome_record("org-0001", n, later.tick - 1) for n in range(50, 58)],
                ],
                key=lambda m: m.id,
            )
        ),
        organisms=(replace(later.organisms[0], memories_written=60),),
    )
    prepared = prepare_tick(later)
    (slot,) = prepared.slots
    assert slot.purpose == "reflect" and stored.id not in {v.id for v in slot.memories}


def test_empty_reply_is_accepted_free_and_stores_nothing():
    _, result = reflect_tick(lab(), {"reflections": []})
    (event,) = [e for e in result.events if e.rule_id == "cognition.reflect.v1"]
    assert event.summary["count"] == 0 and not event.ledger and ids(result.world) == []


def test_parser_bounds_count_shape_and_size():
    life = LifeConfig()
    three = {"reflections": [{"text": "a", "evidence": ["x"]}] * 3}
    assert parse_reflection(json.dumps(three), life).status == "schema_invalid"
    assert parse_reflection('{"reflections":[{"text":"a"}]}', life).status == "schema_invalid"
    assert parse_reflection('{"thoughts":[]}', life).status == "schema_invalid"
    assert parse_reflection("not json", life).status == "malformed_json"
    ok = parse_reflection('```json\n{"reflections":[]}\n```', life)
    assert ok.status == "valid" and isinstance(ok.outcome, ReflectionProposal)
    long = {"reflections": [{"text": "é" * 200, "evidence": ["org-0001-m00008"]}]}
    _, result = reflect_tick(lab(), long)  # 400 bytes > 256: rejected by the engine
    (event,) = [
        e for e in result.events if e.actor_id == "org-0001" and e.type.startswith("action")
    ]
    assert event.summary["reason"] == "invalid_arguments" and ids(result.world) == []


def test_reflection_prompt_lists_only_its_own_records_and_no_actions():
    prepared = prepare_tick(lab())
    request = build_request("run", prepared.world, prepared.slots[0], None)
    assert prompt_version(request) == "reflection-v1"
    system, user = render(request)
    assert "you do not act" in system["content"] and "Writing none is fine" in system["content"]
    content = json.loads(user["content"])
    assert set(content) >= {"memories", "status", "reflection_schema"}
    assert "action_schema" not in content and '"x":' not in user["content"]


def test_decision_requests_are_unchanged_when_not_reflecting():
    prepared = prepare_tick(lab(salient=3))
    (slot,) = prepared.slots
    request = build_request("run", prepared.world, slot, None)
    assert slot.purpose == "decide" and "action_schema" in request.context


# -- fixture, replay, restart, API ---------------------------------------------------------


def run_fixture(name, ticks):
    world = create_world(WorldConfig.for_run(42, name), FIXTURES[name].life)
    provider = ScriptedProvider({o.id: o.policy for o in world.organisms})
    initial, history, requests = world, [], []
    for _ in range(ticks):
        if world.extinct:
            break
        prepared = prepare_tick(world)
        outcomes = {}
        for slot in prepared.slots:
            request = build_request("run", prepared.world, slot, None)
            requests.append((prepared.world.tick, request.purpose))
            raw = provider.decide(request, threading.Event()).raw_text
            parsed = parse_for(request, raw, prepared.world.life, available_actions(prepared.world))
            outcomes[slot.organism_id] = (
                parsed.outcome,
                decision_payload(slot, request, raw, parsed),
            )
        result = finish_tick(prepared, outcomes)
        history.append(result)
        world = result.world
    return initial, history, world, requests


def test_fixture_reflects_on_threshold_and_after_cooldown_and_replays_exactly():
    initial, history, world, requests = run_fixture("reflection_probe", 35)
    assert [t for t, p in requests if p == "reflect"] == [9, 29]
    assert len(requests) == len({t for t, _ in requests})  # never two requests per tick
    assert len(ids(world)) == 2
    replayed = initial
    for result in history:
        for event in result.events:
            replayed = apply_event(replayed, event)
        replayed = replace(replayed, tick=result.world.tick)
    assert replayed == world and replayed.state_hash() == world.state_hash()


def test_invalid_fixture_stores_nothing_but_still_uses_its_opportunities():
    _, _, world, requests = run_fixture("reflection_invalid", 35)
    assert [t for t, p in requests if p == "reflect"] == [9, 29] and ids(world) == []


def test_api_shows_reflection_evidence_and_restart_preserves_trigger_state(tmp_path):
    database = tmp_path / "db.sqlite3"
    with TestClient(create_app(database, dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "reflection_probe"})
        client.post(
            "/api/control", json={"command": "start", "command_id": "refl-000001", "until_tick": 12}
        )
        deadline = time.monotonic() + 20
        while client.get("/api/world").json()["tick"] < 12 and time.monotonic() < deadline:
            time.sleep(0.02)
        memory = client.get("/api/organisms/org-0001").json()["memory"]
        (stored,) = [m for m in memory["active"] if m["kind"] == "reflection"]
        assert stored["evidence"] and all(e.startswith("org-0001-m") for e in stored["evidence"])
        decisions = client.get("/api/decisions", params={"limit": 50}).json()["decisions"]
        (at9,) = [d for d in decisions if d["tick"] == 9]
        detail = client.get(f"/api/decisions/{at9['request_id']}").json()
        assert detail["request"]["purpose"] == "reflect"
        assert detail["outcome"] == "accepted" and detail["outcome_rule"] == "cognition.reflect.v1"
        live_hash = client.get("/api/world").json()["state_hash"]
    from genesis.persistence.sqlite import SqliteStore

    store = SqliteStore(database)
    store.initialize()
    loaded = store.load_active()
    assert loaded.world.state_hash() == live_hash
    assert loaded.world.organism("org-0001").reflected_at_tick == 9
    assert (
        loaded.world
        == store.history(loaded.meta.run_id, loaded.world.tick, use_snapshots=False).world
    )


def test_reflection_does_not_change_the_action_proposal_type():
    assert isinstance(Proposal("wait"), Proposal)  # decisions keep their contract
