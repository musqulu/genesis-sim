"""Plain-English run summaries, built only from recorded facts (no model writes them).

`build_summary` reads the run's manifest, committed state, events, decision attempts and
technical log; `render_markdown` turns that into sentences; `write_report` saves the result
as Markdown under data/reports/ (or GENESIS_REPORTS_DIR). Reading never changes a run, and a
failure to write is technical: it is logged and never alters the simulation.
"""

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from genesis.cognition.policy import FENCE_NOTE
from genesis.persistence.sqlite import SqliteStore

# Rejection reasons in words (diagnostics recorded by the engine and the parser).
REASONS = {
    "insufficient_source": "asked for more than the cell or its inventory held",
    "insufficient_energy": "did not have enough energy for the action",
    "out_of_bounds": "tried to move off the edge of the world",
    "occupied": "tried to move into a cell another organism occupied",
    "over_capacity": "would have held more than a capacity allows",
    "over_action_limit": "asked for more than one action allows",
    "invalid_arguments": "gave an action with missing or wrong details",
    "unknown_action": "asked for an action that does not exist",
    "actor_not_alive": "was no longer alive",
    "schema_invalid": "replied with something that did not match the allowed actions",
    "malformed_json": "replied with text that was not valid JSON",
    "empty_output": "replied with nothing",
    "not_an_object": "replied with JSON that was not a single object",
    "output_too_long": "replied with an overly long text",
}
CAUSES = {
    "age": "of old age (its hidden lifespan ran out)",
    "starvation": "of starvation (its energy reached zero)",
    "exhaustion": "of exhaustion (an action used its last energy)",
}


