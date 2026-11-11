"""Bounded recall (recall-v1): which of an organism's own records enter one request.

Deterministic and inspectable. Each candidate gets three components in [0, 1]:

- recency: elapsed ticks, min-max normalized over the candidates (all equal -> 1);
- relevance: keyword overlap (Jaccard) between the record and the current situation;
- salience: the record category's fixed salience / the largest salience.

The score is their unweighted sum. Ranking is by score, then fewer ticks ago, then id.
At most `limit` records are taken greedily in rank order while their text fits `max_bytes`.
Recall reads only the organism's own memory views and its current observation; it costs
nothing and changes no state.
"""

from dataclasses import dataclass

from genesis.engine.memory import MAX_SALIENCE, MemoryView
from genesis.engine.perception import Observation

RECALL_VERSION = "recall-v1"


def query_keywords(observation: Observation, previous_result: dict | None) -> tuple[str, ...]:
    """The current situation in the vocabulary records are indexed with: what is on the
    organism's cell now, whether others are in view, and its last accepted action."""
    here = next(c for c in observation.cells if (c.dx, c.dy) == (0, 0))
    words = ["energy"] * bool(here.energy)
    words += ["material"] * bool(here.loose_material)
    words += ["packed"] * bool(here.packed_material)
    words += ["organism"] * bool(observation.organisms)
    if previous_result and previous_result.get("outcome") == "accepted":
        action = previous_result.get("action")
        if isinstance(action, str):
            words.append(action)
    return tuple(sorted(set(words)))


@dataclass(frozen=True, slots=True)
class Scored:
    view: MemoryView
    recency: float
    relevance: float
    salience: float

    @property
    def score(self) -> float:
        return round(self.recency + self.relevance + self.salience, 6)

    def to_dict(self, selected: bool) -> dict:
        return {
            "id": self.view.id,
            "score": self.score,
            "recency": self.recency,
            "relevance": self.relevance,
            "salience": self.salience,
            "selected": selected,
        }


@dataclass(frozen=True, slots=True)
class Recall:
    query: tuple[str, ...]
    selected: tuple[MemoryView, ...]
    ranked: tuple[Scored, ...]

    def to_dict(self) -> dict:
        chosen = {v.id for v in self.selected}
        return {
            "version": RECALL_VERSION,
            "query": list(self.query),
            "candidates": len(self.ranked),
            "selected_ids": [v.id for v in self.selected],
            "scores": [s.to_dict(s.view.id in chosen) for s in self.ranked],
        }


def _relevance(query: tuple[str, ...], keywords: tuple[str, ...]) -> float:
    # The category tag ("observation"/"outcome") is salience's business, not relevance's.
    tags = set(keywords) - {"observation", "outcome"}
    union = set(query) | tags
    if not union:
        return 0.0
    return round(len(set(query) & tags) / len(union), 6)


def recall(
    memories: tuple[MemoryView, ...], query: tuple[str, ...], limit: int, max_bytes: int
) -> Recall:
    if not memories:
        return Recall(query, (), ())
    ages = [m.ticks_ago for m in memories]
    low, high = min(ages), max(ages)
    scored = [
        Scored(
            m,
            1.0 if high == low else round(1 - (m.ticks_ago - low) / (high - low), 6),
            _relevance(query, m.keywords),
            round(m.salience / MAX_SALIENCE, 6),
        )
        for m in memories
    ]
    scored.sort(key=lambda s: (-s.score, s.view.ticks_ago, s.view.id))
    selected, used = [], 0
    for item in scored:
        if len(selected) >= limit:
            break
        if used + item.view.size <= max_bytes:
            selected.append(item.view)
            used += item.view.size
    return Recall(query, tuple(selected), tuple(scored))
