"""Strict parsing of raw model output into a proposal. No fallback action is ever invented.

Order: empty -> size -> JSON syntax (duplicate keys are malformed) -> object -> strict
schema (exact types, no unknown fields) -> limits from this run's rules. Anything that
fails becomes an Unparsed outcome that is recorded and consumes the decision opportunity.

Since 2026-11-10 (user decision, see DECISIONS.md P16): a reply that is *exactly one*
Markdown code fence (```json ... ``` or ``` ... ```) around the JSON is read from inside
the fence. Nothing else is repaired. The raw output is still stored verbatim and the
decision carries FENCE_NOTE so the observer can see it was unwrapped.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from genesis.cognition.context import EXPLANATION_MAX
from genesis.domain.actions import Proposal, Unparsed
from genesis.domain.config import LifeConfig

MAX_OUTPUT_CHARS = 4096
FENCE_NOTE = "accepted after removing one ```json code fence"
_FENCE = re.compile(r"\A\s*```(?:json|JSON)?[ \t]*\n(?P<body>.*?)\n?[ \t]*```\s*\Z", re.DOTALL)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    explanation: str | None = Field(default=None, max_length=EXPLANATION_MAX)


class _Wait(_Strict):
    action: Literal["wait"]


class _Move(_Strict):
    action: Literal["move"]
    direction: Literal["north", "east", "south", "west"]


class _Extract(_Strict):
    action: Literal["extract"]
    resource: Literal["energy", "material"]
    amount: int


class _Modify(_Strict):
    action: Literal["modify"]
    mode: Literal["pack", "unpack"]
    amount: int


class _Transfer(_Strict):
    action: Literal["transfer"]
    target: str = Field(max_length=64)
    resource: Literal["energy", "material"]
    amount: int


class _Communicate(_Strict):
    action: Literal["communicate"]
    target: str = Field(max_length=64)
    text: str = Field(min_length=1, max_length=4096)  # bytes are checked by the engine


class _Reproduce(_Strict):
    action: Literal["reproduce"]
    direction: Literal["north", "east", "south", "west"]
    endowment: int
    material: int | None = None


_MODELS = {
    "reproduce": _Reproduce,
    "wait": _Wait,
    "move": _Move,
    "extract": _Extract,
    "modify": _Modify,
    "transfer": _Transfer,
    "communicate": _Communicate,
}


@dataclass(frozen=True, slots=True)
class ParseResult:
    outcome: Proposal | Unparsed  # or a ReflectionProposal (M11)
    explanation: str | None = None
    note: str | None = None  # how a valid reply was read, e.g. FENCE_NOTE

    @property
    def status(self) -> str:
        return self.outcome.reason if isinstance(self.outcome, Unparsed) else "valid"

    @property
    def detail(self) -> str | None:
        """The parse error, or for a valid reply the note on how it was read."""
        return self.note if self.status == "valid" else self.outcome.detail


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def parse(raw: str, life: LifeConfig, available: tuple[str, ...]) -> ParseResult:
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
    action = data.get("action")
    if not isinstance(action, str) or action not in available or action not in _MODELS:
        return ParseResult(Unparsed("schema_invalid", f"unknown action {str(action)[:40]!r}"))
    try:
        model = _MODELS[action].model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "object"
        return ParseResult(Unparsed("schema_invalid", f"{where}: {first['msg']}"[:200]))
    limits = {
        "extract": life.extract_max,
        "modify": life.modify_max,
        "transfer": life.transfer_max,
    }
    amount = getattr(model, "amount", None)
    if amount is not None and not 1 <= amount <= limits[action]:
        return ParseResult(
            Unparsed("schema_invalid", f"amount: must be 1-{limits[action]}, got {amount}")
        )
    proposal = Proposal(
        action=action,
        direction=getattr(model, "direction", None),
        resource=getattr(model, "resource", None),
        mode=getattr(model, "mode", None),
        amount=amount,
        target=getattr(model, "target", None),
        text=getattr(model, "text", None),
        endowment=getattr(model, "endowment", None),
        material=getattr(model, "material", None),
    )
    return ParseResult(proposal, model.explanation, FENCE_NOTE if text is not raw else None)
