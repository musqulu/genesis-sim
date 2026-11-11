# GENESIS — Project Constitution

**Status:** v0.1 · Foundational design document  
**Purpose:** The shared source of truth for human contributors and AI coding agents working on Genesis.  
**Working title:** Genesis

> **Core idea:** Create the minimal conditions for autonomous artificial organisms to exist, act, interact, reproduce, die, and alter a shared digital world. Do not design the history, civilization, or final appearance of that world. Observe what emerges from the organisms' decisions across generations.

Genesis is a long-running artificial-life experiment and, potentially, an evolving artwork. It is **not** a game with a winning condition, a chatbot town, a scripted civilization, or a simulation whose outcome is known in advance. The author designs fundamental constraints and experimental conditions; the organisms generate their own histories within them.

## 1. Non-negotiable principles

1. **Finite lives.** Every organism has a bounded lifespan. Organisms may know that life is finite or learn typical lifespans, but they must not know the precise moment of their own death. Death ends that instance's ability to act.
2. **Limited resources.** Actions, communication, memory, and environmental modifications have costs. Resources must be accounted for and cannot be created arbitrarily by an LLM response.
3. **Partial knowledge.** No organism has omniscient access to the world. It can act on local observations, remembered experiences, and information acquired from others.
4. **Consequential actions.** Actions can persistently alter the world. New organisms inherit the environment shaped by earlier lives. Death itself does not magically transform the landscape; only effects produced during life persist.
5. **Variable inheritance.** Some offspring may inherit portions of traits, memories, strategies, or information; others may start without a direct lineage. Inheritance is partial and imperfect, not the guaranteed continuation of one consciousness.
6. **Mutable local order, stable foundation.** Organisms may eventually create or change local structures, conventions, and protocols using the world's allowed primitives. They cannot override fundamental invariants or execute arbitrary code in the host system.
7. **No guaranteed progress.** Cooperation, specialization, reproduction, collapse, stagnation, or extinction are all legitimate outcomes. Never secretly rescue a failing population or force a particular story.
8. **Observable causality.** Every consequential change should be attributable to an organism action, an environmental rule, or a logged external intervention.

These principles define the experiment. Changing them requires an explicit, versioned design decision, not a silent coding convenience.

## 2. The world: a mutable grid

- The **canonical world is a two-dimensional cell grid**, not a free-form node graph. A suggested starting size is **32 × 32** cells; this is a provisional MVP parameter, not a permanent rule.
- Grid coordinates are the stable substrate. The **state and effective geography** of the grid can change continually.
- Cells may hold energy, material or storage capacity, information, resistance to movement, signal/connectivity properties, and structures made by organisms. Keep the schema minimal at first.
- Organisms have locations and limited perception and action ranges. Movement, resource transfer, information flow, persistence, and decay follow explicit environmental rules.
- The environment can evolve between organism decisions through simple simulation processes. Distinguish changes caused by the world engine from those caused by agents.
- Do **not** predefine cities, roads, professions, political systems, or other expected outcomes. Supply composable primitives and let larger patterns arise, or fail to arise.
- A grid is the world's **logic**, not just an aesthetic overlay. Renderers must not become the source of truth.

## 3. Organisms: motivations, not prescribed identities

Organisms are independent **agent instances** with their own state, memory, position, experiences, and decision history. They may share underlying model weights without sharing personal identity.

Initial organisms should generally **not** be assigned human-style roles. Instead, give them different starting tendencies, such as:

- self-maintenance / energy-seeking;
- caution / risk avoidance;
- curiosity / information-seeking;
- communication / affiliation;
- experimentation / novelty-seeking;
- transmission of knowledge or traits.

These are competing dispositions, **not mandatory objectives or fixed classes**. Current needs and experience should influence decisions; dispositions may change over time. Roles and recurring strategies should be discovered through observed behavior, not labeled in advance.

Later experiments may deliberately introduce agents with assigned roles or different LLMs. Such interventions must be explicitly marked and logged.

## 4. Life, reproduction, and inheritance

- Start with a small **seed population** (suggested: 8–12 organisms).
- There is no target population size that the engine tries to maintain.
- Existing organisms can **choose to attempt reproduction**, provided world constraints and resource costs permit it. A birth is never guaranteed merely because the population is low.
- Offspring may inherit altered traits and selected memories or strategies, while receiving some new variation.
- Organisms die when their lifespan or other valid world conditions end their existence. Historical records remain; the dead instance does not resume acting.
- If all organisms die, **record and archive the extinction**. A new run can begin with a new seed or experimental conditions, but should never be misrepresented as uninterrupted survival.

“Reincarnation” is an artistic metaphor for partial transmission of information and influence, **not** a claim that AI consciousness is transferred.

## 5. Agency and the action boundary

An LLM may **propose** an action; only the simulation engine can validate and commit it.

For the first version, prefer a small, structured, composable action vocabulary, such as `observe`, `move`, `extract`, `transfer`, `communicate`, `modify`, and `reproduce`. Exact action names and parameters are implementation details.

