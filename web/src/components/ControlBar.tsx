import { useState, type ReactNode } from "react";
import {
  checkProvider,
  type ControlCommand,
  type GridFrame,
  type ModelsInfo,
  type ProviderCheck,
} from "../api";
import RunControls from "./RunControls";

export interface NewRunSettings {
  seed: string;
  model: string; // "" = no organisms, otherwise "mlx:<id>" or "api:<profile id>"
  organisms: string;
  model2: string; // optional second model: a mixed-model experiment
  organisms2: string;
  fixture: string;
  size: string; // square map side for a new run ("32" = standard)
  tendencies: "seeded" | "neutral"; // starting tendencies of live organisms
}

const MAP_SIZES = ["32", "24", "20", "16", "12", "8"];

interface Props {
  frame: GridFrame;
  controlsDisabled: boolean;
  controlError: string;
  onCommand: (command: ControlCommand) => void;
  models: ModelsInfo | null;
  settings: NewRunSettings;
  onSettings: (next: NewRunSettings) => void;
  creating: boolean;
  createDisabled: boolean;
  createError: string;
  onCreate: () => void;
  summaryBusy: boolean;
  onSummary: () => void;
  health: ReactNode;
}

export const MAX_ORGANISMS = 16;

/** Always-visible run controls and new-run setup below the stage. */
export default function ControlBar({
  frame,
  controlsDisabled,
  controlError,
  onCommand,
  models,
  settings,
  onSettings,
  creating,
  createDisabled,
  createError,
  onCreate,
  summaryBusy,
  onSummary,
  health,
}: Props) {
  const set = (patch: Partial<NewRunSettings>) =>
    onSettings({ ...settings, ...patch });
  const runtimeOk = models?.runtime.available ?? false;
  const apiProfiles = models?.api?.profiles ?? [];
  const apiId = settings.model.startsWith("api:")
    ? settings.model.slice(4)
    : "";
  const [check, setCheck] = useState<
    (ProviderCheck & { id: string }) | "checking" | null
  >(null);
  const runCheck = () => {
    const id = apiId;
    setCheck("checking");
    checkProvider(id)
      .then((result) => setCheck({ ...result, id }))
      .catch((error: unknown) =>
        setCheck({
          id,
          ok: false,
          message: error instanceof Error ? error.message : String(error),
        }),
      );
  };
  const okCount = (value: string) => {
    const n = Number(value);
    return value !== "" && Number.isInteger(n) && n >= 1 && n <= MAX_ORGANISMS;
  };
  const mixed = Boolean(settings.model && settings.model2);
  const countValid =
    !settings.model ||
    (okCount(settings.organisms) &&
      (!mixed ||
        (okCount(settings.organisms2) &&
          Number(settings.organisms) + Number(settings.organisms2) <=
            MAX_ORGANISMS)));
  const modelOptions = (exclude: string) => (
    <>
      {models && models.models.length > 0 && (
        <optgroup label="Local (this Mac)">
          {models.models.map((m) => (
            <option
              key={m.id}
              value={`mlx:${m.id}`}
              disabled={!runtimeOk || exclude === `mlx:${m.id}`}
            >
              local MLX-LM · {m.id} ({m.size_gb} GB)
            </option>
          ))}
        </optgroup>
      )}
      {apiProfiles.length > 0 && (
        <optgroup label="API connections">
          {apiProfiles.map((p) => (
            <option
              key={p.id}
              value={`api:${p.id}`}
              disabled={!p.available || exclude === `api:${p.id}`}
            >
              API · {p.label} · {p.model || "no model set"}
              {p.available ? "" : ` (${p.reason})`}
            </option>
          ))}
        </optgroup>
      )}
    </>
  );
  return (
    <section className="control-bar" aria-label="Run controls and setup">
      <div className="control-row">
        <RunControls
          frame={frame}
          disabled={controlsDisabled}
          error={controlError}
          onCommand={onCommand}
        />
        <div className="run-identity">
          <span className="eyebrow">RUN</span>
          <span className="run-id" data-testid="run-id" title={frame.run_id}>
            {frame.run_id}
          </span>
        </div>
      </div>
      {health}
      <form
        className="new-run-bar"
        aria-label="New run"
        onSubmit={(event) => {
          event.preventDefault();
          if (countValid) onCreate();
        }}
      >
        <label>
          Seed
          <input
            aria-label="World seed"
            type="number"
            min="0"
            max="4294967295"
            required
            value={settings.seed}
            onChange={(e) => set({ seed: e.target.value })}
          />
        </label>
        <label>
          Map size
          <select
            aria-label="Map size"
            value={settings.fixture ? "32" : settings.size}
            disabled={Boolean(settings.fixture)}
            onChange={(e) => set({ size: e.target.value })}
          >
            {MAP_SIZES.map((s) => (
              <option key={s} value={s}>
                {s} × {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          Tendencies
          <select
            aria-label="Tendencies"
            value={settings.tendencies}
            disabled={!settings.model}
            title="Seeded: each organism gets its own six values 0–100. Neutral: all 50 (paired control; same world)."
            onChange={(e) =>
              set({ tendencies: e.target.value as "seeded" | "neutral" })
            }
          >
            <option value="seeded">Seeded (varied)</option>
            <option value="neutral">Neutral (all 50)</option>
          </select>
        </label>
        <label className="model-field">
          Model
          <select
            aria-label="Model"
            value={settings.model}
            disabled={!models}
            onChange={(e) => {
              set({
                model: e.target.value,
                fixture: "",
                model2:
                  e.target.value && e.target.value !== settings.model2
                    ? settings.model2
                    : "",
              });
              setCheck(null);
            }}
          >
            <option value="">No organisms (environment only)</option>
            {modelOptions("")}
          </select>
        </label>
        {apiId && (
          <button
            type="button"
            className="button"
            disabled={check === "checking"}
            onClick={runCheck}
          >
            {check === "checking" ? "Checking…" : "Check connection"}
          </button>
        )}
        <label>
          Organisms
          <input
            aria-label="Organisms"
            type="number"
            min={1}
            max={MAX_ORGANISMS}
            value={settings.model ? settings.organisms : "0"}
            disabled={!settings.model}
            onChange={(e) => set({ organisms: e.target.value })}
          />
        </label>
        {settings.model && (
          <>
            <label className="model-field">
              Second model
              <select
                aria-label="Second model"
                value={settings.model2}
                onChange={(e) => set({ model2: e.target.value })}
              >
                <option value="">None (one model)</option>
                {modelOptions(settings.model)}
              </select>
            </label>
            {settings.model2 && (
              <label>
                Organisms
                <input
                  aria-label="Organisms with the second model"
                  type="number"
                  min={1}
                  max={MAX_ORGANISMS}
                  value={settings.organisms2}
                  onChange={(e) => set({ organisms2: e.target.value })}
                />
              </label>
            )}
          </>
        )}
        {frame.dev_fixtures.length > 0 && !settings.model && (
          <label>
            Test fixture
            <select
              aria-label="Test fixture"
              value={settings.fixture}
              onChange={(e) => set({ fixture: e.target.value })}
            >
              <option value="">None (standard rules)</option>
              {frame.dev_fixtures.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
        <button
          className="button primary"
          disabled={creating || createDisabled || !countValid}
        >
          {creating ? "Creating…" : "New run ↗"}
        </button>
        <button
          type="button"
          className="button"
          disabled={summaryBusy || createDisabled}
          onClick={onSummary}
        >
          {summaryBusy ? "Writing…" : "Summary"}
        </button>
      </form>
      {models && models.models.length > 0 && !runtimeOk && (
        <p className="muted small-copy" data-testid="mlx-unavailable">
          Local models found, but the MLX runtime is unavailable (
          {models.runtime.reason}). Set GENESIS_MLX_PYTHON.
        </p>
      )}
      {apiId && check && check !== "checking" && check.id === apiId && (
        <p
          className={`small-copy ${check.ok === false ? "error-text" : "muted"}`}
          data-testid="provider-check"
        >
          {check.ok === true ? "✓ " : check.ok === false ? "✗ " : "? "}
          {check.message}
        </p>
      )}
      {mixed && (
        <p className="muted small-copy" data-testid="mixed-note">
          Mixed-model experiment: organisms 1–{settings.organisms} use the first
          model, the next {settings.organisms2} the second, each for life.
        </p>
      )}
      {models?.api?.problems.map((problem) => (
        <p key={problem} className="error-text small-copy">
          API profiles: {problem}
        </p>
      ))}
      {!countValid && (
        <p className="error-text small-copy">
          Organisms must be 1–{MAX_ORGANISMS}
          {mixed ? ` per model, at most ${MAX_ORGANISMS} in total` : ""}.
        </p>
      )}
      {createError && (
        <p role="alert" className="error-text small-copy">
          {createError}
        </p>
      )}
    </section>
  );
}
