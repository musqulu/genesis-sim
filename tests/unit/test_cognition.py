"""M06 cognition contract: context, strict parsing, cadence and recorded providers."""

import json
import random
import threading
from dataclasses import replace

import pytest
from genesis.cognition.context import action_schema, build_request
from genesis.cognition.policy import parse
from genesis.domain.actions import Proposal, Unparsed
from genesis.domain.config import FIXTURES, LifeConfig, SeedOrganism, WorldConfig
from genesis.engine import cadence
from genesis.engine.actions import available_actions
from genesis.engine.world import advance, create_world, prepare_tick
from genesis.providers import record
from genesis.providers.base import ProviderError
from genesis.providers.recorded import RecordedProvider

LIFE = LifeConfig()
ACTIONS = ("wait", "move", "extract", "modify")


def fixture_world(name):
    return create_world(WorldConfig.for_run(42, name), FIXTURES[name].life)


# -- parsing ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,status",
    [
        ('{"action": "wait"}', "valid"),
        ('{"action": "move", "direction": "east", "explanation": "go"}', "valid"),
        ('{"action": "extract", "resource": "energy", "amount": 10}', "valid"),
        ('{"action": "modify", "mode": "unpack", "amount": 2}', "valid"),
        ("", "empty_output"),
        ("   \n ", "empty_output"),
        ('{"action": "move", "direction": ', "malformed_json"),
        ("not json at all", "malformed_json"),
        ('{"action": "wait", "action": "move"}', "malformed_json"),  # duplicate key
        ("[1, 2]", "not_an_object"),
        ('"wait"', "not_an_object"),
        ("null", "not_an_object"),
        ('{"action": "teleport"}', "schema_invalid"),
        ('{"action": 5}', "schema_invalid"),
        ('{"direction": "east"}', "schema_invalid"),
        ('{"action": "wait", "mood": "calm"}', "schema_invalid"),
        ('{"action": "move"}', "schema_invalid"),
        ('{"action": "move", "direction": "up"}', "schema_invalid"),
        ('{"action": "extract", "resource": "material", "amount": "ten"}', "schema_invalid"),
        ('{"action": "extract", "resource": "material", "amount": 5.0}', "schema_invalid"),
        ('{"action": "extract", "resource": "material", "amount": true}', "schema_invalid"),
        ('{"action": "extract", "resource": "material", "amount": 0}', "schema_invalid"),
        ('{"action": "extract", "resource": "material", "amount": 11}', "schema_invalid"),
        ('{"action": "modify", "mode": "pack", "amount": 3}', "schema_invalid"),
        ('{"action": "wait", "explanation": "' + "x" * 281 + '"}', "schema_invalid"),
        ('{"action": "wait"}' + " " * 5000, "output_too_long"),
    ],
)
def test_parse_classifies_raw_output_strictly(raw, status):
    result = parse(raw, LIFE, ACTIONS)
    assert result.status == status
    if status == "valid":
        assert isinstance(result.outcome, Proposal)
    else:
        assert isinstance(result.outcome, Unparsed) and result.outcome.reason == status


def test_parse_respects_the_runs_actions_and_limits():
    assert (
        parse(
            '{"action": "extract", "resource": "energy", "amount": 1}', LIFE, ("wait", "move")
        ).status
        == "schema_invalid"
    )
    tight = LifeConfig(extract_max=3)
    assert (
        parse('{"action": "extract", "resource": "energy", "amount": 4}', tight, ACTIONS).status
        == "schema_invalid"
    )


