import { vi } from "vitest";
import type {
  DecisionRecord,
  ModelsInfo,
  EventDetail,
  EventSummary,
  GridFrame,
  HistoryFrame,
  OrganismDetail,
  PopulationInfo,
  OrganismView,
  SnapshotInfo,
} from "./api";

export function makeFrame(overrides: Partial<GridFrame> = {}): GridFrame {
  const cells = Array.from({ length: 1024 }, (_, i) => ({
    x: i % 32,
    y: Math.floor(i / 32),
    energy: (i * 7) % 101,
    loose_material: i % 5 === 0 ? 3 : 0,
    packed_material: 0,
  }));
  return {
    schema_version: 9,
    mode: "live",
    run_id: "00000000-0000-4000-8000-000000000001",
    created_at: "2026-11-08T12:00:00Z",
    engine_version: "0.2.0",
    engine_source_hash: "a".repeat(64),
    rules_version: "m02-rules-1",
    state_schema: 3,
    manifest_hash: "d".repeat(64),
    config: {
      width: 32,
      height: 32,
      seed: 42,
      energy_cap: 100,
      packed_cap: 8,
      inflow_interval: 10,
      inflow_amount: 1,
      fixture: null,
    },
    tick: 0,
    status: "paused",
    started: false,
    speed: 5,
    until_tick: null,
    last_event_sequence: 1,
    changed_cells: [],
    changed_organisms: [],
    interactions: [],
    organisms: [],
    extinct: false,
    last_error: null,
    pending: null,
    provider: null,
    report_path: null,
    restored_from: null,
    dev_fixtures: [],
    journal_mode: "wal",
    cells,
    ledger: {
      energy: {
        initial: 1000,
        in_cells: 1000,
        in_organisms: 0,
        environmental_inflow: 0,
        dissipation: 0,
        death_retirement: 0,
        expected: 1000,
        actual: 1000,
      },
      material: {
        initial: 50,
        retired: 0,
        expected: 50,
        loose: 50,
        packed: 0,
        carried: 0,
        actual: 50,
      },
      balanced: true,
    },
    state_hash: "b".repeat(64),
    ...overrides,
  };
}

export const createdEvent: EventSummary = {
  sequence: 1,
  tick: 0,
  phase: "initialization",
  type: "run_created",
  rule_id: "init.seeded.v1",
  cause: "operator",
  actor_id: null,
  summary: {
    seed: 42,
    cells_total: 1024,
    initial_energy: 1000,
    initial_material: 50,
  },
};

export const inflowEvent: EventSummary = {
  sequence: 2,
  tick: 10,
  phase: "environment",
  type: "energy_inflow",
  rule_id: "env.energy_inflow.v1",
  cause: "environment",
  actor_id: null,
  summary: {
    requested_per_cell: 1,
    cells_total: 1024,
    cells_credited: 3,
    cells_at_cap: 1021,
    requested_total: 1024,
    credited_total: 3,
    blocked_total: 1021,
  },
};

export const inflowDetail: EventDetail = {
  ...inflowEvent,
  run_id: "00000000-0000-4000-8000-000000000001",
  payload: {},
  organism_changes: [],
  changes: [
    { x: 1, y: 0, field: "energy", before: 7, after: 8, delta: 1 },
    { x: 5, y: 2, field: "energy", before: 20, after: 21, delta: 1 },
    { x: 31, y: 31, field: "energy", before: 99, after: 100, delta: 1 },
  ],
  ledger: [{ resource: "energy", category: "environmental_inflow", amount: 3 }],
};

export function makeHistory(
  tick: number,
  overrides: Partial<HistoryFrame> = {},
): HistoryFrame {
  const live = makeFrame();
  return {
    schema_version: 9,
    mode: "history",
    run_id: live.run_id,
    tick,
    live_tick: 150,
    state_schema: 3,
    config: live.config,
    changed_cells: [],
    changed_organisms: [],
    interactions: [],
    organisms: [],
    extinct: false,
    cells: live.cells.map((c) => ({ ...c, energy: Math.max(0, c.energy - 1) })),
    ledger: live.ledger,
    state_hash: "e".repeat(64),
    recorded_hash: "e".repeat(64),
    verified: true,
    source: {
      kind: "snapshot",
      snapshot_tick: 0,
      ticks_replayed: tick,
      events_applied: Math.floor(tick / 10),
      skipped_snapshots: [],
    },
    ...overrides,
  };
}

