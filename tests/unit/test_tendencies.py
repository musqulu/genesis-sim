"""M10: seeded starting tendencies, paired conditions, prompt path and behaviour counts."""

import json
import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.api.routes import live_seeds
from genesis.cognition.context import build_request
from genesis.cognition.fixture_policy import tendency_probe
from genesis.cognition.policy import parse
from genesis.cognition.prompts import PROMPT_V4, prompt_version, render
from genesis.cognition.session import decision_payload
from genesis.domain.actions import Proposal
from genesis.domain.config import FIXTURES, TENDENCIES, LifeConfig, SeedOrganism, WorldConfig
from genesis.domain.state import Organism
from genesis.engine.actions import available_actions
from genesis.engine.world import create_world, finish_tick, prepare_tick
from genesis.providers.scripted import ScriptedProvider


def live_world(mode="seeded", seed=42, count=10):
    life = LifeConfig(provider="mlx:test", seeds=live_seeds(seed, count, 16), tendency_mode=mode)
    return create_world(WorldConfig.for_run(seed, size=16), life)


def test_seeded_tendencies_are_varied_bounded_and_reproducible():
    world = live_world()
    vectors = [o.tendencies for o in world.organisms]
    assert all(len(v) == 6 and all(0 <= x <= 100 for x in v) for v in vectors)
    assert len(set(vectors)) == 10  # every organism differs
    assert len({v[i] for v in vectors for i in range(6)}) > 20  # spread, not a few values
    assert [o.tendencies for o in live_world().organisms] == vectors  # same seed, same draw
    assert [o.tendencies for o in live_world(seed=7).organisms] != vectors
    assert world.organisms[0].tendency_map() == dict(zip(TENDENCIES, vectors[0], strict=True))


def test_neutral_control_differs_only_in_tendencies():
    seeded, neutral = live_world("seeded"), live_world("neutral")
    assert all(o.tendencies == (50,) * 6 for o in neutral.organisms)
    assert seeded.cells == neutral.cells and seeded.rng_state == neutral.rng_state
    strip = [replace(o, tendencies=None) for o in seeded.organisms]
    assert strip == [replace(o, tendencies=None) for o in neutral.organisms]
    assert seeded.state_hash() != neutral.state_hash()  # the difference is recorded


def test_tendency_range_is_validated():
    with pytest.raises(ValueError):
        SeedOrganism(0, 0, "x", (50, 50, 50, 50, 50, 101))
    with pytest.raises(ValueError):
        SeedOrganism(0, 0, "x", (50, 50, 50))
    with pytest.raises(ValueError):
        Organism("org-0001", "T", "x", 0, 0, 0, 0, 60, 100, tendencies=(1, 2, 3, 4, 5, -1))
    with pytest.raises(ValueError):
        LifeConfig(tendency_mode="extrovert")


def test_paired_requests_differ_only_in_the_tendencies_field():
    contexts = []
    for mode in ("seeded", "neutral"):
        prepared = prepare_tick(live_world(mode))
        slot = prepared.slots[0]
        contexts.append(build_request("run", prepared.world, slot, None).context)
    seeded, neutral = contexts
    assert {k for k in seeded if seeded[k] != neutral[k]} == {"tendencies"}
    assert neutral["tendencies"] == dict.fromkeys(TENDENCIES, 50)


def test_prompt_v4_states_tendencies_are_inclinations_and_hides_others():
    prepared = prepare_tick(live_world())
    slot = prepared.slots[0]
    request = build_request("run", prepared.world, slot, None)
    assert prompt_version(request) == PROMPT_V4
    system, user = render(request)
    assert "not goals or rules" in system["content"]
    mine = prepared.world.organism(slot.organism_id).tendencies
    assert json.loads(user["content"])["tendencies"] == dict(zip(TENDENCIES, mine, strict=True))
    others = [o.tendency_map() for o in prepared.world.organisms if o.id != slot.organism_id]
    text = json.dumps(request.context)
    assert all(json.dumps(t) not in text for t in others)  # neighbours' tendencies private


def test_older_fixtures_have_no_tendencies_and_keep_their_context():
    world = create_world(WorldConfig.for_run(42, "exchange_pair"), FIXTURES["exchange_pair"].life)
    assert all(o.tendencies is None for o in world.organisms)
    prepared = prepare_tick(world)
    request = build_request("run", prepared.world, prepared.slots[0], None)
    assert "tendencies" not in request.context and prompt_version(request) == "decision-v3"


