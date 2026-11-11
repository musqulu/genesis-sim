import { useState } from "react";
import type { ControlCommand, GridFrame } from "../api";

const SPEEDS = [1, 2, 5, 10, 20, 50];

interface Props {
  frame: GridFrame;
  disabled: boolean;
  error: string;
  onCommand: (command: ControlCommand) => void;
}

export default function RunControls({
  frame,
  disabled,
  error,
  onCommand,
}: Props) {
  const [until, setUntil] = useState("");
  const running = frame.status === "running";
  const speeds = SPEEDS.includes(frame.speed)
    ? SPEEDS
    : [...SPEEDS, frame.speed].sort((a, b) => a - b);
  const untilValue = until === "" ? undefined : Number(until);
  const untilValid =
    untilValue === undefined ||
    (Number.isInteger(untilValue) && untilValue > frame.tick);
  const primary = running ? "Pause" : frame.started ? "Resume" : "Start";

  return (
    <div className="run-controls" role="group" aria-label="Run controls">
      <span
        className={`run-state ${frame.status}`}
        role="status"
        data-testid="run-status"
      >
        {running ? "▶ Running" : "Ⅱ Paused"}
      </span>
      <button
        className="button primary"
        disabled={disabled || (!running && !untilValid)}
        onClick={() => {
          if (running) {
            onCommand({ command: "pause" });
            return;
          }
          onCommand({
            command: frame.started ? "resume" : "start",
            ...(untilValue === undefined ? {} : { until_tick: untilValue }),
          });
          // A target applies to this one start/resume; the backend clears it on
          // arrival, so a stale value must not block the next Resume.
          setUntil("");
        }}
      >
        {primary}
      </button>
      <button
        className="button"
        disabled={disabled || running}
        onClick={() => onCommand({ command: "step" })}
        title={running ? "Pause before stepping" : "Advance exactly one tick"}
      >
        Step
      </button>
      <label className="control-field">
        Speed
        <select
          aria-label="Speed"
          value={frame.speed}
          disabled={disabled}
          onChange={(event) =>
            onCommand({
              command: "speed",
              ticks_per_second: Number(event.target.value),
            })
          }
        >
          {speeds.map((speed) => (
            <option key={speed} value={speed}>
              {speed} tick{speed === 1 ? "" : "s"}/s
            </option>
          ))}
        </select>
      </label>
      <label className="control-field">
        Pause at tick
        <input
          aria-label="Pause at tick"
          type="number"
          min={frame.tick + 1}
          placeholder="none"
          value={
            running && frame.until_tick !== null ? frame.until_tick : until
          }
          disabled={running}
          onChange={(event) => setUntil(event.target.value)}
        />
      </label>
      {frame.pending && (
        <p className="pending-note" role="status" data-testid="pending">
          Tick {frame.pending.tick} waiting at the decision barrier:{" "}
          {frame.pending.completed} / {frame.pending.total} responses
          {frame.pending.failed ? `, ${frame.pending.failed} failed` : ""}
          {running
            ? ""
            : ". Paused — received responses are kept; nothing commits until you resume or step."}
        </p>
      )}
      <p className="control-note">
        Speed changes only how fast ticks are attempted; it never changes what a
        tick does.
        {!untilValid && (
          <span className="error-text">
            {" "}
            Pause-at tick must be after the current tick.
          </span>
        )}
      </p>
      {(error || frame.last_error) && (
        <p role="alert" className="error-text control-error">
          {error || `Technical pause: ${frame.last_error}`}
        </p>
      )}
    </div>
  );
}