def test_schema_fuzzing_never_crashes_and_never_invents_a_valid_proposal():
    """Mutate valid outputs and random strings; every result is classified, and anything
    classified valid really satisfies the schema."""
    rng = random.Random(6)
    seeds = [
        '{"action": "wait"}',
        '{"action": "move", "direction": "north"}',
        '{"action": "extract", "resource": "material", "amount": 4}',
        '{"action": "modify", "mode": "pack", "amount": 1}',
    ]
    alphabet = '{}[]":,0123456789-.eE truefalsnul\\actionmoveextract' + "\x00é"
    valid = 0
    for i in range(4000):
        text = rng.choice(seeds)
        chars = list(text)
        for _ in range(rng.randint(1, 4)):
            op = rng.random()
            pos = rng.randrange(len(chars) + 1)
            if op < 0.4 and chars:
                del chars[min(pos, len(chars) - 1)]
            elif op < 0.8:
                chars.insert(pos, rng.choice(alphabet))
            elif chars:
                chars[min(pos, len(chars) - 1)] = rng.choice(alphabet)
        if i % 5 == 0:
            chars = [rng.choice(alphabet) for _ in range(rng.randint(0, 40))]
        result = parse("".join(chars), LIFE, ACTIONS)
        assert result.status in {
            "valid",
            "empty_output",
            "malformed_json",
            "not_an_object",
            "schema_invalid",
            "output_too_long",
        }
        if result.status == "valid":
            valid += 1
            data = json.loads("".join(chars))
            p = result.outcome
            assert set(data) <= {"action", "direction", "resource", "mode", "amount", "explanation"}
            assert p.action in ACTIONS
            if p.amount is not None:
                assert type(p.amount) is int and 1 <= p.amount <= 10
    assert valid > 0  # the fuzzer also produced some still-valid outputs


# -- context and request ------------------------------------------------------------------