def test_a_tendency_sensitive_policy_changes_its_proposal_between_matched_fixtures():
    proposals = {}
    for name in ("tendency_social", "tendency_solitary"):
        world = create_world(WorldConfig.for_run(42, name), FIXTURES[name].life)
        prepared = prepare_tick(world)
        slot = next(s for s in prepared.slots if s.organism_id == "org-0001")
        proposals[name] = tendency_probe(build_request("run", prepared.world, slot, None).context)
    assert proposals["tendency_social"] == Proposal(
        "communicate", target="org-0002", text="Hello, neighbour."
    )
    assert proposals["tendency_solitary"] == Proposal("extract", resource="energy", amount=5)
    social = FIXTURES["tendency_social"].life
    solitary = FIXTURES["tendency_solitary"].life
    assert replace(social, seeds=()) == replace(solitary, seeds=())
    assert [s.tendencies for s in social.seeds] != [s.tendencies for s in solitary.seeds]


def test_tendencies_never_bypass_engine_constraints():
    world = create_world(
        WorldConfig.for_run(42, "tendency_social"), FIXTURES["tendency_social"].life
    )
    far = replace(
        world,
        organisms=tuple(replace(o, x=o.x + 3 * (o.id == "org-0002")) for o in world.organisms),
    )
    prepared = prepare_tick(far)
    outcomes = {
        s.organism_id: (Proposal("communicate", target="org-0002", text="hi"), {})
        if s.organism_id == "org-0001"
        else (Proposal("wait"), {})
        for s in prepared.slots
    }
    result = finish_tick(prepared, outcomes)
    (event,) = [
        e for e in result.events if e.actor_id == "org-0001" and e.type.startswith("action")
    ]
    assert event.summary["reason"] == "not_adjacent"  # affiliation 90 changes nothing here


def test_behaviour_stats_match_the_exchange_pair_fixture_totals(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3", dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "exchange_pair"})
        for n in range(9):
            client.post("/api/control", json={"command": "step", "command_id": f"stats-{n:05d}"})
        assert client.get("/api/world").json()["tick"] == 9
        population = client.get("/api/population").json()
        stats = {o["id"]: o["stats"] for o in population["organisms"]}
        giver, replier = stats["org-0001"], stats["org-0002"]
        assert giver["decisions"] == 9 and giver["rejected"] == 3 and giver["unparsed"] == 0
        assert giver["actions"] == {
            "extract": 1,
            "transfer": 2,
            "communicate": 1,
            "move": 1,
            "wait": 1,
        }
        assert giver["gifts_given"] == 2 and giver["messages_sent"] == 1
        assert giver["material_extracted"] == 5 and giver["cells_visited"] == 2
        assert giver["energy_spent_on_actions"] == 1 + 1 + 1 + 2 + 1
        assert replier["gifts_received"] == 2 and replier["messages_received"] == 1
        assert replier["messages_sent"] == 1 and giver["messages_received"] == 1
        assert population["organisms"][0]["tendencies"] is None  # an M09 fixture


def test_runs_can_be_created_with_neutral_tendencies_and_profiles_are_inspectable(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite3", dev_fixtures=True)) as client:
        frame = client.post("/api/runs", json={"seed": 42, "fixture": "tendency_social"}).json()
        assert frame["state_schema"] == 12
        detail = client.get("/api/organisms/org-0001").json()
        assert detail["profile"]["tendencies"]["affiliation"] == 90
        assert detail["profile"]["tendency_mode"] == "seeded"
        bad = client.post("/api/runs", json={"seed": 1, "tendencies": "extrovert"})
        assert bad.status_code == 422


def test_matched_fixture_runs_play_out_through_the_provider():
    for name, expected in (("tendency_social", "communicate"), ("tendency_solitary", "extract")):
        world = create_world(WorldConfig.for_run(42, name), FIXTURES[name].life)
        provider = ScriptedProvider({o.id: o.policy for o in world.organisms})
        prepared = prepare_tick(world)
        outcomes = {}
        for slot in prepared.slots:
            request = build_request("run", prepared.world, slot, None)
            raw = provider.decide(request, threading.Event()).raw_text
            parsed = parse(raw, prepared.world.life, available_actions(prepared.world))
            outcomes[slot.organism_id] = (
                parsed.outcome,
                decision_payload(slot, request, raw, parsed),
            )
        result = finish_tick(prepared, outcomes)
        (event,) = [
            e for e in result.events if e.actor_id == "org-0001" and e.type == "action_accepted"
        ]
        assert event.summary["action"] == expected
