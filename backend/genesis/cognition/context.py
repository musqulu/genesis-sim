"""Provider-independent decision context and request contract.

A request carries only plain JSON-compatible data: the organism's observation, the result
of its previous decision (derived from recorded history, never from world state), and the
action schema with this run's limits. Under state schema 7 it also carries the memory rules,
the organism's own memory status and its recalled records (recall-v1); without memory the
context is exactly the M06 one. No world, database or engine object ever crosses this
boundary. The input hash binds a response to exactly this input.
"""

import hashlib
import json
from dataclasses import dataclass

from genesis.cognition.retrieval import query_keywords, recall
from genesis.domain.config import DIRECTIONS
from genesis.domain.state import World
from genesis.engine import memory
from genesis.engine.actions import available_actions
from genesis.engine.world import DecisionSlot

ACTION_SCHEMA_VERSION = "genesis.action.v1"
EXPLANATION_MAX = 280  # characters; an explanation is a model report, not proof of reasoning
PURPOSE = "decide"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def action_schema(world: World) -> dict:
    """JSON Schema for one proposal under this run's rules (limits from the run config)."""
    life = world.life
    explanation = {"type": "string", "maxLength": EXPLANATION_MAX}

    def variant(action: str, properties: dict) -> dict:
        return {
            "type": "object",
            "properties": {"action": {"const": action}, **properties, "explanation": explanation},
            "required": ["action", *properties],
            "additionalProperties": False,
        }

    target = {"type": "string", "description": "id of a living organism in an adjacent cell"}
    variants = {
        "transfer": variant(
            "transfer",
            {
                "target": target,
                "resource": {"enum": ["energy", "material"]},
                "amount": {"type": "integer", "minimum": 1, "maximum": life.transfer_max},
            },
        ),
        "communicate": variant(
            "communicate",
            {
                "target": target,
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "description": f"at most {life.message_max_bytes} UTF-8 bytes",
                },
            },
        ),
        "reproduce": {  # M12: material is optional, so not in `required`
            "type": "object",
            "properties": {
                "action": {"const": "reproduce"},
                "direction": {"enum": list(DIRECTIONS)},
                "endowment": {
                    "type": "integer",
                    "minimum": life.reproduction_min_endowment,
                    "maximum": life.energy_capacity,
                },
                "material": {"type": "integer", "minimum": 0, "maximum": life.material_capacity},
                **(
                    {
                        "memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": life.inherit_memory_max,
                            "description": "ids of your own memories to pass on",
                        }
                    }
                    if world.schema >= 12
                    else {}
                ),
                "explanation": explanation,
            },
            "required": ["action", "direction", "endowment"],
            "additionalProperties": False,
        },
        "wait": variant("wait", {}),
        "move": variant("move", {"direction": {"enum": list(DIRECTIONS)}}),
        "extract": variant(
            "extract",
            {
                "resource": {"enum": ["energy", "material"]},
                "amount": {"type": "integer", "minimum": 1, "maximum": life.extract_max},
            },
        ),
        "modify": variant(
            "modify",
            {
                "mode": {"enum": ["pack", "unpack"]},
                "amount": {"type": "integer", "minimum": 1, "maximum": life.modify_max},
            },
        ),
    }
    return {
        "$id": ACTION_SCHEMA_VERSION,
        "oneOf": [variants[a] for a in available_actions(world)],
    }


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    request_id: str  # stable: target tick + actor
    run_id: str
    actor_id: str
    target_tick: int
    purpose: str
    schema_version: str
    context: dict  # plain JSON data only
    input_hash: str
    # Observer-only: how recall chose `context["recalled"]` (scores, query). Derived from
    # the same inputs, so it is not part of the input hash.
    recall: dict | None = None

    def to_dict(self) -> dict:
        return {
            "recall": self.recall,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "actor_id": self.actor_id,
            "target_tick": self.target_tick,
            "purpose": self.purpose,
            "schema_version": self.schema_version,
            "context": self.context,
            "input_hash": self.input_hash,
        }


def request_id(tick: int, actor_id: str) -> str:
    return f"t{tick:08d}-{actor_id}"


def build_request(
    run_id: str, world: World, slot: DecisionSlot, previous_result: dict | None
) -> DecisionRequest:
    if slot.purpose == "reflect":  # M11: a reflection opportunity replaces the decision
        from genesis.cognition.reflection import build_reflection_request

        return build_reflection_request(run_id, world, slot)
    context = {
        "observation": slot.observation.to_dict(),
        "previous_result": previous_result,
        "action_schema": action_schema(world),
        "response_format": (
            "Reply with exactly one JSON object matching action_schema. "
            "Optional 'explanation' (max 280 characters)."
        ),
    }
    recalled = None
    if slot.memories is not None:
        life = world.life
        query = query_keywords(slot.observation, previous_result)
        if slot.inbox:
            query = tuple(sorted({*query, "message", "organism"}))
        recalled = recall(
            slot.memories,
            query,
            life.recall_max,
            life.recall_max_bytes,
        )
        context["memory"] = {
            "rules": memory.rules_text(world),
            "active_records": len(slot.memories),
            "active_bytes": sum(v.size for v in slot.memories),
            "max_records": life.memory_max_records,
            "max_bytes": life.memory_max_bytes,
        }
        context["recalled"] = [v.to_dict() for v in recalled.selected]
    if slot.inbox is not None:  # schema 8: delivered items since its last decision
        context["inbox"] = [v.to_dict() for v in slot.inbox]
    if slot.tendencies is not None:  # schema 9: its own starting tendencies, 0-100
        context["tendencies"] = dict(slot.tendencies)
    # Exactly the JSON a provider receives over the wire: lists, dicts, strings, numbers.
    context = json.loads(canonical(context))
    core = {
        "purpose": PURPOSE,
        "actor_id": slot.organism_id,
        "target_tick": world.tick,
        "schema_version": ACTION_SCHEMA_VERSION,
        "context": context,
    }
    # The run id is deliberately excluded: the same input in a fresh run of the same
    # fixture is the same request, so recordings remain valid across runs.
    digest = hashlib.sha256(canonical(core).encode()).hexdigest()
    return DecisionRequest(
        request_id=request_id(world.tick, slot.organism_id),
        run_id=run_id,
        actor_id=slot.organism_id,
        target_tick=world.tick,
        purpose=PURPOSE,
        schema_version=ACTION_SCHEMA_VERSION,
        context=context,
        input_hash=digest,
        recall=None if recalled is None else recalled.to_dict(),
    )


def previous_result_from(event_type: str, rule_id: str, summary: dict, tick: int) -> dict:
    """What an organism may learn about its last decision: outcome only, no world state."""
    if event_type == "action_accepted":
        return {"tick": tick, "outcome": "accepted", "action": summary.get("action")}
    if rule_id == "cognition.parse.v1":
        return {"tick": tick, "outcome": "unparsed", "reason": summary.get("reason")}
    return {"tick": tick, "outcome": "rejected", "reason": summary.get("reason")}
