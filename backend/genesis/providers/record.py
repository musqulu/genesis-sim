"""Generate recorded-response fixtures (development tool; not used at run time).

    uv run python -m genesis.providers.record            # rewrite all recordings
    uv run python -m genesis.providers.record --check    # verify they are current

Each fixture is simulated with the real engine, context builder and parser; the n-th
decision request receives the n-th scripted raw response (the last one repeats). The file
stores request id, input hash, raw text and delay for every request, so the recorded
provider can answer strictly by input hash.
"""

import hashlib
import json
import sys

from genesis.cognition.context import build_request
from genesis.cognition.session import decision_payload, parse_for, update_previous
from genesis.domain.config import FIXTURES, WorldConfig
from genesis.engine.actions import available_actions
from genesis.engine.world import create_world, finish_tick, prepare_tick
from genesis.providers.recorded import RECORDINGS

VALID = [
    '{"action": "extract", "resource": "material", "amount": 5, '
    '"explanation": "Material is here; take some."}',
    '{"action": "modify", "mode": "pack", "amount": 2, "explanation": "Pack two units here."}',
    '{"action": "move", "direction": "east"}',
    '{"action": "extract", "resource": "energy", "amount": 4}',
    '{"action": "move", "direction": "west", "explanation": "Back to the packed cell."}',
    '{"action": "wait", "explanation": "Conserve energy."}',
]

INVALID = [
    '{"action": "move", "direction": ',  # malformed JSON
    '{"action": "extract", "resource": "material", "amount": "ten"}',  # wrong type
    '{"action": "extract", "resource": "material", "amount": 99}',  # out of range
    '{"action": "teleport", "x": 3, "y": 4}',  # unknown action
    '{"action": "wait", "mood": "calm"}',  # unknown field
    "",  # empty output
    '{"action": "move", "direction": "north"}',  # well-formed but illegal at y = 0
    '[{"action": "wait"}]',  # not an object
    '{"action": "wait", "action": "move", "direction": "east"}',  # duplicate key
    '{"action": "wait", "explanation": "Nothing valid to do."}',  # finally valid
]

SPECS = {
    "recorded_valid": {"responses": VALID},
    "recorded_invalid": {"responses": INVALID},
    "recorded_delayed": {"responses": VALID, "delay_ms": 3000},
    "recorded_mismatch": {"responses": VALID, "corrupt": {1}},
}
MAX_TICKS = 400


def generate(name: str) -> dict:
    spec = SPECS[name]
    fixture = FIXTURES[name]
    world = create_world(WorldConfig.for_run(42, name), fixture.life)
    previous: dict[str, dict] = {}
    entries = []
    while not world.extinct and world.tick < MAX_TICKS:
        prepared = prepare_tick(world)
        outcomes = {}
        for slot in prepared.slots:
            request = build_request(
                "recording", prepared.world, slot, previous.get(slot.organism_id)
            )
            index = len(entries)
            raw = spec["responses"][min(index, len(spec["responses"]) - 1)]
            input_hash = request.input_hash
            if index in spec.get("corrupt", ()):  # deliberately unmatched entry
                input_hash = hashlib.sha256(b"corrupted:" + input_hash.encode()).hexdigest()
            entries.append(
                {
                    "request_id": request.request_id,
                    "input_hash": input_hash,
                    "raw": raw,
                    "delay_ms": spec.get("delay_ms", 0),
                }
            )
            parsed = parse_for(request, raw, prepared.world.life, available_actions(prepared.world))
            outcomes[slot.organism_id] = (
                parsed.outcome,
                decision_payload(slot, request, raw, parsed),
            )
        result = finish_tick(prepared, outcomes)
        update_previous(previous, result)
        world = result.world
    body = {"format": 1, "name": name, "fixture": name, "seed": 42, "entries": entries}
    body["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return body


def render(data: dict) -> str:
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    stale = []
    for name in SPECS:
        text = render(generate(name))
        path = RECORDINGS / f"{name}.json"
        if "--check" in argv:
            if not path.is_file() or path.read_text() != text:
                stale.append(name)
        else:
            path.write_text(text)
            print(f"wrote {path.name}: {len(json.loads(text)['entries'])} entries")
    if stale:
        print("stale recordings:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
