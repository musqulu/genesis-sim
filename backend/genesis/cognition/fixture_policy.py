"""Deterministic scripted test policies (development fixtures only).

They receive nothing but an Observation DTO and return a Proposal. Their behavior is
scripted by the author: it is diagnostic of the mechanics, never evidence of autonomy.
"""

import re
from collections.abc import Callable

from genesis.domain.actions import Proposal
from genesis.engine.perception import Observation

SCRIPTS: dict[str, tuple[Proposal, ...]] = {
    # From (0,0): two off-grid attempts, then a short tour that also returns to the edge.
    "corner_tour": (
        Proposal("move", "west"),
        Proposal("move", "north"),
        Proposal("move", "east"),
        Proposal("move", "east"),
        Proposal("move", "south"),
        Proposal("wait"),
        Proposal("move", "south"),
        Proposal("move", "west"),
        Proposal("move", "west"),
        Proposal("move", "west"),
        Proposal("move", "north"),
        Proposal("move", "north"),
    ),
    "square_walk": (
        Proposal("move", "north"),
        Proposal("move", "east"),
        Proposal("move", "south"),
        Proposal("move", "west"),
    ),
    "wait": (Proposal("wait"),),
    # material_workshop: starts at a 25-unit stockpile with 60 energy. Each step's intended
    # engine outcome is noted; the script is scripted, never adaptive.
    "workshop": (
        Proposal("extract", resource="material", amount=11),  # rejected: over_action_limit
        Proposal("extract", resource="material", amount=10),  # carried 10
        Proposal("extract", resource="material", amount=10),  # carried 20 (capacity)
        Proposal("extract", resource="material", amount=1),  # rejected: over_capacity
        Proposal("modify", mode="pack", amount=2),  # packed 2
        Proposal("modify", mode="pack", amount=3),  # rejected: over_action_limit
        Proposal("modify", mode="pack", amount=2),  # packed 4 -> entry cost 3
        Proposal("move", direction="east"),  # leaves the packed cell (entry cost 1)
        Proposal("move", direction="west"),  # re-enters: costs 1 + 4 // 2 = 3
        Proposal("modify", mode="unpack", amount=1),  # packed 3, carried 17
        Proposal("modify", mode="pack", amount=2),  # packed 5
        Proposal("modify", mode="pack", amount=2),  # packed 7
        Proposal("modify", mode="pack", amount=2),  # rejected: over_capacity (cap 8)
        Proposal("modify", mode="pack", amount=1),  # packed 8 -> entry cost 5
        Proposal("extract", resource="energy", amount=5),  # +5 energy from the cell, fee 1
        Proposal("move", direction="east"),
        Proposal("move", direction="west"),  # re-entry now costs 5
        Proposal("move", direction="east"),
        Proposal("move", direction="west"),
        Proposal("move", direction="east"),
        Proposal("move", direction="west"),  # rejected once energy < 5: insufficient_energy
        Proposal("wait"),
    ),
}


# Scripts that run once and then repeat their last step instead of cycling.
ONCE = {"workshop"}


def script_step(name: str, age: int) -> Proposal:
    script = SCRIPTS[name]
    step = age - 1
    if name in ONCE:
        return script[min(step, len(script) - 1)]
    return script[step % len(script)]


def scripted(name: str) -> Callable[[Observation], Proposal]:
    def decide(observation: Observation) -> Proposal:
        if not isinstance(observation, Observation):
            raise TypeError("test policies accept only Observation DTOs")
        return script_step(name, observation.age)

    return decide


POLICIES: dict[str, Callable[[Observation], Proposal]] = {name: scripted(name) for name in SCRIPTS}


def decide(policy: str, observation: Observation) -> Proposal:
    return POLICIES[policy](observation)


# -- context policies (M08): scripted, but reading the request context the organism gets ---

_LOOSE = re.compile(r"loose material (\d+)")


def recall_return(context: dict) -> Proposal:
    """memory_return fixture. Age 1: wait on the stockpile (so it is observed and stored).
    Ages 2-4: walk east, out of view. Then, using only recalled records: walk back to the
    remembered cell with the most loose material (oldest first on ties), x before y, and
    extract once there. With no such recalled record it waits. Scripted, never adaptive."""
    observation = context["observation"]
    age = observation["age"]
    if age == 1:
        return Proposal("wait")
    if age <= 4:
        return Proposal("move", direction="east")
    here = next(c for c in observation["cells"] if (c["dx"], c["dy"]) == (0, 0))
    if observation["carried_material"]:
        return Proposal("wait")
    targets = []
    for record in context.get("recalled") or []:
        found = _LOOSE.search(record["text"]) if record["kind"] == "observation" else None
        if found and int(found.group(1)) > 0:
            targets.append((-int(found.group(1)), -record["ticks_ago"], record))
    if not targets:
        return Proposal("wait")
    target = min(targets, key=lambda t: t[:2])[2]
    dx, dy = target["dx"], target["dy"]
    if (dx, dy) == (0, 0):
        if here["loose_material"]:
            return Proposal("extract", resource="material", amount=min(5, here["loose_material"]))
        return Proposal("wait")
    if dx:
        return Proposal("move", direction="east" if dx > 0 else "west")
    return Proposal("move", direction="south" if dy > 0 else "north")


