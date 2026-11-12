"""Action proposals. A proposal is only a request; the engine alone validates and commits."""

from dataclasses import dataclass
from typing import Any

# Actions available under each state schema; rules never change within a run.
ACTIONS_BY_SCHEMA = {
    4: ("wait", "move"),
    5: ("wait", "move", "extract", "modify"),
    6: ("wait", "move", "extract", "modify"),
    7: ("wait", "move", "extract", "modify"),
    8: ("wait", "move", "extract", "modify", "transfer", "communicate"),
    11: ("wait", "move", "extract", "modify", "transfer", "communicate", "reproduce"),
}
ACTIONS = ACTIONS_BY_SCHEMA[11]
RESOURCES = ("energy", "material")
MODIFY_MODES = ("pack", "unpack")

# Rejection reasons (diagnostics; a rejected proposal has no state effects of its own).
UNKNOWN_ACTION = "unknown_action"
INVALID_ARGUMENTS = "invalid_arguments"
OUT_OF_BOUNDS = "out_of_bounds"
OCCUPIED = "occupied"
INSUFFICIENT_ENERGY = "insufficient_energy"
OVER_LIMIT = "over_action_limit"  # amount above the per-action maximum
INSUFFICIENT_SOURCE = "insufficient_source"  # the cell / inventory lacks the amount
OVER_CAPACITY = "over_capacity"  # the destination (organism or cell) cannot hold it
ACTOR_NOT_ALIVE = "actor_not_alive"
NOT_ADJACENT = "not_adjacent"  # the target is not a living organism next to the actor
INBOX_FULL = "inbox_full"
MESSAGE_TOO_LONG = "message_too_long"
COOLDOWN = "cooldown"  # reproduction: too soon after its last birth
ENDOWMENT_TOO_SMALL = "endowment_too_small"
INVALID_MEMORY = "invalid_memory"  # not one of the parent's own active records


@dataclass(frozen=True, slots=True)
class Unparsed:
    """Model output that never became a proposal (recorded; consumes the opportunity)."""

    reason: str  # empty_output | malformed_json | not_an_object | schema_invalid
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ReflectionProposal:
    """A parsed reflection response (M11): up to `reflection_max` interpretations, each
    citing ids of records the organism was shown. Only the engine stores anything."""

    reflections: tuple[tuple[str, tuple[str, ...]], ...]  # (text, evidence ids)

    def to_dict(self) -> dict:
        return {"reflections": [{"text": t, "evidence": list(e)} for t, e in self.reflections]}


INVALID_EVIDENCE = "invalid_evidence"  # cites a record the organism was not shown


@dataclass(frozen=True, slots=True)
class Proposal:
    action: Any
    direction: Any = None
    resource: Any = None  # extract: energy | material
    mode: Any = None  # modify: pack | unpack
    amount: Any = None  # extract / modify / transfer: positive integer
    target: Any = None  # transfer / communicate: an organism id
    text: Any = None  # communicate: the message
    endowment: Any = None  # reproduce: energy given to the child
    material: Any = None  # reproduce: optional carried material given to the child
    memory_ids: Any = None  # reproduce (M13): ids of its own records to pass on

    def to_dict(self) -> dict:
        data = {"action": self.action}
        for name in (
            "direction",
            "resource",
            "mode",
            "amount",
            "target",
            "text",
            "endowment",
            "material",
            "memory_ids",
        ):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        return data
