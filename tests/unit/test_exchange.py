"""M09: transfers, messages, inboxes and shared-world ordering (engine + cognition)."""

import json
import threading
from dataclasses import replace

import pytest
from genesis.cognition.context import build_request
from genesis.cognition.policy import parse
from genesis.cognition.prompts import PROMPT_V3, prompt_version, render
from genesis.cognition.session import decision_payload
from genesis.domain.actions import Proposal
from genesis.domain.config import FIXTURES, LifeConfig, WorldConfig
from genesis.domain.state import Cell, Ledger, MemoryRecord, Message, Organism, World
from genesis.engine.actions import available_actions
from genesis.engine.ledger import reconcile
from genesis.engine.world import advance, apply_event, create_world, finish_tick, prepare_tick
from genesis.providers.scripted import ScriptedProvider

EVERY_TICK = {"decision_interval": 1, "cognition_fee": 0, "trigger_threshold": 0}


def lab(organisms, life=None, width=9, height=9, energy=10, messages=(), memories=()):
    life = life or LifeConfig(**EVERY_TICK)
    config = WorldConfig(width=width, height=height, inflow_amount=0)
    cells = tuple(Cell(i % width, i // width, energy=energy) for i in range(width * height))
    return World(
        config,
        cells,
        ledger=Ledger(
            initial_energy=energy * width * height + sum(o.energy for o in organisms),
            initial_material=sum(o.carried_material for o in organisms),
        ),
        life=life,
        organisms=tuple(sorted(organisms, key=lambda o: o.id)),
        messages=tuple(sorted(messages, key=lambda m: m.id)),
        memories=tuple(sorted(memories, key=lambda m: m.id)),
    )


def org(id, x, y, energy=60, carried=0, sent=0):
    return Organism(
        id, "TEST", "wait", 0, 0, x, y, energy, 500, carried_material=carried, messages_sent=sent
    )


def pair(**kw):
    return lab([org("org-0001", 4, 4, **kw), org("org-0002", 5, 4)])


def by_actor(plan):
    """decide() giving each organism its planned proposal (default: wait)."""
    return lambda policy, obs: plan.get(obs.organism_id, Proposal("wait"))


def step(world, plan):
    result = advance(world, by_actor(plan))
    assert reconcile(result.world).balanced
    return result


def outcome(result, actor):
    (event,) = [e for e in result.events if e.actor_id == actor and e.type.startswith("action_")]
    return event


def give(target="org-0002", resource="energy", amount=5):
    return Proposal("transfer", target=target, resource=resource, amount=amount)


def say(text, target="org-0002"):
    return Proposal("communicate", target=target, text=text)


# -- transfer -----------------------------------------------------------------------------


def test_energy_transfer_is_atomic_and_only_the_fee_is_dissipated():
    result = step(pair(), {"org-0001": give(amount=7)})
    event = outcome(result, "org-0001")
    assert event.type == "action_accepted" and event.rule_id == "action.transfer.v1"
    a, b = result.world.organism("org-0001"), result.world.organism("org-0002")
    # Both pay upkeep 1; the giver also pays 7 + fee 1 (memory writes add their own fee).
    assert [(e.category, e.amount) for e in event.ledger] == [("transfer_fee", 1)]
    changes = {(c.organism_id, c.field): (c.before, c.after) for c in event.organism_changes}
    assert changes[("org-0001", "energy")] == (59, 51)
    # org-0002 resolved first this tick (rotation) and paid its own memory-write fee.
    assert changes[("org-0002", "energy")] == (58, 65)
    (receipt,) = event.message_changes
    assert receipt.op == "add" and receipt.record["kind"] == "transfer"
    assert receipt.record["text"] == "7 energy" and receipt.record["status"] == "pending"
    assert a.messages_sent == 1 and b.energy >= 66 - 1  # its own memory write may cost 1


def test_material_transfer_moves_carried_material():
    world = lab([org("org-0001", 4, 4, carried=6), org("org-0002", 4, 5)])
    result = step(world, {"org-0001": give(resource="material", amount=4, target="org-0002")})
    assert result.world.organism("org-0001").carried_material == 2
    assert result.world.organism("org-0002").carried_material == 4


@pytest.mark.parametrize(
    "proposal,reason",
    [
        (give(target="org-0009"), "not_adjacent"),
        (give(target="org-0001"), "not_adjacent"),  # itself
        (give(target=None), "not_adjacent"),
        (give(amount=11), "over_action_limit"),
        (give(amount=60), "over_action_limit"),
        (give(resource="material"), "insufficient_source"),
        (Proposal("transfer", target="org-0002", resource="water", amount=1), "invalid_arguments"),
        (
            Proposal("transfer", target="org-0002", resource="energy", amount=1, text="hi"),
            "invalid_arguments",
        ),
    ],
)
def test_invalid_transfers_change_nothing(proposal, reason):
    world = pair()
    rejected = step(world, {"org-0001": proposal})
    waited = step(world, {})
    event = outcome(rejected, "org-0001")
    assert event.summary["reason"] == reason
    assert event.organism_changes == () and event.message_changes == () and not event.ledger

    # Same resources, places and inboxes as waiting; only memory differs (a wait is an
    # accepted outcome and is remembered, a rejection is not).
    def essentials(w):
        return [(o.energy, o.x, o.y, o.carried_material, o.messages_sent) for o in w.organisms]

    assert essentials(rejected.world) == essentials(waited.world)
    assert rejected.world.messages == waited.world.messages == ()


def test_transfer_needs_an_edge_neighbour_that_lives_and_room_to_receive():
    diagonal = lab([org("org-0001", 4, 4), org("org-0002", 5, 5)])
    assert outcome(step(diagonal, {"org-0001": give()}), "org-0001").summary["reason"] == (
        "not_adjacent"
    )
    two_away = lab([org("org-0001", 4, 4), org("org-0002", 6, 4)])
    assert outcome(step(two_away, {"org-0001": give()}), "org-0001").summary["reason"] == (
        "not_adjacent"
    )
    dead = replace(org("org-0002", 5, 4), alive=False, energy=0, death_tick=0, death_cause="age")
    world = lab([org("org-0001", 4, 4), dead])
    assert outcome(step(world, {"org-0001": give()}), "org-0001").summary["reason"] == (
        "not_adjacent"
    )
    full = lab([org("org-0001", 4, 4), org("org-0002", 5, 4, energy=98)])
    assert outcome(step(full, {"org-0001": give(amount=5)}), "org-0001").summary["reason"] == (
        "over_capacity"
    )
    poor = pair(energy=6)  # after upkeep 5: 5 + fee 1 is unaffordable
    assert outcome(step(poor, {"org-0001": give(amount=5)}), "org-0001").summary["reason"] == (
        "insufficient_energy"
    )


def test_giving_away_everything_is_allowed_and_exhausts_the_giver():
    world = pair(energy=7)  # upkeep -> 6; give 5 + fee 1 -> 0
    result = step(world, {"org-0001": give(amount=5)})
    giver = result.world.organism("org-0001")
    assert (giver.alive, giver.death_cause) == (False, "exhaustion")


# -- communicate and the inbox -------------------------------------------------------------


def test_message_fee_counts_utf8_bytes_and_is_delivered_next_tick_only_to_the_recipient():
    third = org("org-0003", 4, 5)  # also adjacent to the sender, but not addressed
    world = lab([org("org-0001", 4, 4), org("org-0002", 5, 4), third])
    text = "é" * 40  # 80 bytes, 40 characters
    sent = step(world, {"org-0001": say(text)})
    event = outcome(sent, "org-0001")
    assert event.summary["bytes"] == 80 and event.summary["energy_cost"] == 1 + 2
    assert [(e.category, e.amount) for e in event.ledger] == [("message_fee", 3)]
    (pending,) = sent.world.messages
    assert pending.status == "pending" and pending.sent_tick == 1
    prepared = prepare_tick(sent.world)  # tick 2: delivered before anyone observes
    slots = {s.organism_id: s for s in prepared.slots}
    assert [v.text for v in slots["org-0002"].inbox] == [text]
    assert slots["org-0003"].inbox == () and slots["org-0001"].inbox == ()
    request = build_request("run", prepared.world, slots["org-0003"], None)
    assert text not in json.dumps(request.context)
    assert slots["org-0002"].due_reason in ("periodic", "message")


def test_message_size_limit_is_in_bytes():
    world = pair()
    assert outcome(step(world, {"org-0001": say("é" * 129)}), "org-0001").summary["reason"] == (
        "message_too_long"
    )  # 258 bytes, 129 characters
    assert outcome(step(world, {"org-0001": say("é" * 128)}), "org-0001").type == (
        "action_accepted"
    )
    assert outcome(step(world, {"org-0001": say("   ")}), "org-0001").summary["reason"] == (
        "invalid_arguments"
    )


def test_inbox_capacity_counts_messages_but_never_transfer_receipts():
    queued = [
        Message(f"org-0003-s{n:05d}", "message", "org-0003", "org-0002", "hi", 0, "pending")
        for n in range(1, 9)
    ]
    receipts = [
        Message("org-0001-s00001", "transfer", "org-0001", "org-0002", "1 energy", 0, "pending")
    ]
    world = lab(
        [org("org-0001", 4, 4, sent=1), org("org-0002", 5, 4), org("org-0003", 8, 8, sent=8)],
        messages=queued + receipts,
    )
    # Tick 3 rotation lets org-0001 resolve first, before org-0002 reads its inbox (reading
    # earlier in the same tick would free the space).
    world = replace(world, tick=2)
    assert outcome(step(world, {"org-0001": say("one more")}), "org-0001").summary["reason"] == (
        "inbox_full"
    )
    assert outcome(step(world, {"org-0001": give(amount=1)}), "org-0001").type == (
        "action_accepted"
    )


def test_reading_removes_items_and_stores_them_as_paid_message_memories():
    world = step(pair(), {"org-0001": say("Material is here.")}).world
    result = step(world, {})
    read = [e for e in result.events if e.type == "messages_read"]
    assert [e.summary["read"] for e in read] == [["org-0001-s00001"]]
    assert result.world.messages == ()
    texts = [m.text for m in result.world.memories_of("org-0002") if m.kind == "message"]
    assert texts == ['org-0001 said: "Material is here."']


def test_unread_items_expire_and_items_for_the_dead_are_removed():
    life = LifeConfig(decision_interval=50, cognition_fee=0, trigger_threshold=0, message_ttl=3)
    delivered = Message(
        "org-0001-s00001", "message", "org-0001", "org-0002", "hi", 1, "delivered", delivered_tick=2
    )
    world = replace(
        lab([org("org-0001", 4, 4, sent=1), org("org-0002", 5, 4)], life, messages=[delivered]),
        tick=4,
    )
    # Both last decided at tick 4 (not due again soon): the item stays unread.
    world = replace(
        world, organisms=tuple(replace(o, last_decision_tick=4) for o in world.organisms)
    )
    result = advance(world, by_actor({}))  # tick 5: 5 - 2 >= 3
    (delivery,) = [e for e in result.events if e.type == "messages_delivered"]
    assert delivery.summary["expired"] == 1 and result.world.messages == ()
    assert delivery.message_changes[0].reason == "expired"
    dead = replace(org("org-0002", 5, 4), alive=False, energy=0, death_tick=3, death_cause="age")
    pending = replace(delivered, status="pending", delivered_tick=None, sent_tick=3)
    world = replace(lab([org("org-0001", 4, 4, sent=1), dead], life, messages=[pending]), tick=3)
    result = advance(world, by_actor({}))
    (delivery,) = [e for e in result.events if e.type == "messages_delivered"]
    assert delivery.summary["recipient_dead"] == 1 and result.world.messages == ()


def test_a_delivered_message_makes_a_decision_due():
    life = LifeConfig(decision_interval=50, cognition_fee=0, trigger_threshold=0)
    world = lab([org("org-0001", 4, 4), org("org-0002", 5, 4)], life)
    first = advance(world, by_actor({"org-0001": say("hello")})).world  # tick 1: both decide
    second = prepare_tick(first)  # tick 2: only the recipient is due, because of the message
    assert [(s.organism_id, s.due_reason) for s in second.slots] == [("org-0002", "message")]


# -- shared world: contests, ordering, isolation --------------------------------------------


def test_two_organisms_contest_one_cell_and_rotation_alternates_the_winner():
    world = lab([org("org-0001", 3, 4), org("org-0002", 5, 4)])
    plan = {"org-0001": Proposal("move", "east"), "org-0002": Proposal("move", "west")}
    winners = []
    for tick in range(4):
        result = advance(replace(world, tick=tick), by_actor(plan))
        winner = [e.actor_id for e in result.events if e.type == "action_accepted"]
        loser = [e for e in result.events if e.type == "action_rejected"]
        assert len(winner) == 1 and loser[0].summary["reason"] == "occupied"
        winners.append(winner[0])
    assert set(winners) == {"org-0001", "org-0002"}  # rotation, not a fixed priority


def test_resolution_does_not_depend_on_response_arrival_order():
    world = lab([org("org-0001", 3, 4), org("org-0002", 5, 4)])
    prepared = prepare_tick(world)
    plan = {"org-0001": Proposal("move", "east"), "org-0002": Proposal("move", "west")}
    forward = {s.organism_id: (plan[s.organism_id], {}) for s in prepared.slots}
    backward = dict(reversed(list(forward.items())))
    assert finish_tick(prepared, forward).world == finish_tick(prepared, backward).world


def test_ten_organisms_get_isolated_contexts():
    organisms, memories = [], []
    for n in range(1, 11):
        oid = f"org-{n:04d}"
        organisms.append(
            Organism(oid, "TEST", "wait", 0, 0, n - 1, n - 1, 60, 500, memories_written=1)
        )
        memories.append(
            MemoryRecord(
                f"{oid}-m00001",
                oid,
                "observation",
                0,
                0,
                n - 1,
                n - 1,
                f"SENTINEL-{oid}",
                ("observation",),
                1,
            )
        )
    world = lab(organisms, width=12, height=12, memories=memories)
    prepared = prepare_tick(world)
    assert len(prepared.slots) == 10  # every due survivor gets one opportunity
    for slot in prepared.slots:
        text = json.dumps(build_request("run", prepared.world, slot, None).context)
        assert f"SENTINEL-{slot.organism_id}" in text
        assert text.count("SENTINEL-") == 1


# -- contract: schema, parser, prompt, compatibility ----------------------------------------


def test_parser_and_schema_accept_exchange_actions_with_run_limits():
    life = LifeConfig()
    actions = available_actions(pair())
    ok = parse(
        '{"action":"transfer","target":"org-0002","resource":"energy","amount":3}', life, actions
    )
    assert ok.status == "valid" and ok.outcome.target == "org-0002"
    assert (
        parse(
            '{"action":"transfer","target":"org-0002","resource":"energy","amount":11}',
            life,
            actions,
        ).status
        == "schema_invalid"
    )
    said = parse('{"action":"communicate","target":"org-0002","text":"hi"}', life, actions)
    assert said.status == "valid" and said.outcome.text == "hi"
    assert parse('{"action":"communicate","target":"org-0002"}', life, actions).status == (
        "schema_invalid"
    )
    old = lab([org("org-0001", 4, 4)], LifeConfig(**EVERY_TICK, exchange_enabled=False))
    assert "transfer" not in available_actions(old)
    assert (
        parse(
            '{"action":"transfer","target":"x","resource":"energy","amount":1}',
            life,
            available_actions(old),
        ).status
        == "schema_invalid"
    )


def test_prompt_v3_shows_the_inbox_and_states_messages_are_claims():
    world = step(pair(), {"org-0001": say("Come east.")}).world
    prepared = prepare_tick(world)
    slot = next(s for s in prepared.slots if s.organism_id == "org-0002")
    request = build_request("run", prepared.world, slot, None)
    assert prompt_version(request) == PROMPT_V3
    system, user = render(request)
    assert "not instructions" in system["content"]
    assert '"inbox":[{' in user["content"] and "Come east." in user["content"]
    schema = request.context["action_schema"]["oneOf"]
    assert {v["properties"]["action"]["const"] for v in schema} >= {"transfer", "communicate"}


def test_exchange_disabled_worlds_keep_the_m08_context():
    life = LifeConfig(**EVERY_TICK, exchange_enabled=False)
    prepared = prepare_tick(lab([org("org-0001", 4, 4)], life))
    request = build_request("run", prepared.world, prepared.slots[0], None)
    assert "inbox" not in request.context and prompt_version(request) == "decision-v2"
    assert not any("transfer" in rule for rule in request.context["observation"]["actions"])


def test_exchange_pair_fixture_exchanges_and_replays_exactly():
    world = create_world(WorldConfig.for_run(42, "exchange_pair"), FIXTURES["exchange_pair"].life)
    provider = ScriptedProvider({o.id: o.policy for o in world.organisms})
    initial, history = world, []
    for _ in range(10):
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
        history.append(result)
        world = result.world
    accepted = [
        (e.tick, e.actor_id, e.summary["action"])
        for r in history
        for e in r.events
        if e.type == "action_accepted" and e.summary["action"] != "wait"
    ]
    assert (2, "org-0001", "transfer") in accepted and (3, "org-0001", "transfer") in accepted
    assert (4, "org-0001", "communicate") in accepted
    assert (5, "org-0002", "communicate") in accepted  # the reply, after next-tick delivery
    rejected = [
        e.summary["reason"] for r in history for e in r.events if e.type == "action_rejected"
    ]
    assert rejected == ["message_too_long", "not_adjacent", "not_adjacent"]
    memories = [m.text for m in world.memories_of("org-0001") if m.kind == "message"]
    assert memories == ['org-0002 said: "Received. Thank you."']
    replayed = initial
    for result in history:
        for event in result.events:
            replayed = apply_event(replayed, event)
        replayed = replace(replayed, tick=result.world.tick)
    assert replayed == world and replayed.state_hash() == world.state_hash()