def reports_dir(store: SqliteStore) -> Path:
    configured = os.environ.get("GENESIS_REPORTS_DIR")
    return Path(configured) if configured else store.path.parent / "reports"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def build_summary(store: SqliteStore, run_id: str, *, status: str | None = None) -> dict:
    loaded = store.load_run(run_id)
    world, meta = loaded.world, loaded.meta
    with store._read() as db:
        manifest = json.loads(
            db.execute(
                "SELECT manifest_json FROM run_manifest WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        )
        events = db.execute(
            "SELECT tick, type, rule_id, actor_id, summary_json FROM events WHERE run_id = ? "
            "ORDER BY sequence",
            (run_id,),
        ).fetchall()
        attempts = db.execute(
            "SELECT tick, actor_id, raw_output, parse_status, outcome, outcome_rule, "
            "outcome_reason, usage_json, parse_error FROM decision_attempts "
            "WHERE run_id = ? ORDER BY tick",
            (run_id,),
        ).fetchall()
        packed = db.execute(
            "SELECT c.x, c.y, c.before, c.after, e.actor_id, e.tick FROM event_cell_changes c "
            "JOIN events e ON e.run_id = c.run_id AND e.sequence = c.event_sequence "
            "WHERE c.run_id = ? AND c.field = 'packed_material' ORDER BY e.sequence",
            (run_id,),
        ).fetchall()
        ledger = dict(
            ((r, c), a)
            for r, c, a in db.execute(
                "SELECT resource, category, SUM(amount) FROM ledger_entries WHERE run_id = ? "
                "GROUP BY resource, category",
                (run_id,),
            )
        )
        tech = db.execute(
            "SELECT kind, subject, detail FROM technical_log WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()

    profile = manifest.get("provider_profile") or {}
    provider = (world.life.provider if world.life else None) or "none"
    by_actor: dict[str, dict] = {}
    exchanges: list[tuple[str, dict]] = []
    for o in world.organisms:
        by_actor[o.id] = {
            "organism": o,
            "decisions": 0,
            "accepted": 0,
            "reasons": Counter(),
            "moves": 0,
            "energy_extracted": 0,
            "material_extracted": 0,
            "packed": 0,
            "unpacked": 0,
            "valid_json": 0,
            "fenced": 0,
            "latency": [],
            "tokens_in": [],
            "tokens_out": [],
            "quotes": [],
            "longest_streak": (0, None, None),
            "start": None,
        }
    for _tick, type_, _rule, actor, summary in events:
        if actor not in by_actor:
            continue
        s, a = json.loads(summary), by_actor[actor]
        if type_ == "organism_born":
            a["start"] = (s.get("x"), s.get("y"))
        elif type_ == "action_accepted":
            if s.get("action") == "move":
                a["moves"] += 1
            elif s.get("action") == "extract":
                a[f"{s['resource']}_extracted"] += s["amount"]
            elif s.get("action") == "modify":
                a["packed" if s["mode"] == "pack" else "unpacked"] += s["amount"]
            elif s.get("action") in ("transfer", "communicate"):
                exchanges.append((actor, s))
    for tick, actor, raw, parse_status, outcome, _rule, reason, usage, parse_note in attempts:
        a = by_actor.get(actor)
        if a is None:
            continue
        u = json.loads(usage)
        a["decisions"] += 1
        a["valid_json"] += parse_status != "malformed_json" and parse_status != "empty_output"
        a["fenced"] += parse_status == "valid" and parse_note == FENCE_NOTE
        for key, field in (
            ("latency", "latency_ms"),
            ("tokens_in", "input_tokens"),
            ("tokens_out", "output_tokens"),
        ):
            if u.get(field) is not None:
                a[key].append(u[field])
        if outcome == "accepted":
            a["accepted"] += 1
        else:
            a["reasons"][reason or parse_status] += 1
        try:
            explanation = json.loads(raw).get("explanation")
        except (ValueError, AttributeError):
            explanation = None
        if explanation:
            a["quotes"].append((tick, outcome, reason, explanation))

    # Longest run of consecutive non-accepted decisions per organism (first and last tick).
    for actor, a in by_actor.items():
        best, run, start = (0, None, None), 0, None
        for tick, who, _raw, _ps, outcome, _rule, _reason, _u, _note in attempts:
            if who != actor:
                continue
            if outcome == "accepted":
                run, start = 0, None
                continue
            run += 1
            start = tick if run == 1 else start
            if run > best[0]:
                best = (run, start, tick)
        a["longest_streak"] = best

    if status is None:
        status = "extinct" if world.extinct else "running"
    return {
        "run_id": run_id,
        "seed": world.config.seed,
        "fixture": world.config.fixture,
        "created_at": meta.created_at.isoformat(),
        "rules_version": meta.rules_version,
        "tick": world.tick,
        "status": status,
        "extinct": world.extinct,
        "provider": provider,
        "model": profile.get("model_id"),
        "profile": profile,
        "actors": by_actor,
        "packed": packed,
        "exchanges": exchanges,
        "ledger": ledger,
        "ledger_balanced": world.total_energy == world.ledger.expected_energy
        and world.total_material == world.ledger.expected_material,
        "technical": tech,
        "attempts": len(attempts),
    }


def _cognition_words(summary: dict) -> str:
    return _source_words(summary["provider"], summary["profile"])


def _source_words(provider: str, profile: dict) -> str:
    if provider.startswith("mlx:"):
        return f"the local MLX-LM model {profile.get('model_id') or provider[4:]}"
    if provider.startswith("api:"):
        model = profile.get("model") or provider[4:]
        if profile.get("protocol") == "replicate":
            return f"the remote API model {model} via Replicate"
        host = urlsplit(profile.get("base_url", "")).netloc or "an API endpoint"
        where = "remote" if profile.get("remote") else "self-hosted"
        return f"the {where} API model {model} at {host}"
    if provider.startswith("recorded:"):
        return f"recorded test responses ({provider[9:]})"
    if provider == "scripted":
        return "scripted test policies"
    if provider == "mixed":
        return "several models (listed above)"
    return "no decision source"


def _member_words(summary: dict) -> dict[str, str]:
    """Each organism's decision source in a mixed-model run (empty otherwise)."""
    profile = summary["profile"]
    if summary["provider"] != "mixed":
        return {}
    members = profile.get("members", [])
    return {
        actor: _source_words(members[i]["cognition"], members[i]["profile"])
        for actor, i in profile.get("assignment", {}).items()
    }


def render_markdown(summary: dict) -> str:
    s = summary
    actors = s["actors"]
    n = len(actors)
    lines = [f"# Genesis run summary — {s['run_id'][:8]}", ""]
    status = {
        "extinct": f"The population went extinct at tick {s['tick']}; the run is archived.",
        "running": f"The run is still going (summary taken at tick {s['tick']}).",
        "replaced": f"The run was stopped at tick {s['tick']} when a new run was started.",
        "stopped": f"The run was stopped at tick {s['tick']}.",
    }.get(s["status"], f"Summary taken at tick {s['tick']}.")
    mixed = _member_words(s)
    if n == 0:
        who = "no organisms (an environment-only run)"
    else:
        who = f"{_plural(n, 'organism')} whose decisions came from {_cognition_words(s)}"
        if mixed:
            who = (
                f"{_plural(n, 'organism')} in a mixed-model experiment, each with its own "
                "model for life: "
                + "; ".join(f"{actor} used {words}" for actor, words in mixed.items())
            )
    fixture = f" It was the labelled test fixture `{s['fixture']}`." if s["fixture"] else ""
    lines += [
        "## In short",
        "",
        f"Run `{s['run_id']}` (seed {s['seed']}) began with {who}.{fixture} {status}",
        "",
    ]
    if n:
        dead = [a["organism"] for a in actors.values() if not a["organism"].alive]
        alive = [a["organism"] for a in actors.values() if a["organism"].alive]
        if not dead:
            fate = "None of them died; " + (
                "it is still alive." if len(alive) == 1 else f"all {len(alive)} are still alive."
            )
        elif not alive:
            fate = (
                "It died."
                if len(dead) == 1
                else "Both died."
                if len(dead) == 2
                else f"All {len(dead)} died."
            )
        else:
            fate = (
                f"{_plural(len(dead), 'organism')} died and {len(alive)} "
                f"{'is' if len(alive) == 1 else 'are'} still alive."
            )
        lines.append(f"{fate} Together they made {_plural(s['attempts'], 'decision')}.")
        lines.append("")
        lines += ["## Each organism", ""]
        for a in actors.values():
            o = a["organism"]
            start = a["start"] or (o.x, o.y)
            head = f"### {o.id} ({o.label.lower()})"
            lines += [head, ""]
            if o.id in mixed:
                lines.append(f"Its decisions came from {mixed[o.id]}.")
            age = None if o.alive else o.death_tick - o.birth_tick
            life = (
                f"It was born at ({start[0]}, {start[1]}) and died at tick {o.death_tick} "
                f"{CAUSES.get(o.death_cause, o.death_cause)}, at age {age}. "
                f"Its hidden lifespan would have been {o.lifespan} ticks, so it lived "
                f"{round(100 * (o.death_tick - o.birth_tick) / o.lifespan)}% of it."
                if not o.alive
                else f"It was born at ({start[0]}, {start[1]}) and is alive at ({o.x}, {o.y}) "
                f"with {o.energy} energy and {o.carried_material} carried material."
            )
            lines.append(life)
            if a["decisions"]:
                rejected = a["decisions"] - a["accepted"]
                lines.append(
                    f"It made {_plural(a['decisions'], 'decision')}: {a['accepted']} "
                    f"carried out and {rejected} rejected by the world."
                )
                for reason, count in a["reasons"].most_common():
                    lines.append(f"- {count}× it {REASONS.get(reason, reason.replace('_', ' '))}.")
                did = []
                if a["moves"]:
                    did.append(f"moved {_plural(a['moves'], 'time')}")
                if a["energy_extracted"]:
                    did.append(f"took {a['energy_extracted']} energy from cells")
                if a["material_extracted"]:
                    did.append(f"collected {a['material_extracted']} material")
                if a["packed"]:
                    did.append(f"packed {a['packed']} material into the ground")
                if a["unpacked"]:
                    did.append(f"unpacked {a['unpacked']}")
                if did:
                    lines.append("What it actually did: it " + ", ".join(did) + ".")
                streak, first, last = a["longest_streak"]
                if streak >= 3:
                    lines.append(
                        f"Its longest run of failed attempts was {streak} in a row "
                        f"(ticks {first}–{last})."
                    )
            if not o.alive and (o.retired_energy or o.retired_material):
                held = " and ".join(
                    f"{v} {k}"
                    for k, v in (("energy", o.retired_energy), ("material", o.retired_material))
                    if v
                )
                lines.append(
                    f"At death it still held {held}; this was retired, not left in the cell."
                )
            if a["quotes"]:
                picks = [a["quotes"][0]]
                rejected = [q for q in a["quotes"] if q[1] != "accepted"]
                if rejected and rejected[0] not in picks:
                    picks.append(rejected[0])
                if a["quotes"][-1] not in picks:
                    picks.append(a["quotes"][-1])
                lines += ["", "In its own words (the model's explanations, not verified facts):"]
                for tick, outcome, reason, text in picks[:3]:
                    result = "carried out" if outcome == "accepted" else f"rejected: {reason}"
                    lines.append(f"> tick {tick} ({result}): “{text}”")
            lines.append("")
    lines += ["## The world", ""]
    inflow = s["ledger"].get(("energy", "environmental_inflow"), 0)
    lines.append(f"The environment added {inflow} energy to the grid over {s['tick']} ticks.")
    written = s["ledger"].get(("energy", "memory_write_fee"), 0)
    kept = s["ledger"].get(("energy", "memory_upkeep"), 0)
    if written or kept:
        lines.append(
            f"Memory cost the organisms {written} energy to write and {kept} energy to keep."
        )
    gifts = [(a, x) for a, x in s.get("exchanges", []) if x["action"] == "transfer"]
    words = [(a, x) for a, x in s.get("exchanges", []) if x["action"] == "communicate"]
    if gifts or words:
        lines.append(
            f"Organisms gave each other resources {len(gifts)} time(s) and sent "
            f"{len(words)} message(s)."
        )
        for actor, x in words[:3]:
            lines.append(f"> {actor} to {x['target']}: “{x['text'][:120]}”")
    if s["packed"]:
        final: dict[tuple[int, int], tuple[int, str]] = {}
        for x, y, _before, after, actor, _tick in s["packed"]:
            final[(x, y)] = (after, actor)
        changed = [f"({x}, {y}) now holds {v} packed by {a}" for (x, y), (v, a) in final.items()]
        lines.append(
            "Lasting changes made by organisms: " + "; ".join(changed) + ". These cells now cost "
            "more energy to enter, and stay changed after their makers die."
        )
    else:
        lines.append("No organism permanently changed any cell.")
    lines.append(
        "The resource ledger balances: every unit of energy and material is accounted for."
        if s["ledger_balanced"]
        else "WARNING: the resource ledger does not balance — this is a technical fault."
    )
    lines.append("")
    if s["attempts"]:
        lat = [x for a in actors.values() for x in a["latency"]]
        tin = [x for a in actors.values() for x in a["tokens_in"]]
        tout = [x for a in actors.values() for x in a["tokens_out"]]
        valid = sum(a["valid_json"] for a in actors.values())
        lines += ["## How the decisions were made", ""]
        fenced = sum(a["fenced"] for a in actors.values())
        if mixed:
            for words in dict.fromkeys(mixed.values()):
                group = [actors[x] for x, w in mixed.items() if w == words and x in actors]
                g_lat = [x for a in group for x in a["latency"]]
                g_in = [x for a in group for x in a["tokens_in"]]
                g_out = [x for a in group for x in a["tokens_out"]]
                n_dec = sum(a["decisions"] for a in group)
                line = (
                    f"- {words[0].upper()}{words[1:]}: {_plural(n_dec, 'decision')}, "
                    f"{sum(a['valid_json'] for a in group)} well-formed"
                )
                if sum(a["fenced"] for a in group):
                    line += f" ({sum(a['fenced'] for a in group)} inside a code fence)"
                if g_lat:
                    line += f", average {sum(g_lat) / len(g_lat) / 1000:.1f} s"
                if g_in:
                    line += f", prompts ~{round(sum(g_in) / len(g_in))} tokens"
                if g_out:
                    line += f", replies ~{round(sum(g_out) / len(g_out))} tokens"
                lines.append(line + ".")
            lines.append("")
        lines.append(
            f"Decisions came from {_cognition_words(s)}. {valid} of {s['attempts']} replies "
            "were well-formed JSON."
            + (
                f" {fenced} of them came wrapped in a ```json code fence, which was removed "
                "before reading (the raw replies are kept as sent)."
                if fenced
                else ""
            )
        )
        if lat and max(lat) < 100:
            lines.append("Each decision took under 0.1 s.")
        elif lat:
            lines.append(
                f"Each decision took {min(lat) / 1000:.1f}–{max(lat) / 1000:.1f} s "
                f"(average {sum(lat) / len(lat) / 1000:.1f} s)."
            )
        if tin and tout:
            lines.append(
                f"Prompts averaged {round(sum(tin) / len(tin))} tokens and replies "
                f"{round(sum(tout) / len(tout))} tokens."
            )
        settings = s["profile"].get("settings")
        if settings is None and s["profile"].get("kind") == "api":
            settings = s["profile"]
        if settings:
            lines.append(
                f"Settings: temperature {settings['temperature']}, at most "
                f"{settings['max_output_tokens']} output tokens, prompt "
                f"{s['profile'].get('prompt_version')}."
            )
        lines.append("")
    failures = [t for t in s["technical"] if t[0] == "provider_failure"]
    if failures or s["technical"]:
        lines += ["## Technical notes", ""]
        if failures:
            lines.append(
                f"The model provider failed {_plural(len(failures), 'time')}; each time logical "
                "time waited and nothing was lost."
            )
        other = Counter(t[0] for t in s["technical"] if t[0] != "provider_failure")
        for kind, count in other.items():
            lines.append(f"- {count}× {kind.replace('_', ' ')}")
        lines.append("")
    lines += [
        "---",
        f"Rules {s['rules_version']}. Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC from the "
        "recorded history only; no model wrote this text.",
        "",
    ]
    return "\n".join(lines)


def report_path(store: SqliteStore, run_id: str) -> Path | None:
    folder = reports_dir(store)
    matches = sorted(folder.glob(f"*_{run_id[:8]}_seed*.md")) if folder.is_dir() else []
    return matches[-1] if matches else None


def write_report(store: SqliteStore, run_id: str, status: str | None = None) -> tuple[Path, str]:
    summary = build_summary(store, run_id, status=status)
    text = render_markdown(summary)
    folder = reports_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    existing = report_path(store, run_id)
    created = datetime.fromisoformat(summary["created_at"])
    path = existing or folder / f"{created:%Y-%m-%d}_{run_id[:8]}_seed{summary['seed']}.md"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path, text