- Validate range, cost, permissions, available resources, and consequences.
- Invalid actions must not mutate state. Record them as diagnostics.
- Do not give agents direct database writes, unrestricted filesystem access, arbitrary code execution, or the power to change immutable physics.
- Complex behavior should arise through sequences of simple actions, not shortcuts such as `build_city` or `establish_civilization`.

## 6. Memory, adaptation, and models

Agent cognition may use an **observe → recall → evaluate → decide → act → reflect** loop, inspired by generative-agent research. Keep that cognition separate from the world engine.

- Each instance owns its memories and evolving state.
- Summaries or reflections are useful, but must not replace the actual historical record.
- Passing memories to offspring is different from retraining model weights; do not claim weight-level evolution unless implemented and measured.
- Begin with one local LLM serving multiple isolated agent contexts. Keep a **model-provider adapter** so later runs can compare different local models or mixed-model populations.
- Use event-driven inference where possible. Routine physical simulation steps should not require an LLM call.

## 7. Recording is part of the artwork

The system must support more than a live animation: it must enable **archeology of a world that no longer exists in its earlier form**.

Preserve at minimum:

- organism births, deaths, identities, and ancestry;
- state-changing actions, actors, locations, and outcomes;
- mutations, inherited data, and changing organism tendencies;
- environmental changes and their causes;
- any human/public intervention;
- experiment configuration, world seed, model identity/version, and relevant inference settings.

Use a persistent append-only event record plus periodic snapshots where useful. Replay should reconstruct recorded history, rather than asking LLMs to generate the same decisions again. Keep raw decisions or enough metadata to inspect *why* something happened, subject to practical storage limits.

## 8. Reliability is not evolution

Separate the **organism/world layer** from the **technical operations layer**.

- Technical failures (crashes, interrupted inference, corrupted transient processes) should be detected, logged, and recovered from safely.
- Ecological outcomes (an organism's death, resource depletion, population extinction, unproductive behavior) should **not** be silently corrected.
- Technical cleanup may archive/compress logs or reclaim temporary files; it must not erase meaningful world history or remove structures simply because they appear unused.
- AI-generated code changes may be suggested and tested in isolation later, but **must not autonomously deploy** to the running experiment without explicit approval and versioned tests.

## 9. Visualization and physical installation

The world state must be independent of its display.

- **First interface:** a browser-based, inspectable 2D grid showing organisms, resources, changes, and history.
- **Future output:** individually addressable LED strips or matrices, using the same canonical grid through a configurable mapping layer.
- Different projections may show different aspects of the world: current organisms, traces, accumulated structures, signals, or past events.
- Light and color are interpretations of data. They must not secretly determine simulation behavior.
- Allow resampling between the simulation grid and LED resolution (e.g., 32 × 32 simulation mapped to 16 × 16 LEDs).

## 10. MVP 0.1: prove the loop, not the spectacle

**Build now:**

1. A persistent 2D grid with simple resources and environmental rules.
2. Roughly 8–12 independent agents with varied motivations and local observations.
3. One shared local LLM with structured, validated action proposals.
4. Costs, aging, mortality, resource exchange, and persistent cell modification.
5. Agent-initiated reproduction with partial inherited traits/memory.
6. Full causal event logging, snapshots, pause/resume, and basic replay.
7. A simple web grid and an inspector for individual organisms.
8. Automated technical health checks and safe recovery after crashes.

**Do not build yet:** public participation, 3D terrain, elaborate LED hardware, unsandboxed self-modifying code, complex agent-written legal systems, automatic evolutionary “improvement,” or a predetermined narrative.

**First proof of concept:** demonstrate that the system *permits* autonomous second-generation birth, partial inheritance, and lasting grid modifications, and that an observer can trace those changes to actual decisions. The agents are **not required** to succeed in every run.

## 11. Rules for contributors and AI coding agents

Before proposing or implementing features:

1. **Read this document** and identify which principles the feature serves.
2. Prefer the **smallest testable mechanism** that expands organisms' possible behaviors over scripted, human-recognizable outcomes.
3. Keep **world physics, agent cognition, simulation state, rendering, and technical operations** separate.
4. Treat every novel capability as a **hypothesis to test**, not evidence that emergence has occurred.
5. Log environmental interventions and preserve reproducibility wherever feasible.
6. Never equate compelling generated language with consciousness, intention, or genuine evolutionary adaptation without suitable evidence.
7. If a request conflicts with the constitution, **flag the conflict** and propose an explicit revision rather than silently altering core principles.
8. Keep this file stable. Document approved constitutional changes with a version and rationale; keep evolving implementation details in separate technical docs.

## 12. Open questions — intentionally unresolved

The first implementation should inform, not prematurely settle, these choices:

- What is the smallest set of cell properties that still permits interesting environmental change?
- Which actions are powerful enough to enable unforeseen structures without encoding their outcomes?
- How do motivation weights change with experience?
- When and how do organisms attempt reproduction? How much variation is inherited?
- Can organisms create enduring local protocols from existing primitives?
- Which measures distinguish novelty, diversity, adaptation, and simple repetition?
- How should a public observer eventually introduce new organisms or environmental stimuli without controlling results?

---

**North star:** *Design the conditions of existence, not the organisms' destiny. Let the world become the record of lives that shaped it.*
