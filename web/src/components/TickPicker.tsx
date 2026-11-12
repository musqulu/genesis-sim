import { useEffect, useState } from "react";
import type { HistoryFrame, KeyMoment, SnapshotInfo } from "../api";

type Source = "snapshot" | "initial";

interface Props {
  liveTick: number;
  history: HistoryFrame | null;
  snapshots: SnapshotInfo[];
  busy: boolean;
  error: string;
  disabled: boolean;
  onSeek: (tick: number, source: Source) => void;
  onReturnLive: () => void;
  moments?: KeyMoment[]; // M14: births, deaths, modifications, extinction
}

export default function TickPicker({
  liveTick,
  history,
  snapshots,
  busy,
  error,
  disabled,
  onSeek,
  onReturnLive,
  moments = [],
}: Props) {
  const [tick, setTick] = useState(String(history?.tick ?? 0));
  const [source, setSource] = useState<Source>("snapshot");
  useEffect(() => {
    if (history) setTick(String(history.tick));
  }, [history]);
  const value = Number(tick);
  const valid =
    tick !== "" && Number.isInteger(value) && value >= 0 && value <= liveTick;

  return (
    <section className="history panel" aria-labelledby="history-title">
      <div className="panel-heading">
        <h2 id="history-title">Recorded history</h2>
        <span className="eyebrow">{history ? "READ-ONLY" : "LIVE"}</span>
      </div>
      <form
        className="tick-picker"
        onSubmit={(event) => {
          event.preventDefault();
          if (valid) onSeek(value, source);
        }}
      >
        <label className="tick-slider">
          Tick 0 – {liveTick}
          <input
            aria-label="History tick slider"
            type="range"
            min={0}
            max={liveTick}
            value={valid ? value : 0}
            disabled={disabled}
            onChange={(e) => setTick(e.target.value)}
          />
        </label>
        <label>
          Tick
          <input
            aria-label="History tick"
            type="number"
            min={0}
            max={liveTick}
            required
            value={tick}
            disabled={disabled}
            onChange={(e) => setTick(e.target.value)}
          />
        </label>
        <label>
          Rebuild from
          <select
            aria-label="Rebuild from"
            value={source}
            disabled={disabled}
            onChange={(e) => setSource(e.target.value as Source)}
          >
            <option value="snapshot">Nearest valid snapshot + events</option>
            <option value="initial">
              Initial state + all events (full replay)
            </option>
          </select>
        </label>
        <button
          className="button primary"
          type="submit"
          disabled={disabled || busy || !valid}
        >
          {busy ? "Rebuilding…" : "View tick"}
        </button>
        {history && (
          <>
            <button
              type="button"
              className="button"
              aria-label="Previous tick"
              disabled={disabled || busy || history.tick <= 0}
              onClick={() => onSeek(history.tick - 1, source)}
            >
              ◀ tick
            </button>
            <button
              type="button"
              className="button"
              aria-label="Next tick"
              disabled={disabled || busy || history.tick >= liveTick}
              onClick={() => onSeek(history.tick + 1, source)}
            >
              tick ▶
            </button>
            <button type="button" className="button" onClick={onReturnLive}>
              Return to live
            </button>
          </>
        )}
      </form>
      {moments.length > 0 && (
        <div className="moments" data-testid="key-moments">
          <h3 className="subheading">Key moments</h3>
          <ul className="cell-history" aria-label="Key moments">
            {moments
              .slice()
              .reverse()
              .slice(0, 40)
              .map((m) => (
                <li key={m.sequence} className={`moment ${m.kind}`}>
                  <span>
                    t{m.tick} · {m.text}
                  </span>
                  <span>
                    <button
                      type="button"
                      className="link-button"
                      disabled={disabled || busy || m.tick < 1}
                      onClick={() => onSeek(m.tick - 1, source)}
                    >
                      before
                    </button>{" "}
                    <button
                      type="button"
                      className="link-button"
                      disabled={disabled || busy}
                      onClick={() => onSeek(m.tick, source)}
                    >
                      after
                    </button>
                  </span>
                </li>
              ))}
          </ul>
        </div>
      )}
      {!valid && tick !== "" && (
        <p className="error-text small-copy">
          Choose a committed tick between 0 and {liveTick}.
        </p>
      )}
      {error && (
        <p role="alert" className="error-text small-copy">
          {error}
        </p>
      )}
      {history && (
        <dl className="properties" data-testid="history-source">
          <div>
            <dt>Rebuilt from</dt>
            <dd data-testid="history-rebuilt">
              {history.source.kind === "snapshot"
                ? `snapshot @ tick ${history.source.snapshot_tick}`
                : "initial state"}{" "}
              + {history.source.ticks_replayed} ticks (
              {history.source.events_applied} events)
            </dd>
          </div>
          <div>
            <dt>Recorded hash check</dt>
            <dd data-testid="history-verified">
              {history.verified ? "✓ matches" : "✗ MISMATCH"}
            </dd>
          </div>
          {history.source.skipped_snapshots.map((skip) => (
            <div key={skip.tick}>
              <dt>Skipped snapshot @ {skip.tick}</dt>
              <dd className="error-text">{skip.reason}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="muted small-copy" data-testid="snapshot-list">
        Snapshots:{" "}
        {snapshots.length === 0
          ? "none"
          : snapshots
              .slice()
              .reverse()
              .map(
                (s) =>
                  `${s.tick} (${s.reason}${s.checksum_ok ? "" : ", corrupt"})`,
              )
              .join(", ")}
        . Replay applies recorded deltas only; rules are never re-run.
      </p>
    </section>
  );
}
