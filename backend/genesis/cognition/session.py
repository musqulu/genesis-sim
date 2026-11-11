"""Glue between the decision barrier and cognition, shared by the runtime and recorders."""

from genesis.cognition.context import DecisionRequest, previous_result_from
from genesis.cognition.policy import ParseResult, parse
from genesis.cognition.reflection import parse_reflection
from genesis.domain.config import LifeConfig
from genesis.engine.world import DecisionSlot, TickResult


def parse_for(
    request: DecisionRequest, raw: str, life: LifeConfig, available: tuple[str, ...]
) -> ParseResult:
    """Parse raw output by the request's purpose: an action, or a reflection (M11)."""
    if request.purpose == "reflect":
        return parse_reflection(raw, life)
    return parse(raw, life, available)


def decision_payload(
    slot: DecisionSlot, request: DecisionRequest, raw: str, parsed: ParseResult
) -> dict:
    """Observer-only record attached to the outcome event (never affects state)."""
    proposal = parsed.outcome.to_dict() if parsed.status == "valid" else None
    return {
        "request_id": request.request_id,
        "purpose": request.purpose,
        "input_hash": request.input_hash,
        "observation": slot.observation.to_dict(),
        "observation_sha256": slot.observation.digest(),
        "origin": {"x": slot.origin[0], "y": slot.origin[1]},
        "due_reason": slot.due_reason,
        "previous_result": request.context.get("previous_result"),
        "recalled": request.context.get("recalled"),
        "recall": request.recall,
        "raw_output": raw,
        "parse_status": parsed.status,
        "parse_error": parsed.detail,
        "proposal": proposal,
        "explanation": parsed.explanation,
        "policy": slot.policy,
    }


def update_previous(previous: dict[str, dict], result: TickResult) -> None:
    """Remember each actor's last decision outcome (from recorded events only)."""
    for event in result.events:
        if event.type in ("action_accepted", "action_rejected") and event.actor_id:
            previous[event.actor_id] = previous_result_from(
                event.type, event.rule_id, event.summary, event.tick
            )