export function makeOrganism(
  overrides: Partial<OrganismView> = {},
): OrganismView {
  return {
    id: "org-0001",
    label: "TEST POLICY: corner_tour",
    policy: "corner_tour",
    generation: 0,
    birth_tick: 0,
    x: 1,
    y: 0,
    energy: 57,
    energy_capacity: 100,
    age: 3,
    alive: true,
    death_tick: null,
    death_cause: null,
    retired_energy: 0,
    carried_material: 4,
    material_capacity: 20,
    retired_material: 0,
    lifespan: null,
    parent_ids: [],
    ...overrides,
  };
}

export function makeOrganismDetail(organism: OrganismView): OrganismDetail {
  return {
    run_id: "00000000-0000-4000-8000-000000000001",
    tick: 3,
    mode: "live",
    organism,
    latest_decision: {
      sequence: 9,
      tick: 3,
      phase: "action",
      type: "action_accepted",
      rule_id: "action.move.v1",
      cause: "organism",
      actor_id: organism.id,
      summary: {
        action: "move",
        direction: "east",
        energy_cost: 1,
        to_x: 1,
        to_y: 0,
      },
      run_id: "00000000-0000-4000-8000-000000000001",
      payload: {
        observation: {
          organism_id: organism.id,
          age: 3,
          energy: 58,
          energy_capacity: 100,
          carried_material: 4,
          material_capacity: 20,
          upkeep_per_tick: 1,
          perception_radius: 2,
          life_note: "Life is finite. Your own end is unknown.",
          actions: ["wait", "move"],
          cells: [
            {
              dx: 0,
              dy: 0,
              energy: 7,
              loose_material: 0,
              packed_material: 0,
              occupied: false,
            },
            {
              dx: 1,
              dy: 0,
              energy: 14,
              loose_material: 0,
              packed_material: 0,
              occupied: false,
            },
            {
              dx: 0,
              dy: 1,
              energy: 21,
              loose_material: 3,
              packed_material: 0,
              occupied: false,
            },
          ],
          organisms: [],
        },
        observation_sha256: "f".repeat(64),
        origin: { x: 0, y: 0 },
        proposal: { action: "move", direction: "east" },
        policy: "corner_tour",
      },
      organism_changes: [],
      changes: [],
      ledger: [{ resource: "energy", category: "movement", amount: 1 }],
    },
    events: [],
    action_preview: [
      {
        proposal: { action: "wait" },
        accepted: true,
        reason: null,
        energy_cost: 0,
      },
      {
        proposal: { action: "move", direction: "west" },
        accepted: true,
        reason: null,
        energy_cost: 3,
      },
      {
        proposal: { action: "move", direction: "north" },
        accepted: false,
        reason: "out_of_bounds",
        energy_cost: 0,
      },
      {
        proposal: { action: "modify", mode: "pack", amount: 1 },
        accepted: true,
        reason: null,
        energy_cost: 2,
      },
    ],
  };
}

export interface Backend {
  history?: (
    tick: number,
    source: string,
  ) => HistoryFrame | { status: number; detail: string };
  snapshots?: SnapshotInfo[];
  organism?: (id: string) => OrganismDetail | null;
  decisions?: DecisionRecord[];
  population?: PopulationInfo;
  models?: ModelsInfo;
  summary?: string;
  frame: GridFrame;
  events: EventSummary[];
  detail: EventDetail | null;
  controlResponse?: (
    body: Record<string, unknown>,
  ) => GridFrame | { status: number; detail: string };
}

