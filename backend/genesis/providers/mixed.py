"""Mixed-model populations: each organism's decisions come from its own provider.

A deliberate, labelled experiment (constitution §3): the assignment of organisms to models
is fixed at run creation, sealed in the manifest and recorded with every decision. The
world, prompt, validator and costs are identical for every organism; only the model that
answers differs. Organisms sharing a model share one provider instance (one resident local
model), each with its own isolated request context.
"""

import threading

from genesis.cognition.context import DecisionRequest
from genesis.providers.base import Capabilities, Provider, ProviderError, ProviderIdentity


class MixedProvider(Provider):
    def __init__(self, members: list[Provider], assignment: dict[str, int]):
        self.members = members
        self.assignment = assignment
        self.identity = ProviderIdentity(
            "mixed", " + ".join(m.identity.name for m in members), model=None, revision=None
        )
        self.capabilities = Capabilities("none", None, None, 1)
        self.calls = 0

    def adopt(self, child_id: str, parent_id: str, policy: str) -> None:
        """A child born in the run (M12) keeps its parent's model for life."""
        if parent_id in self.assignment:
            self.assignment.setdefault(child_id, self.assignment[parent_id])

    def member_for(self, actor_id: str) -> Provider:
        index = self.assignment.get(actor_id)
        if index is None:
            raise ProviderError("config", f"No model is assigned to {actor_id}")
        return self.members[index]

    def identity_for(self, actor_id: str) -> ProviderIdentity:
        return self.member_for(actor_id).identity

    def decide(self, request: DecisionRequest, cancel: threading.Event):
        self.calls += 1
        return self.member_for(request.actor_id).decide(request, cancel)

    def health(self) -> dict:
        members = [m.health() if hasattr(m, "health") else {} for m in self.members]
        statuses = [h.get("status") for h in members]
        return {
            "status": "busy"
            if "busy" in statuses
            else ("failed" if "failed" in statuses or "crashed" in statuses else "ready"),
            "pid": None,
            "load_seconds": None,
            "memory_gb": None,
            "requests": sum(h.get("requests", 0) for h in members),
            "failures": sum(h.get("failures", 0) for h in members),
            "last_latency_ms": None,
        }

    def members_view(self) -> dict:
        """Per-organism model identity and health, for the observer."""
        views = [
            {
                **m.identity.to_dict(),
                "health": m.health() if hasattr(m, "health") else None,
            }
            for m in self.members
        ]
        return {actor: views[index] for actor, index in sorted(self.assignment.items())}

    def close(self) -> None:
        for member in self.members:
            if hasattr(member, "close"):
                member.close()
