import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  IncompatibleBackend,
  createRun as requestCreateRun,
  memberLabel,
  requestCell,
  requestOrganism,
  requestEvent,
  requestEvents,
  requestHistory,
  requestModels,
  requestSnapshots,
  requestWorld,
  readSummary,
  sendControl,
  writeSummary,
} from "./api";
import type {
  Cell,
  CellRecentChange,
  ControlCommand,
  EventDetail,
  EventSummary,
  GridFrame,
  HistoryFrame,
  Interaction,
  ModelsInfo,
  OrganismDetail,
  RunSummary,
  SnapshotInfo,
  WorldView,
} from "./api";
import CanvasGrid, { EXCHANGE_FADE } from "./renderers/CanvasGrid";
import ExchangeFeed from "./components/ExchangeFeed";
import PopulationPanel from "./components/PopulationPanel";
import CellInspector from "./components/CellInspector";
import DecisionInspector from "./components/DecisionInspector";
import EventList from "./components/EventList";
import OrganismPanel from "./components/OrganismPanel";
import Legend from "./components/Legend";
import ControlBar from "./components/ControlBar";
import type { NewRunSettings } from "./components/ControlBar";
import Dashboard from "./components/Dashboard";
import OrganismTooltip from "./components/OrganismTooltip";
import SummaryModal from "./components/SummaryModal";
import TickPicker from "./components/TickPicker";

type Connection = "connecting" | "connected" | "disconnected" | "incompatible";

export const POLL_RUNNING_MS = 250;
export const POLL_IDLE_MS = 1000;

function mergeEvents(a: EventSummary[], b: EventSummary[]): EventSummary[] {
  const bySequence = new Map<number, EventSummary>();
  for (const event of [...a, ...b]) bySequence.set(event.sequence, event);
  return [...bySequence.values()].sort((x, y) => y.sequence - x.sequence);
}

function sameView(a: GridFrame, b: GridFrame): boolean {
  return (
    a.run_id === b.run_id &&
    a.state_hash === b.state_hash &&
    a.status === b.status &&
    a.started === b.started &&
    a.speed === b.speed &&
    a.until_tick === b.until_tick &&
    a.last_error === b.last_error &&
    a.last_event_sequence === b.last_event_sequence &&
    // A backend restart can return identical state with different restore metadata.
    JSON.stringify(a.restored_from) === JSON.stringify(b.restored_from) &&
    // Pending work changes (responses arriving) while the committed state does not.
    JSON.stringify(a.pending) === JSON.stringify(b.pending) &&
    a.report_path === b.report_path
  );
}