/** A fake backend that routes by path, so tests assert behavior, not call order. */
export function stubBackend(backend: Backend) {
  const mock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const reply = (body: unknown, ok = true, status = 200) => ({
      ok,
      status,
      json: async () => body,
    });
    if (url === "/api/world") return reply(backend.frame);
    if (url.startsWith("/api/population"))
      return backend.population
        ? reply(backend.population)
        : reply({ detail: "Not Found" }, false, 404);
    if (url.startsWith("/api/history/")) {
      const [, tick, source] = url.match(/history\/(\d+)\?source=(\w+)/)!;
      const result = backend.history?.(Number(tick), source) ?? {
        status: 404,
        detail: "not recorded",
      };
      return "detail" in result
        ? reply({ detail: result.detail }, false, result.status)
        : reply(result);
    }
    if (url.startsWith("/api/decisions?"))
      return reply({
        run_id: backend.frame.run_id,
        decisions: backend.decisions ?? [],
      });
    if (url.startsWith("/api/decisions/")) {
      const id = decodeURIComponent(url.split("/")[3]);
      const found = (backend.decisions ?? []).find((d) => d.request_id === id);
      return found
        ? reply(found)
        : reply({ detail: "Decision not found" }, false, 404);
    }
    if (url.startsWith("/api/organisms/")) {
      const id = decodeURIComponent(url.split("/")[3].split("?")[0]);
      const detail = backend.organism?.(id);
      return detail
        ? reply(detail)
        : reply({ detail: "No such organism" }, false, 404);
    }
    if (url === "/api/summary")
      return init.method === "POST"
        ? reply({
            run_id: backend.frame.run_id,
            path: "/tmp/r.md",
            markdown: backend.summary ?? "# Summary",
            status: "stopped",
          })
        : backend.summary
          ? reply({
              run_id: backend.frame.run_id,
              path: "/tmp/r.md",
              markdown: backend.summary,
            })
          : reply({ detail: "No summary" }, false, 404);
    if (url === "/api/models")
      return reply(
        backend.models ?? {
          runtime: { available: false },
          model_dirs: [],
          models: [],
        },
      );
    if (url.startsWith("/api/providers/") && url.endsWith("/check"))
      return reply({ ok: true, message: "Connected; fake-chat is served" });
    if (url === "/api/snapshots")
      return reply({
        run_id: backend.frame.run_id,
        snapshots: backend.snapshots ?? [],
      });
    if (url.startsWith("/api/events?"))
      return reply({
        run_id: backend.frame.run_id,
        events: backend.events,
        has_more: false,
      });
    if (url.startsWith("/api/events/")) {
      return backend.detail
        ? reply(backend.detail)
        : reply({ detail: "Event not found" }, false, 404);
    }
    if (url.startsWith("/api/cells/")) {
      const [, x, y] = url.match(/cells\/(\d+)\/(\d+)/)!;
      const atTick = url.match(/at_tick=(\d+)/)?.[1];
      const index = Number(y) * 32 + Number(x);
      const hit = backend.detail?.changes.find(
        (c) => c.x === Number(x) && c.y === Number(y),
      );
      return reply({
        run_id: backend.frame.run_id,
        tick: atTick === undefined ? backend.frame.tick : Number(atTick),
        mode: atTick === undefined ? "live" : "history",
        index,
        cell: backend.frame.cells[index],
        entry_cost:
          1 + Math.floor(backend.frame.cells[index].packed_material / 2),
        recent_changes: hit
          ? [
              {
                event_sequence: 2,
                tick: 10,
                type: "energy_inflow",
                rule_id: "env.energy_inflow.v1",
                actor_id: null,
                field: hit.field,
                before: hit.before,
                after: hit.after,
                delta: hit.delta,
              },
            ]
          : [],
      });
    }
    if (url === "/api/control") {
      const body = JSON.parse(String(init.body));
      const result = backend.controlResponse?.(body) ?? backend.frame;
      if ("detail" in result)
        return reply({ detail: result.detail }, false, result.status);
      backend.frame = result;
      return reply(result);
    }
    if (url === "/api/runs") return reply(backend.frame, true, 201);
    return reply({ detail: "not found" }, false, 404);
  });
  return mock;
}

export function postsTo(mock: ReturnType<typeof stubBackend>, path: string) {
  return mock.mock.calls
    .filter(([url, init]) => url === path && init?.method === "POST")
    .map(([, init]) => JSON.parse(String(init!.body)));
}

export const recordedProvider = {
  kind: "recorded",
  name: "recorded_invalid",
  model: null,
  revision: "abc",
  capabilities: {
    structured_output: "none",
    max_context_tokens: null,
    max_output_tokens: null,
    concurrency: 1,
  },
  health: null,
  profile: null,
};

export function makeDecision(
  overrides: Partial<DecisionRecord> = {},
): DecisionRecord {
  return {
    request_id: "t00000001-org-0001",
    tick: 1,
    actor_id: "org-0001",
    input_hash: "c".repeat(64),
    provider: { kind: "recorded", name: "recorded_invalid" },
    raw_output: '{"action": "move", "direction": ',
    parse_status: "malformed_json",
    parse_error: "Expecting value: line 1 column 33",
    proposal: null,
    outcome: "unparsed",
    outcome_rule: "cognition.parse.v1",
    outcome_reason: "malformed_json",
    event_sequence: 5,
    usage: {
      input_tokens: null,
      output_tokens: null,
      latency_ms: 0,
      reused: false,
    },
    request: null,
    ...overrides,
  };
}
