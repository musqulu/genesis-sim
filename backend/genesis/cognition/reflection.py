"""Reflection requests and strict parsing (M11, prompt reflection-v1).

A reflection request shows the organism only its own recent records (the same offsets-only
views as recall), its basic state and its tendencies, and asks for at most `reflection_max`
short interpretations, each citing the ids of supporting records. Replying with an empty
list is valid: reflection is never mandatory. Parsing checks only the shape; whether the
cited ids were actually shown is the engine's decision.
"""

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from genesis.cognition.policy import (
    _FENCE,
    FENCE_NOTE,
    MAX_OUTPUT_CHARS,
    ParseResult,
    _no_duplicates,
)
from genesis.domain.actions import ReflectionProposal, Unparsed
from genesis.domain.config import LifeConfig

REFLECTION_SCHEMA_VERSION = "genesis.reflection.v1"
REFLECTION_PROMPT_VERSION = "reflection-v1"
PURPOSE = "reflect"

SYSTEM = (
    "You are one organism living in a shared two-dimensional grid world. Your life is "
    "finite. This time you do not act: you may reflect on your own memories.\n"
    "'memories' lists some of your remembered records, each with an id, how many ticks "
    "ago it happened and the offset (dx, dy) from where you are now. Remembered values are "
    "as they were then.\n"
    "You may write a few short interpretations of your experience: patterns, lessons or "
    "intentions. Each must cite the ids of the memories that support it. Writing none is "
    "fine. Reflections are stored in your memory and cost energy.\n"
    "Reply with exactly one JSON object that matches the reflection schema, and nothing "
    "else: no prose, no code fences."
)


def reflection_schema(life: LifeConfig) -> dict:
    return {
        "$id": REFLECTION_SCHEMA_VERSION,
        "type": "object",
        "properties": {
            "reflections": {
                "type": "array",
                "maxItems": life.reflection_max,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "minLength": 1,
                            "description": f"at most {life.memory_record_max_bytes} UTF-8 bytes",
                        },
                        "evidence": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 8,
                            "description": "ids from 'memories'",
                        },
                    },
                    "required": ["text", "evidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["reflections"],
        "additionalProperties": False,
    }


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_reflection_request(run_id: str, world, slot):
    """A DecisionRequest whose purpose is "reflect" (same id scheme: one per tick)."""
    from genesis.cognition.context import DecisionRequest, request_id

    life = world.life
    observation = slot.observation
    context = {
        "status": {
            "age": observation.age,
            "energy": observation.energy,
            "energy_capacity": observation.energy_capacity,
            "carried_material": observation.carried_material,
            "life_note": observation.life_note,
        },
        "memories": [v.to_dict() for v in slot.memories or ()],
        "reflection_schema": reflection_schema(life),
        "response_format": (
            "Reply with exactly one JSON object: "
            '{"reflections": [{"text": "...", "evidence": ["<memory id>", ...]}]} '
            f"with at most {life.reflection_max} reflections, or "
            '{"reflections": []}.'
        ),
    }
    if slot.tendencies is not None:
        context["tendencies"] = dict(slot.tendencies)
    context = json.loads(canonical(context))
    core = {
        "purpose": PURPOSE,
        "actor_id": slot.organism_id,
        "target_tick": world.tick,
        "schema_version": REFLECTION_SCHEMA_VERSION,
        "context": context,
    }
    return DecisionRequest(
        request_id=request_id(world.tick, slot.organism_id),
        run_id=run_id,
        actor_id=slot.organism_id,
        target_tick=world.tick,
        purpose=PURPOSE,
        schema_version=REFLECTION_SCHEMA_VERSION,
        context=context,
        input_hash=hashlib.sha256(canonical(core).encode()).hexdigest(),
    )


def render(request) -> list[dict]:
    context = request.context
    user = {
        "status": context["status"],
        "memories": context["memories"],
        "reflection_schema": context["reflection_schema"],
        "response_format": context["response_format"],
    }
    if "tendencies" in context:
        user["tendencies"] = context["tendencies"]
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(user, separators=(",", ":"))},
    ]


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    text: str = Field(min_length=1, max_length=1024)
    evidence: list[str] = Field(min_length=1, max_length=8)


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    reflections: list[_Item] = Field(max_length=8)


def parse_reflection(raw: str, life: LifeConfig) -> ParseResult:
    if not isinstance(raw, str) or not raw.strip():
        return ParseResult(Unparsed("empty_output"))
    if len(raw) > MAX_OUTPUT_CHARS:
        return ParseResult(Unparsed("output_too_long", f"{len(raw)} characters"))
    fenced = _FENCE.match(raw)
    text = raw
    if fenced and "```" not in fenced.group("body"):
        text = fenced.group("body")
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError) as exc:
        return ParseResult(Unparsed("malformed_json", str(exc)[:200]))
    if not isinstance(data, dict):
        return ParseResult(Unparsed("not_an_object", type(data).__name__))
    try:
        reply = _Reply.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "object"
        return ParseResult(Unparsed("schema_invalid", f"{where}: {first['msg']}"[:200]))
    if len(reply.reflections) > life.reflection_max:
        return ParseResult(Unparsed("schema_invalid", f"at most {life.reflection_max} reflections"))
    proposal = ReflectionProposal(
        tuple((item.text, tuple(item.evidence)) for item in reply.reflections)
    )
    return ParseResult(proposal, None, FENCE_NOTE if text is not raw else None)
