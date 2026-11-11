import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import App from "./App";
import {
  createdEvent,
  inflowDetail,
  inflowEvent,
  makeFrame,
  makeHistory,
  makeOrganism,
  makeOrganismDetail,
  makeDecision,
  recordedProvider,
  postsTo,
  stubBackend,
} from "./testFixtures";
import type { Backend } from "./testFixtures";
import type { EventSummary } from "./api";

const fixture = makeFrame();

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    setTransform: vi.fn(),
    fillRect: vi.fn(),
    strokeRect: vi.fn(),
    // Exchange arrows (M09).
    save: vi.fn(),
    restore: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    closePath: vi.fn(),
    stroke: vi.fn(),
    fill: vi.fn(),
    setLineDash: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function openDashboard() {
  fireEvent.click(screen.getByRole("button", { name: "Dashboard" }));
}

function backend(overrides: Partial<Backend> = {}): Backend {
  return { frame: fixture, events: [createdEvent], detail: null, ...overrides };
}

it("loads the backend frame, inspects values and keeps run identity on remount", async () => {
  const mock = stubBackend(backend());
  vi.stubGlobal("fetch", mock);
  const app = render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);
  fireEvent.change(screen.getByLabelText("Cell x"), {
    target: { value: "31" },
  });
  fireEvent.change(screen.getByLabelText("Cell y"), {
    target: { value: "31" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
  expect(screen.getByTestId("selected-cell").textContent).toBe("31,31");
  const last = fixture.cells[1023];
  expect(screen.getByTestId("cell-energy").textContent).toBe(
    `${last.energy} / 100`,
  );
  expect(screen.getByText("1023")).toBeTruthy();
  expect(screen.getByText("1,024 cells")).toBeTruthy();
  expect(screen.getByTestId("run-status").textContent).toContain("Paused");
  expect(screen.getByTestId("ledger-status").textContent).toContain("balanced");
  expect(mock.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(
    true,
  );
  app.unmount();
  render(<App />);
  openDashboard();
  expect(await screen.findByText(fixture.run_id)).toBeTruthy();
});

it("shows disconnected state rather than a fictional grid, then reconnects", async () => {
  const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  openDashboard();
  await screen.findByText("Backend unavailable");
  expect(screen.queryByRole("img")).toBeNull();
  vi.stubGlobal("fetch", stubBackend(backend()));
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  await screen.findByText(fixture.run_id);
  expect(screen.queryByText("Backend unavailable")).toBeNull();
});

it("creates a run only after an explicit submit with the chosen seed", async () => {
  const state = backend();
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);
  expect(screen.queryByLabelText("Test fixture")).toBeNull(); // dev-only control
  state.frame = makeFrame({
    run_id: "00000000-0000-4000-8000-000000000002",
    config: { ...fixture.config, seed: 123 },
  });
  fireEvent.change(screen.getByLabelText("World seed"), {
    target: { value: "123" },
  });
  fireEvent.click(screen.getByRole("button", { name: /New run/ }));
  await screen.findByText(state.frame.run_id);
  expect(postsTo(mock, "/api/runs")).toEqual([{ seed: 123 }]);
});

it("offers labeled test fixtures only when the backend reports dev mode", async () => {
  const state = backend({
    frame: makeFrame({ dev_fixtures: ["zero_inflow"] }),
  });
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);
  fireEvent.change(screen.getByLabelText("Test fixture"), {
    target: { value: "zero_inflow" },
  });
  state.frame = makeFrame({
    dev_fixtures: ["zero_inflow"],
    run_id: "00000000-0000-4000-8000-000000000003",
    config: { ...fixture.config, fixture: "zero_inflow", inflow_amount: 0 },
  });
  fireEvent.click(screen.getByRole("button", { name: /New run/ }));
  expect((await screen.findByTestId("fixture-tag")).textContent).toContain(
    "zero_inflow",
  );
  expect(postsTo(mock, "/api/runs")).toEqual([
    { seed: 42, fixture: "zero_inflow" },
  ]);
});

it("rejects a mismatched grid instead of rendering it", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ...fixture, cells: fixture.cells.slice(1) }),
    }),
  );
  render(<App />);
  openDashboard();
  await waitFor(() =>
    expect(screen.getByText("Backend unavailable")).toBeTruthy(),
  );
  expect(screen.queryByRole("img")).toBeNull();
});

it("reports a backend on an older contract as a version mismatch, not an outage", async () => {
  for (const version of [1, 3, 6]) {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ ...fixture, schema_version: version }),
      }),
    );
    const view = render(<App />);
    openDashboard();
    expect((await screen.findByTestId("version-alert")).textContent).toContain(
      `sent frame version ${version}; this page expects version 8`,
    );
    expect(screen.queryByText("Backend unavailable")).toBeNull();
    expect(screen.getByText("Version mismatch")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    view.unmount();
  }
});