def _plain(value) -> bool:
    if isinstance(value, dict):
        return all(isinstance(k, str) and _plain(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_plain(v) for v in value)
    return value is None or type(value) in (str, int, float, bool)


def test_request_contains_only_plain_data_and_a_binding_hash():
    world = fixture_world("recorded_valid")
    prepared = prepare_tick(world)
    (slot,) = prepared.slots
    request = build_request("run-a", prepared.world, slot, None)
    assert _plain(request.context) and _plain(request.to_dict())
    assert set(request.context) == {
        "observation",
        "previous_result",
        "action_schema",
        "response_format",
    }
    text = json.dumps(request.context)
    assert "lifespan" not in text and "rng" not in text and "ledger" not in text
    same = build_request("run-b", prepared.world, slot, None)
    assert same.input_hash == request.input_hash  # run id is not part of the input
    other = build_request("run-a", prepared.world, slot, {"tick": 1, "outcome": "accepted"})
    assert other.input_hash != request.input_hash
    assert request.request_id == "t00000001-org-0001"


def test_action_schema_carries_this_runs_limits():
    world = create_world(WorldConfig(), LifeConfig(extract_max=7, modify_max=1))
    schema = action_schema(world)
    extract = next(v for v in schema["oneOf"] if v["properties"]["action"]["const"] == "extract")
    assert extract["properties"]["amount"]["maximum"] == 7
    assert extract["additionalProperties"] is False


# -- cadence ------------------------------------------------------------------------------


def lifes(**overrides):
    return LifeConfig(seeds=(SeedOrganism(8, 8, "wait"),), **overrides)


def test_decisions_are_due_first_then_every_interval():
    world = create_world(WorldConfig(inflow_amount=0), lifes(trigger_threshold=0))
    due_ticks = []
    for _ in range(13):
        prepared = prepare_tick(world)
        if prepared.slots:
            due_ticks.append(prepared.world.tick)
        world = advance(world, lambda p, o: Proposal("wait")).world
    assert due_ticks == [1, 5, 9, 13]


def test_zero_requests_on_non_due_ticks():
    world = create_world(WorldConfig(inflow_amount=0), lifes(trigger_threshold=0))
    calls = []
    for _ in range(12):
        world = advance(world, lambda p, o: calls.append(o.age) or Proposal("wait")).world
    assert calls == [1, 5, 9]


def test_local_change_trigger_makes_a_decision_due_early():
    world = create_world(WorldConfig(), lifes(trigger_threshold=10))
    due = {}
    for _ in range(12):
        prepared = prepare_tick(world)
        for slot in prepared.slots:
            due[prepared.world.tick] = slot.due_reason
        world = advance(world, lambda p, o: Proposal("wait")).world
    assert due[1] == "first" and due[5] == "periodic" and due[9] == "periodic"
    assert due[10] == "local_change"  # energy inflow changed the local view


def test_resolution_order_is_stable_and_rotates_by_tick():
    ids = ["org-0003", "org-0001", "org-0002"]
    assert cadence.resolution_order(ids, 0) == ["org-0001", "org-0002", "org-0003"]
    assert cadence.resolution_order(ids, 1) == ["org-0002", "org-0003", "org-0001"]
    assert cadence.resolution_order(ids, 5) == ["org-0003", "org-0001", "org-0002"]


def test_cognition_fee_is_charged_once_per_decision_and_can_be_fatal():
    world = create_world(WorldConfig(inflow_amount=0), lifes(start_energy=2, upkeep=1))
    prepared = prepare_tick(world)
    o = prepared.world.organisms[0]
    assert not o.alive and o.death_cause == "starvation"  # upkeep 1 + fee 1 = 2
    assert prepared.slots == ()  # no request for an organism the fee killed
    fee_events = [e for e in prepared.events if e.type == "decision_round"]
    assert [(e.category, e.amount) for e in fee_events[0].ledger] == [("cognition_fee", 1)]


def test_observations_for_one_round_come_from_the_same_state():
    life = LifeConfig(seeds=(SeedOrganism(8, 8, "wait"), SeedOrganism(9, 8, "wait")))
    world = create_world(WorldConfig(), life)
    prepared = prepare_tick(world)
    a, b = prepared.slots
    view_a = {(c.dx, c.dy): c for c in a.observation.cells}
    assert view_a[(1, 0)].occupied or view_a[(-1, 0)].occupied
    assert all(
        s.observation.energy == prepared.world.organism(s.organism_id).energy for s in (a, b)
    )


# -- recorded provider -------------------------------------------------------------------------


def test_recordings_are_current_and_reproducible():
    assert record.main(["--check"]) == 0


def test_recorded_provider_answers_by_input_hash_never_by_order():
    data = record.generate("recorded_valid")
    shuffled = {**data, "entries": list(reversed(data["entries"]))}
    provider = RecordedProvider("recorded_valid", recording=shuffled)
    world = fixture_world("recorded_valid")
    prepared = prepare_tick(world)
    request = build_request("r", prepared.world, prepared.slots[0], None)
    response = provider.decide(request, threading.Event())
    assert response.raw_text == data["entries"][0]["raw"]
    assert response.usage.input_tokens is None  # unknown, never zero
    tampered = replace(request, input_hash="0" * 64)
    with pytest.raises(ProviderError) as err:
        provider.decide(tampered, threading.Event())
    assert err.value.kind == "mismatch" and not err.value.retryable
    assert "different input" in str(err.value)


def test_mismatch_fixture_contains_exactly_one_unmatched_entry():
    data = record.generate("recorded_mismatch")
    valid = record.generate("recorded_valid")
    diffs = [
        i
        for i, (a, b) in enumerate(zip(data["entries"], valid["entries"], strict=True))
        if a["input_hash"] != b["input_hash"]
    ]
    assert diffs == [1]


def test_available_actions_follow_the_schema():
    assert available_actions(fixture_world("recorded_valid")) == ACTIONS


# -- code fences (2026-11-10 parser decision) -------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"action": "wait"}\n```',
        '```\n{"action": "wait"}\n```',
        '  ```JSON\n{"action": "wait"}\n```  \n',
        '```json\n{"action": "wait"}```',
    ],
)
def test_exactly_one_code_fence_is_unwrapped_and_noted(raw):
    from genesis.cognition.policy import FENCE_NOTE

    result = parse(raw, LIFE, ("wait", "move", "extract", "modify"))
    assert result.status == "valid" and result.note == FENCE_NOTE
    assert result.detail == FENCE_NOTE


@pytest.mark.parametrize(
    "raw",
    [
        'Here you go:\n```json\n{"action": "wait"}\n```',  # prose outside the fence
        '```json\n{"action": "wait"}\n```\n```json\n{"action": "wait"}\n```',  # two
        '```python\n{"action": "wait"}\n```',  # another language tag
        '```json {"action": "wait"} ```',  # no line break: not a fence block
        '```json\n{"action": "wait"}\n',  # unclosed
    ],
)
def test_anything_else_with_fences_stays_malformed(raw):
    result = parse(raw, LIFE, ("wait", "move", "extract", "modify"))
    assert result.status == "malformed_json"


def test_plain_json_has_no_note():
    result = parse('{"action": "wait"}', LIFE, ("wait",))
    assert result.status == "valid" and result.note is None and result.detail is None