GIVER = {
    1: Proposal("extract", resource="material", amount=5),
    2: Proposal("transfer", target="org-0002", resource="energy", amount=5),
    3: Proposal("transfer", target="org-0002", resource="material", amount=3),
    4: Proposal("communicate", target="org-0002", text="There is loose material on my cell."),
    5: Proposal("communicate", target="org-0002", text="x" * 300),  # message_too_long
    6: Proposal("move", direction="west"),  # now two cells apart
    7: Proposal("transfer", target="org-0002", resource="energy", amount=1),  # not_adjacent
    8: Proposal("communicate", target="org-0002", text="Still there?"),  # not_adjacent
}


def exchange_giver(context: dict) -> Proposal:
    """exchange_pair fixture, org-0001: a fixed script of gifts, a message, oversized and
    out-of-range attempts, then waiting. Scripted, never adaptive."""
    return GIVER.get(context["observation"]["age"], Proposal("wait"))


def exchange_replier(context: dict) -> Proposal:
    """exchange_pair fixture, org-0002: answers the first delivered message from a visible
    neighbour, using only its inbox and observation; otherwise waits. Scripted."""
    adjacent = {
        o["id"] for o in context["observation"]["organisms"] if abs(o["dx"]) + abs(o["dy"]) == 1
    }
    for item in context.get("inbox") or []:
        if item["kind"] == "message" and item["from"] in adjacent:
            return Proposal("communicate", target=item["from"], text="Received. Thank you.")
    return Proposal("wait")


def tendency_probe(context: dict) -> Proposal:
    """tendency_social / tendency_solitary fixtures, org-0001: reads only its own stated
    tendencies. Affiliation >= 50: message an adjacent neighbour; otherwise extract energy.
    A scripted proof of the information path, not a model of how tendencies act."""
    tendencies = context.get("tendencies") or {}
    neighbours = [
        o["id"] for o in context["observation"]["organisms"] if abs(o["dx"]) + abs(o["dy"]) == 1
    ]
    if tendencies.get("affiliation", 0) >= 50 and neighbours:
        return Proposal("communicate", target=neighbours[0], text="Hello, neighbour.")
    return Proposal("extract", resource="energy", amount=5)


def walk_square(context: dict) -> Proposal:
    """reflection fixtures: the square_walk script, decided from the request context."""
    return script_step("square_walk", context["observation"]["age"])


def reflect_cite_two(context: dict) -> dict:
    """reflection_probe: one interpretation citing the two newest records it was shown."""
    ids = [m["id"] for m in context["memories"]][:2]
    return {"reflections": [{"text": "I keep walking the same small square.", "evidence": ids}]}


def reflect_cite_unknown(context: dict) -> dict:
    """reflection_invalid: cites a record it was never shown (must be rejected)."""
    return {
        "reflections": [{"text": "Something I never remembered.", "evidence": ["org-0001-m99999"]}]
    }


REPRODUCER = {
    1: Proposal("reproduce", direction="east", endowment=40),  # accepted: a child east
    2: Proposal("reproduce", direction="south", endowment=40),  # rejected: cooldown
    3: Proposal("reproduce", direction="north", endowment=5),  # rejected: endowment_too_small
    21: Proposal("reproduce", direction="east", endowment=40),  # rejected: occupied (child)
    22: Proposal("reproduce", direction="north", endowment=40),  # rejected: insufficient_energy
}


def reproducer(context: dict) -> Proposal:
    """birth_pair fixture: a fixed script by age; its child runs the same script and is
    too poor to reproduce (40 energy < fee 10 + 40 + keep 10). Scripted, not adaptive."""
    return REPRODUCER.get(context["observation"]["age"], Proposal("wait"))


# Scripted reflection replies (M11), by fixture policy name; others reply with none.
REFLECT_POLICIES: dict[str, Callable[[dict], dict]] = {
    "walk_reflect": reflect_cite_two,
    "walk_reflect_invalid": reflect_cite_unknown,
}

CONTEXT_POLICIES: dict[str, Callable[[dict], Proposal]] = {
    "reproducer": reproducer,
    "walk_reflect": walk_square,
    "walk_reflect_invalid": walk_square,
    "tendency_probe": tendency_probe,
    "recall_return": recall_return,
    "exchange_giver": exchange_giver,
    "exchange_replier": exchange_replier,
}
