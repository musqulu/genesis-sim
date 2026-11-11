"""Deterministic scripted test policies served through the provider contract.

The script is chosen by the organism's fixture policy and indexed by the observed age; the
raw output is the JSON a model would have to produce. Diagnostics, never autonomy.
"""

import json
import threading
import time

from genesis.cognition.context import DecisionRequest
from genesis.cognition.fixture_policy import CONTEXT_POLICIES, script_step
from genesis.providers.base import (
    Capabilities,
    Provider,
    ProviderIdentity,
    ProviderResponse,
    Usage,
)


class ScriptedProvider(Provider):
    capabilities = Capabilities("none", None, None, 1)

    def __init__(self, policies: dict[str, str]):
        self.identity = ProviderIdentity("scripted", "fixture scripts")
        self.policies = dict(policies)  # actor id -> script name (fixed at run creation)
        self.calls = 0

    def decide(self, request: DecisionRequest, cancel: threading.Event) -> ProviderResponse:
        self.calls += 1
        started = time.monotonic()
        name = self.policies[request.actor_id]
        if name in CONTEXT_POLICIES:  # reads only the request context, like a model would
            proposal = CONTEXT_POLICIES[name](request.context)
        else:
            proposal = script_step(name, request.context["observation"]["age"])
        raw = json.dumps(proposal.to_dict(), sort_keys=True)
        return ProviderResponse(
            request.request_id,
            request.input_hash,
            raw,
            Usage(latency_ms=int((time.monotonic() - started) * 1000)),
            finish_reason="stop",
        )