export default function App() {
  const [frame, setFrame] = useState<GridFrame | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [versionError, setVersionError] = useState("");
  const [selected, setSelected] = useState<Cell>({ x: 0, y: 0 });
  const [settings, setSettings] = useState<NewRunSettings>({
    seed: "42",
    model: "",
    organisms: "1",
    model2: "",
    organisms2: "1",
    fixture: "",
    size: "32",
    tendencies: "seeded",
  });
  const [dashboardOpen, setDashboardOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const stageRef = useRef<HTMLElement>(null);
  const [hovered, setHovered] = useState<{
    id: string;
    x: number;
    y: number;
  } | null>(null);
  const [hoverDetails, setHoverDetails] = useState<
    Record<string, OrganismDetail>
  >({});
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const summaryShownFor = useRef<string | null>(null);
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [controlError, setControlError] = useState("");
  const [events, setEvents] = useState<EventSummary[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<number | null>(null);
  const [detail, setDetail] = useState<EventDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [recent, setRecent] = useState<CellRecentChange[]>([]);
  const [entryCost, setEntryCost] = useState<number | null>(null);
  const [organismId, setOrganismId] = useState<string | null>(null);
  const [organismDetail, setOrganismDetail] = useState<OrganismDetail | null>(
    null,
  );
  const [organismError, setOrganismError] = useState("");
  const [showLocalView, setShowLocalView] = useState(true);
  const [history, setHistory] = useState<HistoryFrame | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [snapshots, setSnapshots] = useState<SnapshotInfo[]>([]);
  const requestSequence = useRef(0);
  const busy = useRef(false);
  const loadedOlder = useRef(false);

  const load = useCallback(async () => {
    if (busy.current) return;
    const sequence = ++requestSequence.current;
    try {
      const next = await requestWorld();
      if (sequence !== requestSequence.current) return;
      setFrame((previous) =>
        previous && sameView(previous, next) ? previous : next,
      );
      setConnection("connected");
    } catch (caught) {
      if (sequence !== requestSequence.current) return;
      if (caught instanceof IncompatibleBackend) {
        setVersionError(caught.message);
        setConnection("incompatible");
      } else setConnection("disconnected");
    }
  }, []);

  useEffect(() => {
    if (connection !== "connected" || models !== null) return;
    requestModels()
      .then(setModels)
      .catch(() =>
        setModels({
          runtime: { available: false },
          model_dirs: [],
          models: [],
        }),
      );
  }, [connection, models]);

  const pollMs = frame?.status === "running" ? POLL_RUNNING_MS : POLL_IDLE_MS;
  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(), pollMs);
    return () => {
      window.clearInterval(interval);
      requestSequence.current++;
    };
  }, [load, pollMs]);

  const runId = frame?.run_id;
  const lastEvent = frame?.last_event_sequence;
  const historyTick = history?.tick;
  const liveStatus = frame?.status;

  // History belongs to one run; a different active run returns to live view.
  useEffect(() => {
    if (history && runId !== undefined && history.run_id !== runId)
      setHistory(null);
  }, [history, runId]);

  useEffect(() => {
    if (runId === undefined) return;
    let cancelled = false;
    requestSnapshots()
      .then((page) => {
        if (!cancelled && page.run_id === runId) setSnapshots(page.snapshots);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [runId, liveStatus, lastEvent, historyTick]);

  // A different run or viewed tick starts with a clean event view.
  useEffect(() => {
    setEvents([]);
    setHasMore(false);
    setSelectedEvent(null);
    setDetail(null);
    setDetailError("");
    setRecent([]);
    loadedOlder.current = false;
  }, [runId, historyTick]);

  useEffect(() => {
    if (runId === undefined) return;
    let cancelled = false;
    requestEvents(undefined, historyTick)
      .then((page) => {
        if (cancelled || page.run_id !== runId) return;
        setEvents((previous) => mergeEvents(previous, page.events));
        if (!loadedOlder.current) setHasMore(page.has_more);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [runId, lastEvent, historyTick]);

  useEffect(() => {
    if (runId === undefined || selectedEvent === null) {
      setDetail(null);
      setDetailError("");
      return;
    }
    let cancelled = false;
    requestEvent(selectedEvent)
      .then((next) => {
        if (cancelled) return;
        setDetail(next);
        setDetailError("");
      })
      .catch(() => {
        if (cancelled) return;
        setDetail(null);
        setDetailError("Could not load this event from the backend.");
      });
    return () => {
      cancelled = true;
    };
  }, [runId, selectedEvent]);

  useEffect(() => {
    if (runId === undefined) return;
    let cancelled = false;
    requestCell(selected, historyTick)
      .then((inspection) => {
        if (!cancelled && inspection.run_id === runId)
          setRecent(inspection.recent_changes);
        setEntryCost(inspection.entry_cost);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [runId, lastEvent, selected, historyTick]);

  // Select the first organism of a run by default; clear the selection on a new run.
  const firstOrganism = frame?.organisms[0]?.id ?? null;
  useEffect(() => {
    setOrganismId(firstOrganism);
  }, [runId, firstOrganism]);

  useEffect(() => {
    if (runId === undefined || organismId === null) {
      setOrganismDetail(null);
      return;
    }
    let cancelled = false;
    requestOrganism(organismId, historyTick)
      .then((next) => {
        if (cancelled || next.run_id !== runId) return;
        setOrganismDetail(next);
        setOrganismError("");
      })
      .catch((caught) => {
        if (cancelled) return;
        setOrganismDetail(null);
        setOrganismError(
          caught instanceof ApiError
            ? caught.message
            : "Could not load this organism from the backend.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [runId, organismId, lastEvent, historyTick]);

  const loadOlder = async () => {
    const oldest = events[events.length - 1];
    if (!oldest) return;
    try {
      const page = await requestEvents(oldest.sequence, historyTick);
      loadedOlder.current = true;
      setEvents((previous) => mergeEvents(previous, page.events));
      setHasMore(page.has_more);
    } catch {
      setConnection("disconnected");
    }
  };

  const runCommand = async (command: ControlCommand) => {
    busy.current = true;
    const sequence = ++requestSequence.current;
    setControlError("");
    try {
      const next = await sendControl(command);
      if (sequence === requestSequence.current) {
        setFrame(next);
        setConnection("connected");
      }
    } catch (caught) {
      if (caught instanceof ApiError) {
        setControlError(caught.message);
      } else {
        setConnection("disconnected");
        setControlError(
          "Could not reach the backend; the command was not confirmed.",
        );
      }
    } finally {
      busy.current = false;
      void load();
    }
  };

  const createRun = async () => {
    const {
      seed,
      fixture,
      model,
      organisms,
      model2,
      organisms2,
      size,
      tendencies,
    } = settings;
    const value = Number(seed);
    if (
      seed === "" ||
      !Number.isInteger(value) ||
      value < 0 ||
      value > 4294967295
    )
      return;
    busy.current = true;
    const sequence = ++requestSequence.current;
    setCreating(true);
    setError("");
    try {
      const next = await requestCreateRun(
        value,
        model ? null : fixture || null,
        model || null,
        Number(organisms),
        model && model2
          ? [
              { cognition: model, organisms: Number(organisms) },
              { cognition: model2, organisms: Number(organisms2) },
            ]
          : null,
        Number(size),
        tendencies,
      );
      if (sequence !== requestSequence.current) return;
      setFrame(next);
      setSelected({ x: 0, y: 0 });
      setConnection("connected");
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.message); // refused by the backend; the active run is unchanged
      } else {
        setConnection("disconnected");
        setError(
          "Could not confirm the new run. Reconnect to check the active run before trying again.",
        );
      }
    } finally {
      busy.current = false;
      setCreating(false);
    }
  };

  const showSummary = async () => {
    setSummaryBusy(true);
    try {
      setSummary(await writeSummary());
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not write the summary.",
      );
    } finally {
      setSummaryBusy(false);
    }
  };

  // When a run ends (extinction) its summary is written by the backend: show it once.
  const extinctReport = frame?.extinct ? frame.report_path : null;
  useEffect(() => {
    if (
      !extinctReport ||
      runId === undefined ||
      summaryShownFor.current === runId
    )
      return;
    summaryShownFor.current = runId;
    readSummary()
      .then((next) => {
        if (next.run_id === runId) setSummary(next);
      })
      .catch(() => undefined);
  }, [extinctReport, runId]);

  // Hover details: one organism request per organism per committed tick (cached).
  const hoverKey = hovered
    ? `${hovered.id}@${frame?.last_event_sequence}`
    : null;
  useEffect(() => {
    if (!hovered || !hoverKey || hoverDetails[hoverKey]) return;
    let cancelled = false;
    requestOrganism(hovered.id)
      .then((next) => {
        if (!cancelled)
          setHoverDetails((prev) => ({ ...prev, [hoverKey]: next }));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [hovered, hoverKey, hoverDetails]);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement !== null);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen?.();
    else void stageRef.current?.requestFullscreen?.();
  };

  const seek = async (tick: number, source: "snapshot" | "initial") => {
    setHistoryBusy(true);
    setHistoryError("");
    try {
      const next = await requestHistory(tick, source);
      if (next.run_id === runId) setHistory(next);
    } catch (caught) {
      setHistoryError(
        caught instanceof ApiError
          ? caught.message
          : "Could not reconstruct that tick from the backend.",
      );
    } finally {
      setHistoryBusy(false);
    }
  };

  // The displayed world: an immutable historical reconstruction, or the live frame.
  const view: WorldView | null = history ?? frame;

  // Recent transfers/messages of the live run (display only): kept for a few ticks so a
  // one-tick exchange stays visible while quiet ticks pass quickly.
  const [exchanges, setExchanges] = useState<
    (Interaction & { tick: number; run: string })[]
  >([]);
  useEffect(() => {
    if (!frame) return;
    setExchanges((previous) => {
      const kept = previous.filter(
        (e) => e.run === frame.run_id && e.tick < frame.tick,
      );
      const fresh = frame.interactions.map((i) => ({
        ...i,
        tick: frame.tick,
        run: frame.run_id,
      }));
      return [...kept, ...fresh].slice(-24);
    });
  }, [frame]);
  const shownExchanges = useMemo(() => {
    if (history)
      return history.interactions.map((i) => ({
        ...i,
        tick: history.tick,
        age: 0,
      }));
    if (!frame) return [];
    return exchanges
      .filter((e) => frame.tick - e.tick < EXCHANGE_FADE)
      .map((e) => ({ ...e, age: frame.tick - e.tick }));
  }, [history, frame, exchanges]);

  const highlight = useMemo<ReadonlySet<number>>(() => {
    if (!view) return new Set();
    if (detail && selectedEvent !== null)
      return new Set(detail.changes.map((c) => c.y * view.config.width + c.x));
    return new Set(view.changed_cells);
  }, [view, detail, selectedEvent]);

  // Display-only overlay of the cells in the last observation actually supplied.
  const localView = useMemo<ReadonlySet<number>>(() => {
    const payload = organismDetail?.latest_decision?.payload;
    // Only a living organism's last observation is overlaid; the dead keep the table.
    if (
      !view ||
      !showLocalView ||
      !organismDetail?.organism.alive ||
      !payload?.observation ||
      !payload.origin
    )
      return new Set();
    const { x, y } = payload.origin;
    return new Set(
      payload.observation.cells.map(
        (c) => (y + c.dy) * view.config.width + (x + c.dx),
      ),
    );
  }, [organismDetail, showLocalView, view]);

  const selectCellFromEvent = (cell: Cell) => setSelected(cell);

  const hoveredOrganism =
    hovered && view
      ? view.organisms.find((o) => o.id === hovered.id)
      : undefined;

  const apiHealth =
    frame?.provider?.kind === "api" ? frame.provider.health : null;
  const members = frame?.provider?.members ?? null;
  const healthLine = members ? (
    <p className="worker-health" data-testid="worker-health">
      {Object.entries(members).map(([id, m]) => (
        <span key={id} className="member-health">
          {id}: {memberLabel(m)} — {m.health?.status ?? "unknown"}
          {m.health?.last_latency_ms != null
            ? `, last ${(m.health.last_latency_ms / 1000).toFixed(1)}s`
            : ""}
          {m.health
            ? `, ${m.health.requests} requests, ${m.health.failures} failures`
            : ""}
          {m.health?.last_error_kind
            ? ` (last: ${m.health.last_error_kind})`
            : ""}
          {". "}
        </span>
      ))}
      Logical time waits for every organism; the page stays live.
    </p>
  ) : apiHealth ? (
    <p className="worker-health" data-testid="worker-health">
      API connection: {apiHealth.status} · {apiHealth.endpoint}
      {apiHealth.last_latency_ms !== null
        ? ` · last decision ${(apiHealth.last_latency_ms / 1000).toFixed(1)}s`
        : ""}
      {` · ${apiHealth.requests} requests, ${apiHealth.failures} failures`}
      {apiHealth.last_error_kind ? ` (last: ${apiHealth.last_error_kind})` : ""}
      {apiHealth.budget
        ? ` · budget ${apiHealth.budget.requests}/${apiHealth.budget.max_requests} requests`
        : ""}
      . Logical time waits for it; the page stays live.
    </p>
  ) : frame?.provider?.health ? (
    <p className="worker-health" data-testid="worker-health">
      Inference worker: {frame.provider.health.status}
      {frame.provider.health.pid ? ` · pid ${frame.provider.health.pid}` : ""}
      {frame.provider.health.load_seconds !== null
        ? ` · load ${frame.provider.health.load_seconds}s`
        : ""}
      {frame.provider.health.last_latency_ms !== null
        ? ` · last decision ${(frame.provider.health.last_latency_ms / 1000).toFixed(1)}s`
        : ""}
      {frame.provider.health.memory_gb !== null
        ? ` · ${frame.provider.health.memory_gb} GB`
        : ""}
      {` · ${frame.provider.health.requests} requests, ${frame.provider.health.failures} failures`}
      . Logical time waits for it; the page stays live.
    </p>
  ) : null;

  return (
    <div
      className={`app-shell stage-mode ${dashboardOpen ? "dashboard-open" : ""}`}
    >
      <header className="site-header compact">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
            <i />
          </div>
          <span>GENESIS</span>
          <span className="brand-divider" />
          <span className="brand-subtitle">Artificial life, observed.</span>
        </div>
        <div className="header-meta">
          <span className={`connection ${connection}`} role="status">
            <i />
            {connection === "connected"
              ? "Connected"
              : connection === "connecting"
                ? "Connecting"
                : connection === "incompatible"
                  ? "Version mismatch"
                  : "Disconnected"}
          </span>
          <button
            className="button small"
            onClick={toggleFullscreen}
            disabled={!frame}
            aria-pressed={fullscreen}
          >
            {fullscreen ? "Exit fullscreen" : "Fullscreen"}
          </button>
          <button
            className="button small"
            onClick={() => setDashboardOpen((open) => !open)}
            aria-expanded={dashboardOpen}
            aria-controls="dashboard"
          >
            Dashboard
          </button>
        </div>
      </header>

      <main className="stage-main">
        {connection === "incompatible" && (
          <div
            className="connection-alert"
            role="alert"
            data-testid="version-alert"
          >
            <div>
              <strong>Backend and page versions differ</strong>
              <p>
                {versionError} The backend is running but has not been restarted
                since the code changed. Restart the backend (and reload this
                page) so both run the same version. Nothing is shown rather than
                misreading its data.
              </p>
            </div>
            <button className="button small" onClick={() => void load()}>
              Check again
            </button>
          </div>
        )}

        {connection === "disconnected" && (
          <div className="connection-alert" role="alert">
            <div>
              <strong>Backend unavailable</strong>
              <p>
                {frame
                  ? "Showing the last confirmed state. The world is not updating."
                  : "Start the backend to load the saved world."}
              </p>
            </div>
            <button className="button small" onClick={() => void load()}>
              Reconnect
            </button>
          </div>
        )}

        {!frame || !view ? (
          <section className="empty-state panel">
            <div className="empty-grid-mark" aria-hidden="true" />
            <h2>
              {connection === "disconnected" || connection === "incompatible"
                ? "Waiting for the world"
                : "Loading the world"}
            </h2>
            <p className="muted">
              The grid will appear when its saved state is available.
            </p>
          </section>
        ) : (
          <>
            <section className="stage" ref={stageRef} aria-label="World view">
              <div className="stage-top">
                <div className="world-title">
                  {frame.config.fixture && (
                    <span className="fixture-tag" data-testid="fixture-tag">
                      TEST FIXTURE: {frame.config.fixture}
                    </span>
                  )}
                  {frame.provider && frame.organisms.length > 0 && (
                    <span
                      className={`provider-tag ${["mlx", "api", "mixed"].includes(frame.provider.kind) ? "live" : ""}`}
                      data-testid="provider-tag"
                    >
                      {frame.provider.kind === "mixed" && members
                        ? `MIXED MODELS (experiment) · ${Object.entries(members)
                            .map(([id, m]) => `${id}: ${memberLabel(m)}`)
                            .join(" · ")} — live models, nondeterministic`
                        : frame.provider.kind === "mlx"
                          ? `COGNITION: local MLX-LM · ${frame.provider.name} — live model, nondeterministic`
                          : frame.provider.kind === "api"
                            ? `COGNITION: ${frame.provider.health?.remote ? "REMOTE API" : "SELF-HOSTED API"} · ${frame.provider.health?.endpoint ?? frame.provider.name} · ${frame.provider.model} — live model, nondeterministic`
                            : `COGNITION: ${frame.provider.kind} · ${frame.provider.name} — not a live model`}
                    </span>
                  )}
                  <span className="stage-count">
                    {frame.organisms.length === 0
                      ? "No organisms"
                      : `${view.organisms.filter((o) => o.alive).length} of ${view.organisms.length} alive`}
                  </span>
                </div>
                <span className="tick-label">
                  TICK{" "}
                  <strong data-testid="tick">
                    {String(view.tick).padStart(6, "0")}
                  </strong>
                  {history && <span className="history-tag">HISTORY</span>}
                </span>
              </div>
              {history && (
                <div
                  className="history-banner"
                  role="status"
                  data-testid="history-banner"
                >
                  <div>
                    <strong>HISTORY · tick {history.tick}</strong>
                    <span>
                      Read-only reconstruction. The live run is at tick{" "}
                      {frame.tick} ({frame.status}); nothing here changes it.
                    </span>
                  </div>
                  <button
                    className="button small"
                    onClick={() => setHistory(null)}
                  >
                    Return to live
                  </button>
                </div>
              )}
              {frame.extinct && !history && (
                <div
                  className="extinct-banner"
                  role="status"
                  data-testid="extinct-banner"
                >
                  <strong>EXTINCT · tick {frame.tick}</strong>
                  <span>
                    The last organism died; the run is archived and cannot
                    advance. Its full history stays inspectable. Nothing was
                    reseeded.
                  </span>
                </div>
              )}
              <div className="stage-canvas">
                <CanvasGrid
                  frame={view}
                  selected={selected}
                  highlight={highlight}
                  onSelect={setSelected}
                  localView={localView}
                  selectedOrganism={organismId}
                  interactions={shownExchanges}
                  onHoverOrganism={(id, x, y) =>
                    setHovered((prev) =>
                      id === null
                        ? null
                        : prev &&
                            prev.id === id &&
                            Math.abs(prev.x - x) + Math.abs(prev.y - y) < 4
                          ? prev
                          : { id, x, y },
                    )
                  }
                />
                <ExchangeFeed exchanges={shownExchanges} />
              </div>
              <Legend
                energyCap={frame.config.energy_cap}
                cellCount={view.cells.length}
              />
              {hoveredOrganism && hovered && (
                <OrganismTooltip
                  organism={hoveredOrganism}
                  model={
                    frame.provider?.members?.[hoveredOrganism.id]
                      ? memberLabel(frame.provider.members[hoveredOrganism.id])
                      : null
                  }
                  detail={hoverKey ? (hoverDetails[hoverKey] ?? null) : null}
                  x={hovered.x}
                  y={hovered.y}
                />
              )}
            </section>
            <ControlBar
              frame={frame}
              controlsDisabled={
                connection !== "connected" || history !== null || frame.extinct
              }
              controlError={controlError}
              onCommand={(command) => void runCommand(command)}
              models={models}
              settings={settings}
              onSettings={setSettings}
              creating={creating}
              createDisabled={connection !== "connected" || history !== null}
              createError={error}
              onCreate={() => void createRun()}
              summaryBusy={summaryBusy}
              onSummary={() => void showSummary()}
              health={healthLine}
            />
            <p id="grid-help" className="grid-help">
              Hover an organism for details. Click a cell to select it (or focus
              the grid and use the arrow keys); open the Dashboard for every
              inspector. Energy inflow is +{frame.config.inflow_amount} per cell
              every {frame.config.inflow_interval} ticks, up to{" "}
              {frame.config.energy_cap}.
            </p>
            <Dashboard
              open={dashboardOpen}
              onClose={() => setDashboardOpen(false)}
            >
              <OrganismPanel
                organisms={view.organisms}
                selected={organismId}
                detail={organismDetail}
                error={organismError}
                showLocalView={showLocalView}
                onSelect={setOrganismId}
                onToggleLocalView={setShowLocalView}
                onSelectEvent={setSelectedEvent}
              />
              <TickPicker
                liveTick={frame.tick}
                history={history}
                snapshots={snapshots}
                busy={historyBusy}
                error={historyError}
                disabled={connection !== "connected"}
                onSeek={(tick, source) => void seek(tick, source)}
                onReturnLive={() => setHistory(null)}
              />
              <CellInspector
                frame={view}
                selected={selected}
                recent={recent}
                entryCost={entryCost}
                onSelect={setSelected}
                onSelectEvent={setSelectedEvent}
              />
              <section className="run-panel panel" aria-labelledby="run-title">
                <div className="panel-heading">
                  <h2 id="run-title">Run record</h2>
                  <span className="eyebrow">SAVED</span>
                </div>
                <dl className="properties">
                  <div>
                    <dt>World seed</dt>
                    <dd>{frame.config.seed}</dd>
                  </div>
                  <div>
                    <dt>Created</dt>
                    <dd className="date-value">
                      {new Date(frame.created_at).toLocaleString(undefined, {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </dd>
                  </div>
                  <div className="stacked">
                    <dt>
                      State hash (tick {view.tick}
                      {history ? ", history" : ""})
                    </dt>
                    <dd
                      className="hash-value"
                      data-testid="state-hash"
                      title={view.state_hash}
                    >
                      {view.state_hash}
                    </dd>
                  </div>
                  <div>
                    <dt>State schema</dt>
                    <dd>{frame.state_schema}</dd>
                  </div>
                  {frame.restored_from && (
                    <div className="stacked">
                      <dt>Loaded at startup from</dt>
                      <dd data-testid="restored-from">
                        {frame.restored_from.kind === "snapshot"
                          ? `snapshot @ tick ${frame.restored_from.snapshot_tick}`
                          : "initial state"}{" "}
                        + {frame.restored_from.ticks_replayed} replayed ticks
                      </dd>
                    </div>
                  )}
                  {frame.report_path && (
                    <div className="stacked">
                      <dt>Latest summary</dt>
                      <dd className="hash-value" data-testid="report-path">
                        {frame.report_path}
                      </dd>
                    </div>
                  )}
                </dl>
                <h3 className="subheading">Resource ledger</h3>
                <dl className="properties" data-testid="ledger">
                  <div>
                    <dt>Energy at start</dt>
                    <dd>{view.ledger.energy.initial}</dd>
                  </div>
                  <div>
                    <dt>+ Environmental inflow</dt>
                    <dd>{view.ledger.energy.environmental_inflow}</dd>
                  </div>
                  <div>
                    <dt>− Dissipated (upkeep, movement)</dt>
                    <dd>{view.ledger.energy.dissipation}</dd>
                  </div>
                  <div>
                    <dt>− Retired at death</dt>
                    <dd>{view.ledger.energy.death_retirement}</dd>
                  </div>
                  <div>
                    <dt>= Expected</dt>
                    <dd>{view.ledger.energy.expected}</dd>
                  </div>
                  <div>
                    <dt>In cells + organisms</dt>
                    <dd>
                      {view.ledger.energy.in_cells} +{" "}
                      {view.ledger.energy.in_organisms} ={" "}
                      {view.ledger.energy.actual}
                    </dd>
                  </div>
                  <div>
                    <dt>Material at start</dt>
                    <dd>{view.ledger.material.initial}</dd>
                  </div>
                  <div>
                    <dt>Loose + packed + carried</dt>
                    <dd data-testid="material-split">
                      {view.ledger.material.loose} +{" "}
                      {view.ledger.material.packed} +{" "}
                      {view.ledger.material.carried}
                    </dd>
                  </div>
                  <div>
                    <dt>+ Retired at death</dt>
                    <dd>{view.ledger.material.retired}</dd>
                  </div>
                  <div>
                    <dt>= Material accounted</dt>
                    <dd>
                      {view.ledger.material.actual +
                        view.ledger.material.retired}
                    </dd>
                  </div>
                </dl>
                <div
                  className={`ledger-status ${view.ledger.balanced ? "ok" : "bad"}`}
                  role="status"
                  data-testid="ledger-status"
                >
                  {view.ledger.balanced
                    ? "✓ Ledger balanced"
                    : "✗ Ledger mismatch — technical failure"}
                </div>
                <div className="saved-note">
                  <i /> Committed to {frame.journal_mode.toUpperCase()} SQLite
                  on this Mac · Genesis v{frame.engine_version} · rules{" "}
                  {frame.rules_version}
                </div>
              </section>
              {frame.state_schema >= 9 && (
                <PopulationPanel
                  runId={frame.run_id}
                  refreshKey={Math.floor(frame.tick / 4)}
                  maxTick={history?.tick}
                  selected={organismId}
                  onSelect={setOrganismId}
                />
              )}
              {frame.provider && (
                <DecisionInspector
                  runId={frame.run_id}
                  organismId={organismId}
                  refreshKey={frame.last_event_sequence}
                  maxTick={history?.tick}
                  onSelectEvent={setSelectedEvent}
                />
              )}
              <EventList
                events={events}
                hasMore={hasMore}
                selectedSequence={selectedEvent}
                detail={detail}
                detailError={detailError}
                onSelect={setSelectedEvent}
                onLoadOlder={() => void loadOlder()}
                onSelectCell={selectCellFromEvent}
              />
            </Dashboard>
            {summary && (
              <SummaryModal
                markdown={summary.markdown}
                path={summary.path}
                onClose={() => setSummary(null)}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}