it("retains and labels last confirmed state when a later poll fails", async () => {
  let poll: (() => void) | undefined;
  const originalInterval = window.setInterval.bind(window);
  vi.spyOn(window, "setInterval").mockImplementation((callback, delay) => {
    if (delay === 1000) {
      poll = callback as () => void;
      return 99;
    }
    return originalInterval(callback, delay);
  });
  vi.stubGlobal("fetch", stubBackend(backend()));
  render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("Backend stopped")),
  );
  await act(async () => {
    poll!();
  });
  await screen.findByText("Backend unavailable");
  expect(screen.getByText(/Showing the last confirmed state/)).toBeTruthy();
  expect(screen.getByTestId("run-id").textContent).toBe(fixture.run_id);
  expect(
    (screen.getByRole("button", { name: /New run/ }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "Step" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it("sends start, step, speed and pause-at commands with unique ids and shows the returned state", async () => {
  const state = backend();
  state.controlResponse = (body) => {
    if (body.command === "start")
      return makeFrame({
        status: "running",
        started: true,
        until_tick: body.until_tick as number,
      });
    if (body.command === "speed")
      return makeFrame({ speed: body.ticks_per_second as number });
    return makeFrame({ tick: 1, started: false });
  };
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);

  fireEvent.click(screen.getByRole("button", { name: "Step" }));
  await waitFor(() =>
    expect(screen.getByTestId("tick").textContent).toBe("000001"),
  );
  fireEvent.change(screen.getByLabelText("Speed"), { target: { value: "20" } });
  await waitFor(() => expect(postsTo(mock, "/api/control")).toHaveLength(2));
  fireEvent.change(screen.getByLabelText("Pause at tick"), {
    target: { value: "100" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start" }));
  await waitFor(() =>
    expect(screen.getByTestId("run-status").textContent).toContain("Running"),
  );

  const posts = postsTo(mock, "/api/control");
  expect(posts.map((p) => p.command)).toEqual(["step", "speed", "start"]);
  expect(posts[1].ticks_per_second).toBe(20);
  expect(posts[2].until_tick).toBe(100);
  expect(new Set(posts.map((p) => p.command_id)).size).toBe(3);
  expect(posts.every((p) => /^[A-Za-z0-9_-]{8,64}$/.test(p.command_id))).toBe(
    true,
  );
  // While running: Pause is offered and Step is unavailable.
  expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "Step" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it("labels Resume after the run has started and shows backend refusals", async () => {
  const state = backend({ frame: makeFrame({ started: true, tick: 12 }) });
  state.controlResponse = () => ({
    status: 409,
    detail: "The run is already running",
  });
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);
  fireEvent.click(screen.getByRole("button", { name: "Resume" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "already running",
  );
});

it("blocks an invalid pause-at tick before sending anything", async () => {
  const mock = stubBackend(
    backend({ frame: makeFrame({ tick: 12, started: true }) }),
  );
  vi.stubGlobal("fetch", mock);
  render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);
  fireEvent.change(screen.getByLabelText("Pause at tick"), {
    target: { value: "5" },
  });
  expect(
    (screen.getByRole("button", { name: "Resume" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(postsTo(mock, "/api/control")).toHaveLength(0);
});

it("lists events and an inflow event identifies rule, credited energy and changed cells", async () => {
  const frame = makeFrame({
    tick: 10,
    last_event_sequence: 2,
    changed_cells: [1, 69, 1023],
  });
  vi.stubGlobal(
    "fetch",
    stubBackend({
      frame,
      events: [inflowEvent, createdEvent],
      detail: inflowDetail,
    }),
  );
  render(<App />);
  openDashboard();
  const list = await screen.findByRole("list", { name: "Events" });
  await waitFor(() =>
    expect(within(list).getAllByRole("button")).toHaveLength(2),
  );
  const items = within(list).getAllByRole("button");
  expect(items[0].textContent).toContain("env.energy_inflow.v1");
  expect(items[0].textContent).toContain("+3 energy in 3 cells");

  fireEvent.click(items[0]);
  expect((await screen.findByTestId("event-rule")).textContent).toBe(
    "env.energy_inflow.v1",
  );
  expect(screen.getByTestId("event-credited_total").textContent).toBe("3");
  expect(screen.getByTestId("event-blocked_total").textContent).toBe("1021");
  const table = screen.getByRole("region", { name: "Changed cells" });
  expect(within(table).getAllByRole("row")).toHaveLength(4); // header + 3 changes
  expect(within(table).getByText("(31, 31)")).toBeTruthy();

  // Cross-navigation: a changed cell opens in the inspector with its before/after history.
  fireEvent.click(within(table).getByText("(5, 2)"));
  await waitFor(() =>
    expect(screen.getByTestId("selected-cell").textContent).toBe("05,02"),
  );
  expect(await screen.findByText(/tick 10: energy 20 → 21/)).toBeTruthy();
});

it("shows a technical ledger mismatch as a failure, not as ecology", async () => {
  const bad = makeFrame();
  bad.ledger = { ...bad.ledger, balanced: false };
  vi.stubGlobal("fetch", stubBackend(backend({ frame: bad })));
  render(<App />);
  openDashboard();
  expect((await screen.findByTestId("ledger-status")).textContent).toContain(
    "technical failure",
  );
});

it("shows a technical pause reason from the backend", async () => {
  vi.stubGlobal(
    "fetch",
    stubBackend(
      backend({
        frame: makeFrame({ last_error: "OperationalError: disk I/O error" }),
      }),
    ),
  );
  render(<App />);
  openDashboard();
  expect((await screen.findByRole("alert")).textContent).toContain(
    "disk I/O error",
  );
});

it("clears a used pause-at target so it cannot block the next Resume", async () => {
  const state = backend({ frame: makeFrame({ started: true, tick: 50 }) });
  state.controlResponse = (body) =>
    body.command === "resume"
      ? makeFrame({
          started: true,
          tick: 50,
          status: "running",
          until_tick: 100,
        })
      : makeFrame();
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);
  fireEvent.change(screen.getByLabelText("Pause at tick"), {
    target: { value: "100" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Resume" }));
  await waitFor(() =>
    expect(screen.getByTestId("run-status").textContent).toContain("Running"),
  );
  expect(
    (screen.getByLabelText("Pause at tick") as HTMLInputElement).value,
  ).toBe("100");
  // The backend auto-pauses at the target.
  state.frame = makeFrame({
    started: true,
    tick: 100,
    state_hash: "c".repeat(64),
  });
  await waitFor(
    () => expect(screen.getByTestId("tick").textContent).toBe("000100"),
    {
      timeout: 3000,
    },
  );
  expect(
    (screen.getByLabelText("Pause at tick") as HTMLInputElement).value,
  ).toBe("");
  expect(
    (screen.getByRole("button", { name: "Resume" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
});

it("history mode shows a read-only reconstruction and disables every live mutation", async () => {
  const live = makeFrame({ tick: 150, started: true, last_event_sequence: 16 });
  const state = backend({
    frame: live,
    events: [inflowEvent, createdEvent],
    snapshots: [
      { tick: 150, reason: "pause", created_at: "", checksum_ok: true },
      { tick: 100, reason: "periodic", created_at: "", checksum_ok: true },
      { tick: 0, reason: "initial", created_at: "", checksum_ok: true },
    ],
  });
  const requested: string[] = [];
  state.history = (tick, source) => {
    requested.push(`${tick}:${source}`);
    return makeHistory(tick, {
      source: {
        kind: source === "initial" ? "initial" : "snapshot",
        snapshot_tick: source === "initial" ? null : 0,
        ticks_replayed: tick,
        events_applied: 5,
        skipped_snapshots: [],
      },
    });
  };
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);
  expect((await screen.findByTestId("snapshot-list")).textContent).toContain(
    "0 (initial), 100 (periodic), 150 (pause)",
  );

  fireEvent.change(screen.getByLabelText("History tick"), {
    target: { value: "50" },
  });
  fireEvent.click(screen.getByRole("button", { name: "View tick" }));
  expect((await screen.findByTestId("history-banner")).textContent).toContain(
    "tick 50",
  );
  expect(screen.getByTestId("tick").textContent).toBe("000050");
  expect(screen.getByTestId("history-rebuilt").textContent).toContain(
    "snapshot @ tick 0",
  );
  expect(screen.getByTestId("history-verified").textContent).toContain(
    "matches",
  );
  // Inspector shows the historical value, not the live one.
  expect(screen.getByTestId("cell-energy").textContent).toBe(
    `${Math.max(0, live.cells[0].energy - 1)} / 100`,
  );
  for (const name of ["Resume", "Step"])
    expect(
      (screen.getByRole("button", { name }) as HTMLButtonElement).disabled,
    ).toBe(true);
  expect((screen.getByLabelText("Speed") as HTMLSelectElement).disabled).toBe(
    true,
  );
  expect(
    (screen.getByRole("button", { name: /New run/ }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await waitFor(() => {
    const urls = mock.mock.calls.map(([url]) => url as string);
    expect(
      urls.some((u) => u.includes("/api/events?") && u.includes("max_tick=50")),
    ).toBe(true);
    expect(urls.some((u) => u.includes("/api/cells/0/0?at_tick=50"))).toBe(
      true,
    );
  });
  expect(postsTo(mock, "/api/control")).toHaveLength(0);

  fireEvent.change(screen.getByLabelText("Rebuild from"), {
    target: { value: "initial" },
  });
  fireEvent.click(screen.getByRole("button", { name: "View tick" }));
  await waitFor(() =>
    expect(screen.getByTestId("history-rebuilt").textContent).toContain(
      "initial state",
    ),
  );
  expect(requested).toEqual(["50:snapshot", "50:initial"]);

  fireEvent.click(screen.getAllByRole("button", { name: "Return to live" })[0]);
  expect(screen.queryByTestId("history-banner")).toBeNull();
  expect(screen.getByTestId("tick").textContent).toBe("000150");
  expect(
    (screen.getByRole("button", { name: "Step" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
});

it("an unavailable historical tick is reported, not invented", async () => {
  const state = backend({ frame: makeFrame({ tick: 20 }) });
  state.history = () => ({
    status: 409,
    detail: "Historical data unavailable: gap",
  });
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);
  fireEvent.change(screen.getByLabelText("History tick"), {
    target: { value: "5" },
  });
  fireEvent.click(screen.getByRole("button", { name: "View tick" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Historical data unavailable",
  );
  expect(screen.queryByTestId("history-banner")).toBeNull();
  // Ticks beyond the live tick cannot be requested at all.
  fireEvent.change(screen.getByLabelText("History tick"), {
    target: { value: "21" },
  });
  expect(
    (screen.getByRole("button", { name: "View tick" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it("shows skipped corrupt snapshots and the startup restore source", async () => {
  const state = backend({
    frame: makeFrame({
      tick: 230,
      restored_from: {
        kind: "snapshot",
        snapshot_tick: 100,
        ticks_replayed: 130,
        events_applied: 13,
        skipped_snapshots: [{ tick: 200, reason: "checksum mismatch" }],
      },
    }),
  });
  state.history = (tick) =>
    makeHistory(tick, {
      source: {
        kind: "snapshot",
        snapshot_tick: 100,
        ticks_replayed: tick - 100,
        events_applied: 3,
        skipped_snapshots: [{ tick: 200, reason: "checksum mismatch" }],
      },
    });
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  expect((await screen.findByTestId("restored-from")).textContent).toContain(
    "snapshot @ tick 100 + 130 replayed ticks",
  );
  fireEvent.change(screen.getByLabelText("History tick"), {
    target: { value: "210" },
  });
  fireEvent.click(screen.getByRole("button", { name: "View tick" }));
  expect(await screen.findByText("checksum mismatch")).toBeTruthy();
});

it("updates restore metadata after a backend restart even when the state is identical", async () => {
  let poll: (() => void) | undefined;
  const originalInterval = window.setInterval.bind(window);
  vi.spyOn(window, "setInterval").mockImplementation((callback, delay) => {
    if (delay === 1000) {
      poll = callback as () => void;
      return 99;
    }
    return originalInterval(callback, delay);
  });
  const source = (ticks: number) => ({
    kind: "snapshot" as const,
    snapshot_tick: 150,
    ticks_replayed: ticks,
    events_applied: 0,
    skipped_snapshots: [],
  });
  const state = backend({
    frame: makeFrame({ tick: 157, restored_from: source(0) }),
  });
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  expect((await screen.findByTestId("restored-from")).textContent).toContain(
    "+ 0 replayed",
  );
  state.frame = makeFrame({ tick: 157, restored_from: source(7) });
  await act(async () => {
    poll!();
  });
  await waitFor(() =>
    expect(screen.getByTestId("restored-from").textContent).toContain(
      "+ 7 replayed",
    ),
  );
});

it("shows organisms, the supplied observation and its local view, with lifespan hidden", async () => {
  const organism = makeOrganism();
  const state = backend({
    frame: makeFrame({
      tick: 3,
      organisms: [organism],
      config: { ...fixture.config, fixture: "corner_perception" },
    }),
  });
  state.organism = () => makeOrganismDetail(organism);
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  expect((await screen.findByTestId("organism-energy")).textContent).toBe(
    "57 / 100",
  );
  expect(screen.getByTestId("organism-lifespan").textContent).toBe(
    "hidden while alive",
  );
  expect(screen.getByTestId("organism-position").textContent).toBe("(1, 0)");
  expect(screen.getByText(/scripted, not autonomous/)).toBeTruthy();
  expect(screen.getByTestId("organism-outcome").textContent).toContain(
    "Moved east",
  );
  const table = screen.getByRole("region", { name: "Supplied observation" });
  expect(within(table).getAllByRole("row")).toHaveLength(4);
  expect(screen.getByTestId("observation-summary").textContent).toContain(
    "3 cells within radius 2",
  );
  expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
});

it("reveals lifespan and death details only after death and shows extinction as archived", async () => {
  const dead = makeOrganism({
    alive: false,
    energy: 0,
    death_tick: 24,
    death_cause: "age",
    retired_energy: 77,
    lifespan: 24,
    age: 24,
  });
  const state = backend({
    frame: makeFrame({
      tick: 24,
      organisms: [dead],
      extinct: true,
      started: true,
    }),
  });
  state.organism = () => makeOrganismDetail(dead);
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  openDashboard();
  expect((await screen.findByTestId("extinct-banner")).textContent).toContain(
    "archived",
  );
  expect((await screen.findByTestId("organism-lifespan")).textContent).toBe(
    "24 ticks",
  );
  expect(screen.getByTestId("organism-status").textContent).toBe("dead (age)");
  for (const name of ["Resume", "Step"])
    expect(
      (screen.getByRole("button", { name }) as HTMLButtonElement).disabled,
    ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "View tick" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
  expect(postsTo(mock, "/api/control")).toHaveLength(0);
});

it("describes organism events in the event list", async () => {
  const events: EventSummary[] = [
    {
      sequence: 5,
      tick: 3,
      phase: "lifecycle",
      type: "organism_died",
      rule_id: "life.starvation.v1",
      cause: "lifecycle",
      actor_id: "org-0001",
      summary: { cause: "starvation", age: 3, retired_energy: 0 },
    },
    {
      sequence: 4,
      tick: 3,
      phase: "diagnostic",
      type: "action_rejected",
      rule_id: "action.validation.v1",
      cause: "organism",
      actor_id: "org-0001",
      summary: { reason: "out_of_bounds", action: "move" },
    },
    {
      sequence: 3,
      tick: 3,
      phase: "lifecycle",
      type: "upkeep",
      rule_id: "life.upkeep.v1",
      cause: "lifecycle",
      actor_id: null,
      summary: { charged_total: 1, organisms: 1, per_organism: 1 },
    },
  ];
  vi.stubGlobal(
    "fetch",
    stubBackend(
      backend({ frame: makeFrame({ last_event_sequence: 5 }), events }),
    ),
  );
  render(<App />);
  openDashboard();
  const list = await screen.findByRole("list", { name: "Events" });
  await waitFor(() =>
    expect(within(list).getAllByRole("button")).toHaveLength(3),
  );
  const text = list.textContent ?? "";
  expect(text).toContain("org-0001 died (starvation) at age 3");
  expect(text).toContain("org-0001 proposal rejected: out_of_bounds");
  expect(text).toContain("Upkeep: −1 energy from 1 organism(s)");
});

it("shows carried material, the engine cost preview and cell entry cost with its maker", async () => {
  const organism = makeOrganism();
  const cells = makeFrame().cells.map((c, i) =>
    i === 0 ? { ...c, packed_material: 4 } : c,
  );
  const state = backend({
    frame: makeFrame({ tick: 3, organisms: [organism], cells }),
  });
  state.organism = () => makeOrganismDetail(organism);
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  expect((await screen.findByTestId("organism-carried")).textContent).toBe(
    "4 / 20",
  );
  const preview = screen.getByRole("region", { name: "Cost preview" });
  const rows = within(preview)
    .getAllByRole("row")
    .map((r) =>
      Array.from(r.querySelectorAll("td"))
        .map((c) => c.textContent)
        .join("|"),
    );
  expect(rows).toContain("move west|valid|−3");
  expect(rows).toContain("move north|out_of_bounds|—");
  expect(rows).toContain("modify pack 1|valid|−2");
  await waitFor(() =>
    expect(screen.getByTestId("cell-entry-cost").textContent).toContain("3"),
  );
  expect(screen.getByTestId("cell-packed").textContent).toBe("4 / 8");
});

it("shows pending work at the decision barrier separately from the committed tick", async () => {
  const frame = makeFrame({
    tick: 4,
    started: true,
    provider: recordedProvider,
    pending: {
      tick: 5,
      total: 1,
      completed: 1,
      failed: 0,
      waiting: [],
      seconds: 2.1,
    },
  });
  vi.stubGlobal("fetch", stubBackend(backend({ frame })));
  render(<App />);
  openDashboard();
  const note = await screen.findByTestId("pending");
  expect(note.textContent).toContain(
    "Tick 5 waiting at the decision barrier: 1 / 1 responses",
  );
  expect(note.textContent).toContain(
    "nothing commits until you resume or step",
  );
  expect(screen.getByTestId("tick").textContent).toBe("000004");
});

it("decision inspector shows raw output as text, parse result and engine outcome", async () => {
  const organism = makeOrganism();
  const malicious = makeDecision({
    request_id: "t00000005-org-0001",
    tick: 5,
    raw_output: '<img src=x onerror="window.__pwned=1">',
    parse_status: "malformed_json",
    event_sequence: 9,
  });
  const valid = makeDecision({
    raw_output: '{"action": "wait"}',
    parse_status: "valid",
    parse_error: null,
    proposal: { action: "wait" },
    outcome: "accepted",
    outcome_rule: "action.wait.v1",
    outcome_reason: null,
    request: {
      request_id: "t00000001-org-0001",
      target_tick: 1,
      schema_version: "genesis.action.v1",
      input_hash: "c".repeat(64),
      context: {
        observation:
          makeOrganismDetail(organism).latest_decision!.payload.observation!,
        previous_result: null,
        action_schema: {},
        response_format: "one JSON object",
      },
    },
  });
  const state = backend({
    frame: makeFrame({
      tick: 5,
      organisms: [organism],
      provider: recordedProvider,
    }),
    decisions: [malicious, valid],
  });
  state.organism = () => makeOrganismDetail(organism);
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  expect((await screen.findByTestId("provider-tag")).textContent).toContain(
    "not a live model",
  );
  const raw = await screen.findByTestId("raw-output");
  expect(raw.textContent).toBe('<img src=x onerror="window.__pwned=1">');
  expect(raw.querySelector("img")).toBeNull(); // rendered as text, never as HTML
  expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  expect(screen.getByTestId("decision-outcome").textContent).toContain(
    "unparsed: malformed_json",
  );
  const list = screen.getByRole("list", { name: "Decision attempts" });
  fireEvent.click(within(list).getAllByRole("button")[1]);
  await waitFor(() =>
    expect(screen.getByTestId("parsed-proposal").textContent).toBe(
      '{"action":"wait"}',
    ),
  );
  expect(screen.getByTestId("decision-outcome").textContent).toContain(
    "accepted",
  );
  expect(screen.getByTestId("decision-detail").textContent).toContain(
    "3 cells, energy 58",
  );
  expect(screen.getByTestId("decision-detail").textContent).toContain(
    "Tokens in/out: unknown",
  );
});

it("updates pending progress while paused even though the committed state is unchanged", async () => {
  let poll: (() => void) | undefined;
  const originalInterval = window.setInterval.bind(window);
  vi.spyOn(window, "setInterval").mockImplementation((callback, delay) => {
    if (delay === 1000) {
      poll = callback as () => void;
      return 99;
    }
    return originalInterval(callback, delay);
  });
  const pending = (completed: number) => ({
    tick: 5,
    total: 1,
    completed,
    failed: 0,
    waiting: completed ? [] : ["org-0001"],
    seconds: 1,
  });
  const state = backend({
    frame: makeFrame({
      tick: 4,
      provider: recordedProvider,
      pending: pending(0),
    }),
  });
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  expect((await screen.findByTestId("pending")).textContent).toContain(
    "0 / 1 responses",
  );
  state.frame = makeFrame({
    tick: 4,
    provider: recordedProvider,
    pending: pending(1),
  });
  await act(async () => {
    poll!();
  });
  await waitFor(() =>
    expect(screen.getByTestId("pending").textContent).toContain(
      "1 / 1 responses",
    ),
  );
});

const liveProvider = {
  ...recordedProvider,
  kind: "mlx",
  name: "Qwen3.8-27B-Uncensored-MLX/8-bit",
  health: {
    status: "ready",
    pid: 4242,
    load_seconds: 2.4,
    memory_gb: 28.6,
    requests: 3,
    failures: 0,
    restarts: 0,
    last_latency_ms: 4900,
    runtime: { mlx: "0.32.0", mlx_lm: "0.31.3" },
  },
};

it("creates a live local-model run from the cognition selector", async () => {
  const models = {
    runtime: { available: true, mlx: "0.32.0", mlx_lm: "0.31.3" },
    model_dirs: ["/Users/x/models"],
    models: [
      {
        id: "Qwen3.8-27B-Uncensored-MLX/8-bit",
        path: "/p",
        model_type: "qwen3_5",
        size_gb: 29.5,
      },
    ],
  };
  const state = backend({ models });
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  openDashboard();
  await screen.findByText(fixture.run_id);
  const select = (await screen.findByLabelText("Model")) as HTMLSelectElement;
  expect(select.textContent).toContain(
    "local MLX-LM · Qwen3.8-27B-Uncensored-MLX/8-bit (29.5 GB)",
  );
  fireEvent.change(select, {
    target: { value: "mlx:Qwen3.8-27B-Uncensored-MLX/8-bit" },
  });
  state.frame = makeFrame({
    run_id: "00000000-0000-4000-8000-000000000009",
    provider: liveProvider,
    organisms: [makeOrganism({ label: "LIVE MODEL", policy: "live" })],
  });
  fireEvent.click(screen.getByRole("button", { name: /New run/ }));
  expect((await screen.findByTestId("provider-tag")).textContent).toContain(
    "local MLX-LM · Qwen3.8-27B-Uncensored-MLX/8-bit — live model, nondeterministic",
  );
  expect(postsTo(mock, "/api/runs")).toEqual([
    {
      seed: 42,
      cognition: "mlx:Qwen3.8-27B-Uncensored-MLX/8-bit",
      organisms: 1,
    },
  ]);
  expect(screen.getByTestId("worker-health").textContent).toContain(
    "Inference worker: ready · pid 4242 · load 2.4s · last decision 4.9s · 28.6 GB · 3 requests, 0 failures",
  );
});

it("explains an unavailable MLX runtime instead of offering a broken choice", async () => {
  const models = {
    runtime: { available: false, reason: "GENESIS_MLX_PYTHON is not set" },
    model_dirs: [],
    models: [{ id: "m", path: "/p", model_type: "x", size_gb: 1 }],
  };
  vi.stubGlobal("fetch", stubBackend(backend({ models })));
  render(<App />);
  openDashboard();
  expect((await screen.findByTestId("mlx-unavailable")).textContent).toContain(
    "GENESIS_MLX_PYTHON is not set",
  );
  const options = (screen.getByLabelText("Model") as HTMLSelectElement).options;
  expect(options[1].disabled).toBe(true); // the model cannot be chosen without a runtime
});

it("shows the exact prompt and inference settings of a live decision", async () => {
  const organism = makeOrganism({ label: "LIVE MODEL" });
  const decision = makeDecision({
    raw_output: '{"action":"wait"}',
    parse_status: "valid",
    outcome: "accepted",
    outcome_rule: "action.wait.v1",
    prompt: [
      {
        role: "system",
        content: "You are one organism living in a shared grid world.",
      },
      { role: "user", content: '{"observation":{"age":1}}' },
    ],
    detail: {
      prompt_version: "decision-v1",
      sampling: { temperature: 0.7, seed: 123 },
      limits: { max_output_tokens: 256 },
      runtime: { mlx_lm: "0.31.3" },
    },
    usage: {
      input_tokens: 980,
      output_tokens: 65,
      latency_ms: 4900,
      finish_reason: "stop",
    },
  });
  const state = backend({
    frame: makeFrame({
      tick: 1,
      organisms: [organism],
      provider: liveProvider,
    }),
    decisions: [decision],
  });
  state.organism = () => makeOrganismDetail(organism);
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  expect(
    (await screen.findByText(/Exact prompt sent \(decision-v1\)/)).tagName,
  ).toBe("SUMMARY");
  expect(
    screen.getByText("You are one organism living in a shared grid world."),
  ).toBeTruthy();
  expect(screen.getByTestId("inference-settings").textContent).toContain(
    '"seed": 123',
  );
  expect(screen.getByTestId("decision-detail").textContent).toContain(
    "Tokens in/out: 980 / 65",
  );
  expect(
    screen.getByText(/decisions from a live model \(nondeterministic\)/),
  ).toBeTruthy();
});

it("keeps every inspector out of the way until the Dashboard is opened", async () => {
  vi.stubGlobal("fetch", stubBackend(backend()));
  render(<App />);
  await screen.findByText(fixture.run_id);
  expect(screen.queryByRole("complementary", { name: "Dashboard" })).toBeNull();
  expect(screen.queryByTestId("ledger-status")).toBeNull();
  expect(screen.getByRole("img")).toBeTruthy(); // the grid is always visible
  const toggle = screen.getByRole("button", { name: "Dashboard" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(screen.getByRole("complementary", { name: "Dashboard" })).toBeTruthy();
  expect(screen.getByTestId("ledger-status")).toBeTruthy();
  expect(screen.getByLabelText("Cell x")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByTestId("ledger-status")).toBeNull();
});

it("creates a multi-organism run with the chosen model and count", async () => {
  const models = {
    runtime: { available: true },
    model_dirs: [],
    models: [{ id: "qwen", path: "/p", model_type: "x", size_gb: 29.5 }],
  };
  const state = backend({ models });
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  await screen.findByText(fixture.run_id);
  const count = screen.getByLabelText("Organisms") as HTMLInputElement;
  expect(count.disabled).toBe(true); // no model selected: environment only
  fireEvent.change(await screen.findByLabelText("Model"), {
    target: { value: "mlx:qwen" },
  });
  fireEvent.change(screen.getByLabelText("Organisms"), {
    target: { value: "13" },
  });
  expect(
    (screen.getByRole("button", { name: /New run/ }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.change(screen.getByLabelText("Organisms"), {
    target: { value: "5" },
  });
  fireEvent.change(screen.getByLabelText("World seed"), {
    target: { value: "7" },
  });
  fireEvent.click(screen.getByRole("button", { name: /New run/ }));
  await waitFor(() =>
    expect(postsTo(mock, "/api/runs")).toEqual([
      { seed: 7, cognition: "mlx:qwen", organisms: 5 },
    ]),
  );
});

it("shows organism details on hover over the board", async () => {
  const organism = makeOrganism({ x: 0, y: 0, energy: 41 });
  const state = backend({
    frame: makeFrame({ tick: 3, organisms: [organism] }),
  });
  state.organism = () => makeOrganismDetail(organism);
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  await screen.findByText(fixture.run_id);
  const canvas = screen.getByRole("img");
  vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    width: 320,
    height: 320,
  } as DOMRect);
  fireEvent.pointerMove(canvas, { clientX: 5, clientY: 5 });
  const tip = await screen.findByTestId("organism-tooltip");
  expect(tip.textContent).toContain("org-0001");
  expect(tip.textContent).toContain("41 / 100");
  await waitFor(() => expect(tip.textContent).toContain("Last: moved east"));
  fireEvent.pointerMove(canvas, { clientX: 300, clientY: 300 }); // empty cell
  expect(screen.queryByTestId("organism-tooltip")).toBeNull();
});

it("shows the run summary automatically when the population goes extinct", async () => {
  const dead = makeOrganism({
    alive: false,
    energy: 0,
    death_tick: 54,
    death_cause: "starvation",
  });
  const state = backend({
    frame: makeFrame({
      tick: 54,
      organisms: [dead],
      extinct: true,
      report_path: "/tmp/r.md",
    }),
    summary: "# Genesis run summary\n\nThe population went extinct at tick 54.",
  });
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  const text = await screen.findByTestId("summary-text");
  expect(text.textContent).toContain("The population went extinct at tick 54.");
  expect(screen.getByTestId("summary-path").textContent).toContain("/tmp/r.md");
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByTestId("summary-text")).toBeNull();
});

it("writes a summary on demand from the control bar", async () => {
  const state = backend({ summary: "# Summary\n\nStopped at tick 3." });
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  await screen.findByText(fixture.run_id);
  fireEvent.click(screen.getByRole("button", { name: "Summary" }));
  expect((await screen.findByTestId("summary-text")).textContent).toContain(
    "Stopped at tick 3.",
  );
  const posts = mock.mock.calls.filter(
    ([url, init]) => url === "/api/summary" && init?.method === "POST",
  );
  expect(posts).toHaveLength(1);
});

it("enters fullscreen on the stage", async () => {
  vi.stubGlobal("fetch", stubBackend(backend()));
  const request = vi.fn().mockResolvedValue(undefined);
  HTMLElement.prototype.requestFullscreen = request;
  render(<App />);
  await screen.findByText(fixture.run_id);
  fireEvent.click(screen.getByRole("button", { name: "Fullscreen" }));
  expect(request).toHaveBeenCalledTimes(1);
  expect(request.mock.contexts[0]).toBe(
    screen.getByRole("region", { name: "World view" }),
  );
});

const apiModels = {
  runtime: { available: false, reason: "GENESIS_MLX_PYTHON is not set" },
  model_dirs: [],
  models: [],
  api: {
    file: "config/providers.toml",
    problems: [],
    profiles: [
      {
        id: "local-vllm",
        protocol: "openai_chat",
        label: "vllm-mlx on this Mac",
        host: "127.0.0.1:8090",
        model: "qwen-served",
        remote: false,
        key_status: "not needed",
        api_key_env: null,
        available: true,
        reason: null,
      },
      {
        id: "replicate",
        protocol: "replicate",
        label: "Replicate",
        host: "api.replicate.com",
        model: "owner/text-model",
        remote: true,
        key_status: "missing",
        api_key_env: "REPLICATE_API_TOKEN",
        available: false,
        reason: "REPLICATE_API_TOKEN is not set",
      },
    ],
  },
};

const apiProvider = {
  ...recordedProvider,
  kind: "api",
  name: "replicate",
  model: "owner/text-model",
  health: {
    status: "idle",
    pid: null,
    load_seconds: null,
    memory_gb: null,
    requests: 4,
    failures: 1,
    last_latency_ms: 2300,
    endpoint: "api.replicate.com",
    remote: true,
    last_error_kind: "rate_limited",
    budget: {
      requests: 4,
      max_requests: 2000,
      tokens: 600,
      max_total_tokens: 4000000,
    },
  },
};

it("offers API connections without MLX and creates an API run", async () => {
  const state = backend({ models: apiModels });
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  await screen.findByText(fixture.run_id);
  const select = (await screen.findByLabelText("Model")) as HTMLSelectElement;
  await waitFor(() =>
    expect(select.textContent).toContain(
      "API · vllm-mlx on this Mac · qwen-served",
    ),
  );
  const replicate = [...select.options].find(
    (o) => o.value === "api:replicate",
  )!;
  expect(replicate.disabled).toBe(true); // no key in the backend's environment
  expect(replicate.textContent).toContain("REPLICATE_API_TOKEN is not set");
  expect(document.body.textContent).not.toContain("sk-"); // keys never reach the page
  fireEvent.change(select, { target: { value: "api:local-vllm" } });
  fireEvent.click(screen.getByRole("button", { name: "Check connection" }));
  expect((await screen.findByTestId("provider-check")).textContent).toContain(
    "Connected; fake-chat is served",
  );
  expect(
    mock.mock.calls.filter(
      ([url, init]) =>
        url === "/api/providers/local-vllm/check" && init?.method === "POST",
    ),
  ).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("Organisms"), {
    target: { value: "2" },
  });
  state.frame = makeFrame({
    run_id: "00000000-0000-4000-8000-00000000000a",
    provider: apiProvider,
    organisms: [makeOrganism({ label: "LIVE MODEL", policy: "live" })],
  });
  fireEvent.click(screen.getByRole("button", { name: /New run/ }));
  expect((await screen.findByTestId("provider-tag")).textContent).toContain(
    "REMOTE API · api.replicate.com · owner/text-model — live model, nondeterministic",
  );
  expect(postsTo(mock, "/api/runs")).toEqual([
    { seed: 42, cognition: "api:local-vllm", organisms: 2 },
  ]);
  expect(screen.getByTestId("worker-health").textContent).toContain(
    "API connection: idle · api.replicate.com · last decision 2.3s · 4 requests, 1 failures (last: rate_limited) · budget 4/2000 requests",
  );
});

it("creates a mixed-model run and labels each organism's own model", async () => {
  const models = {
    ...apiModels,
    runtime: { available: true },
    models: [{ id: "qwen", path: "/p", model_type: "x", size_gb: 29.5 }],
  };
  const state = backend({ models });
  const mock = stubBackend(state);
  vi.stubGlobal("fetch", mock);
  render(<App />);
  await screen.findByText(fixture.run_id);
  expect(screen.queryByLabelText("Second model")).toBeNull(); // only once a model is chosen
  fireEvent.change(await screen.findByLabelText("Model"), {
    target: { value: "mlx:qwen" },
  });
  const second = (await screen.findByLabelText(
    "Second model",
  )) as HTMLSelectElement;
  expect(
    [...second.options].find((o) => o.value === "mlx:qwen")!.disabled,
  ).toBe(true); // the first model cannot be chosen twice
  fireEvent.change(second, { target: { value: "api:local-vllm" } });
  expect(screen.getByTestId("mixed-note").textContent).toContain(
    "Mixed-model experiment",
  );
  const members = {
    "org-0001": { ...liveProvider, model: "/p", revision: null },
    "org-0002": {
      ...apiProvider,
      health: { ...apiProvider.health, remote: true },
    },
  };
  state.frame = makeFrame({
    run_id: "00000000-0000-4000-8000-00000000000b",
    provider: {
      ...recordedProvider,
      kind: "mixed",
      name: "qwen + replicate",
      members,
    },
    organisms: [
      makeOrganism({ id: "org-0001", label: "LIVE MODEL", policy: "live" }),
      makeOrganism({
        id: "org-0002",
        label: "LIVE MODEL",
        policy: "live",
        x: 5,
        y: 5,
      }),
    ],
  });
  fireEvent.click(screen.getByRole("button", { name: /New run/ }));
  expect((await screen.findByTestId("provider-tag")).textContent).toContain(
    "MIXED MODELS (experiment) · org-0001: local MLX-LM · Qwen3.8-27B-Uncensored-MLX/8-bit · org-0002: remote API · owner/text-model",
  );
  expect(postsTo(mock, "/api/runs")).toEqual([
    {
      seed: 42,
      population: [
        { cognition: "mlx:qwen", organisms: 1 },
        { cognition: "api:local-vllm", organisms: 1 },
      ],
    },
  ]);
  expect(screen.getByTestId("worker-health").textContent).toContain(
    "org-0002: remote API · owner/text-model — idle",
  );
});

it("shows active and forgotten memory, recall scores and the recalled records given", async () => {
  const organism = makeOrganism();
  const record = (n: number, extra: Record<string, unknown> = {}) => ({
    id: `org-0001-m0000${n}`,
    owner_id: "org-0001",
    kind: "observation",
    tick: n,
    age: n,
    x: 10,
    y: 10,
    text: `cell: energy ${n}, loose material 25, packed material 0`,
    keywords: ["observation", "energy", "material"],
    salience: 1,
    bytes: 52,
    added_sequence: 20 + n,
    ...extra,
  });
  const detail = {
    ...makeOrganismDetail(organism),
    memory: {
      limits: {
        max_records: 64,
        max_bytes: 16384,
        record_max_bytes: 256,
        write_fee_per_kib: 1,
        upkeep_per_kib: 1,
        upkeep_interval: 10,
        recall_max: 8,
        recall_max_bytes: 2048,
      },
      active_records: 2,
      active_bytes: 104,
      written_total: 3,
      active: [record(3), record(2)],
      forgotten: [record(1, { forgotten_tick: 3, forgotten_sequence: 30 })],
      last_recall: {
        tick: 3,
        recall: {
          version: "recall-v1",
          query: ["energy", "move"],
          candidates: 2,
          selected_ids: ["org-0001-m00003"],
          scores: [
            {
              id: "org-0001-m00003",
              score: 2.5,
              recency: 1,
              relevance: 1,
              salience: 0.5,
              selected: true,
            },
            {
              id: "org-0001-m00002",
              score: 1.25,
              recency: 0,
              relevance: 0.75,
              salience: 0.5,
              selected: false,
            },
          ],
        },
        recalled: null,
      },
    },
  };
  const decision = makeDecision({
    raw_output: '{"action": "wait"}',
    parse_status: "valid",
    parse_error: null,
    proposal: { action: "wait" },
    outcome: "accepted",
    outcome_rule: "action.wait.v1",
    outcome_reason: null,
    request: {
      request_id: "t00000003-org-0001",
      target_tick: 3,
      schema_version: "genesis.action.v1",
      input_hash: "d".repeat(64),
      context: {
        observation: detail.latest_decision!.payload.observation!,
        previous_result: null,
        action_schema: {},
        response_format: "one JSON object",
        memory: {
          rules: "Writing costs 1 energy per started KiB.",
          active_records: 2,
          active_bytes: 104,
          max_records: 64,
          max_bytes: 16384,
        },
        recalled: [
          {
            id: "org-0001-m00003",
            kind: "observation",
            ticks_ago: 2,
            age_then: 1,
            dx: -3,
            dy: 0,
            text: "cell: energy 3, loose material 25, packed material 0",
          },
        ],
      },
    },
  });
  const state = backend({
    frame: makeFrame({
      tick: 5,
      organisms: [organism],
      provider: recordedProvider,
    }),
    decisions: [decision],
  });
  state.organism = () => detail;
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  expect((await screen.findByTestId("memory-usage")).textContent).toContain(
    "2 / 64 records, 104 / 16384 bytes active; 3 written",
  );
  const active = screen.getByRole("region", { name: "Active memory" });
  expect(within(active).getAllByRole("row")).toHaveLength(3);
  expect(active.textContent).toContain("(10, 10)");
  const scores = screen.getByTestId("recall-scores");
  expect(within(scores).getAllByRole("row")).toHaveLength(3);
  expect(scores.textContent).toContain("2.500");
  expect(screen.getByText(/query keywords: energy, move/)).toBeTruthy();
  const forgotten = screen.getByTestId("forgotten-memory");
  expect(forgotten.textContent).toContain("Forgotten records (1)");
  expect(forgotten.textContent).toContain("t3");
  expect((await screen.findByTestId("recalled-count")).textContent).toBe(
    "1 of 2 active",
  );
  expect(screen.getByTestId("recalled-given").textContent).toContain(
    "offset (-3, 0)",
  );
});

it("shows recent exchanges on the stage and an organism's inbox and exchange history", async () => {
  const giver = makeOrganism();
  const receiver = makeOrganism({ id: "org-0002", x: 2, y: 0 });
  const state = backend({
    frame: makeFrame({
      tick: 4,
      organisms: [giver, receiver],
      interactions: [
        {
          kind: "communicate",
          from_id: "org-0001",
          to_id: "org-0002",
          from_x: 1,
          from_y: 0,
          to_x: 2,
          to_y: 0,
          detail: "There is material on my cell.",
        },
        {
          kind: "transfer",
          from_id: "org-0002",
          to_id: "org-0001",
          from_x: 2,
          from_y: 0,
          to_x: 1,
          to_y: 0,
          detail: "5 energy",
        },
      ],
    }),
  });
  state.organism = () => ({
    ...makeOrganismDetail(giver),
    messages: {
      inbox: [
        {
          id: "org-0002-s00001",
          kind: "transfer",
          sender_id: "org-0002",
          recipient_id: "org-0001",
          text: "5 energy",
          sent_tick: 4,
          status: "pending",
          delivered_tick: null,
          state: "pending",
        },
      ],
      inbox_capacity: 8,
      recent: [
        {
          id: "org-0001-s00001",
          kind: "message",
          sender_id: "org-0001",
          recipient_id: "org-0002",
          text: "There is material on my cell.",
          sent_tick: 4,
          status: "pending",
          delivered_tick: null,
          state: "pending",
        },
      ],
    },
  });
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  const feed = await screen.findByRole("list", { name: "Recent exchanges" });
  expect(feed.textContent).toContain(
    "org-0001 → org-0002: “There is material on my cell.”",
  );
  expect(feed.textContent).toContain("org-0002 → org-0001 gave 5 energy");
  openDashboard();
  expect((await screen.findByTestId("inbox-usage")).textContent).toContain(
    "1 item(s), 0 / 8 message slots used",
  );
  expect(screen.getByTestId("message-history").textContent).toContain(
    "to org-0002: “There is material on my cell.”",
  );
  expect(screen.getByTestId("message-history").textContent).toContain(
    "sent, arrives next tick",
  );
});

it("compares tendencies with observed behaviour and shows an organism's profile", async () => {
  const a = makeOrganism();
  const b = makeOrganism({ id: "org-0002", x: 3, y: 3 });
  const tendencies = (affiliation: number) => ({
    maintenance: 70,
    caution: 20,
    curiosity: 55,
    affiliation,
    experimentation: 10,
    transmission: 40,
  });
  const stats = (sent: number) => ({
    decisions: 12,
    accepted: 10,
    rejected: 2,
    unparsed: 0,
    actions: { extract: 7, move: 3 },
    cells_visited: 4,
    energy_extracted: 50,
    material_extracted: 5,
    energy_spent_on_actions: 10,
    gifts_given: 0,
    gifts_received: 0,
    messages_sent: sent,
    messages_received: 0,
    material_packed: 0,
    material_unpacked: 0,
  });
  const state = backend({
    frame: makeFrame({ tick: 12, state_schema: 9, organisms: [a, b] }),
    population: {
      run_id: fixture.run_id,
      tick: 12,
      tendency_names: [
        "maintenance",
        "caution",
        "curiosity",
        "affiliation",
        "experimentation",
        "transmission",
      ],
      tendency_mode: "seeded",
      organisms: [
        {
          id: a.id,
          alive: true,
          death_tick: null,
          tendencies: tendencies(90),
          stats: stats(3),
        },
        {
          id: b.id,
          alive: true,
          death_tick: null,
          tendencies: tendencies(5),
          stats: stats(0),
        },
      ],
    },
  });
  const told = makeOrganismDetail(a);
  told.latest_decision = {
    ...told.latest_decision!,
    rule_id: "action.communicate.v1",
    summary: {
      action: "communicate",
      target: "org-0002",
      text: "Hello.",
      energy_cost: 2,
    },
  };
  state.organism = () => ({
    ...told,
    profile: {
      tendencies: tendencies(90),
      tendency_mode: "seeded",
      stats: stats(3),
    },
  });
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  const table = await screen.findByRole("region", {
    name: "Population comparison",
  });
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(3);
  expect(rows[1].textContent).toContain("10/12");
  expect(rows[1].textContent).toContain("3/0");
  expect(screen.getByText(/TENDENCIES SEEDED · TICK 12/)).toBeTruthy();
  const profile = await screen.findByTestId("profile-panel");
  expect(profile.textContent).toContain("affiliation");
  expect(profile.textContent).toContain("90");
  expect(screen.getByTestId("profile-stats").textContent).toContain(
    "12 decisions: 10 accepted, 2 rejected",
  );
  expect(screen.getByTestId("profile-stats").textContent).toContain(
    "messages 3 sent",
  );
  expect(
    (screen.getByLabelText("Tendencies") as HTMLSelectElement).disabled,
  ).toBe(true);
  expect(screen.getByTestId("organism-outcome").textContent).toContain(
    "Told org-0002: “Hello.” (−2 energy)",
  );
});

it("distinguishes facts, claims and reflections and inspects a reflection request", async () => {
  const organism = makeOrganism();
  const record = (id: string, kind: string, text: string, extra = {}) => ({
    id,
    owner_id: "org-0001",
    kind,
    tick: 9,
    age: 9,
    x: 16,
    y: 16,
    text,
    keywords: [kind],
    salience: 2,
    bytes: text.length,
    added_sequence: 40,
    ...extra,
  });
  const reflected = makeOrganismDetail(organism);
  reflected.latest_decision = {
    ...reflected.latest_decision!,
    rule_id: "cognition.reflect.v1",
    summary: { action: "reflect", count: 1, energy_cost: 2 },
  };
  const detail = {
    ...reflected,
    memory: {
      limits: {
        max_records: 64,
        max_bytes: 16384,
        record_max_bytes: 256,
        write_fee_per_kib: 1,
        upkeep_per_kib: 1,
        upkeep_interval: 10,
        recall_max: 8,
        recall_max_bytes: 2048,
      },
      active_records: 3,
      active_bytes: 90,
      written_total: 13,
      active: [
        record(
          "org-0001-m00013",
          "reflection",
          "I keep walking the same square.",
          {
            evidence: ["org-0001-m00012", "org-0001-m00011"],
          },
        ),
        record("org-0001-m00012", "outcome", "moved east; entry cost 1 energy"),
        record("org-0001-m00011", "message", 'org-0002 said: "hi"'),
      ],
      forgotten: [],
      last_recall: null,
    },
  };
  const decision = makeDecision({
    tick: 9,
    raw_output: '{"reflections":[]}',
    parse_status: "valid",
    parse_error: null,
    proposal: { reflections: [] },
    outcome: "accepted",
    outcome_rule: "cognition.reflect.v1",
    outcome_reason: null,
    request: {
      request_id: "t00000009-org-0001",
      target_tick: 9,
      schema_version: "genesis.reflection.v1",
      input_hash: "e".repeat(64),
      purpose: "reflect",
      context: {
        status: { age: 9, energy: 70 },
        memories: [
          {
            id: "org-0001-m00012",
            kind: "outcome",
            ticks_ago: 1,
            age_then: 8,
            dx: 0,
            dy: 0,
            text: "moved east; entry cost 1 energy",
          },
        ],
        response_format: '{"reflections": []}',
      },
    },
  });
  const state = backend({
    frame: makeFrame({
      tick: 9,
      organisms: [organism],
      provider: recordedProvider,
    }),
    decisions: [decision],
  });
  state.organism = () => detail;
  vi.stubGlobal("fetch", stubBackend(state));
  render(<App />);
  openDashboard();
  const active = await screen.findByRole("region", { name: "Active memory" });
  expect(active.textContent).toContain("interpretation");
  expect(active.textContent).toContain("cites m00012, m00011");
  expect(active.textContent).toContain("claim");
  expect(active.textContent).toContain("fact");
  expect(screen.getByTestId("organism-outcome").textContent).toContain(
    "Reflected: 1 interpretation(s) (−2 energy)",
  );
  expect((await screen.findByTestId("decision-detail")).textContent).toContain(
    "reflection request: 1 of its own records, energy 70",
  );
});
