import { useEffect, useState } from "react";
import type { Cell, CellRecentChange, WorldView } from "../api";

interface Props {
  frame: WorldView;
  selected: Cell;
  recent: CellRecentChange[];
  entryCost: number | null;
  onSelect: (cell: Cell) => void;
  onSelectEvent: (sequence: number) => void;
  live?: WorldView | null; // M14: in history mode, the live frame to compare against
}

export default function CellInspector({
  frame,
  selected,
  recent,
  entryCost,
  onSelect,
  onSelectEvent,
  live = null,
}: Props) {
  const [x, setX] = useState(String(selected.x));
  const [y, setY] = useState(String(selected.y));
  useEffect(() => {
    setX(String(selected.x));
    setY(String(selected.y));
  }, [selected]);
  const cell = frame.cells[selected.y * frame.config.width + selected.x];
  return (
    <section className="inspector panel" aria-labelledby="inspector-title">
      <div className="panel-heading">
        <h2 id="inspector-title">Cell inspector</h2>
        <span className="eyebrow">SELECTED</span>
      </div>
      <div
        className="coordinate-display"
        aria-live="polite"
        data-testid="selected-cell"
      >
        <span>{String(cell.x).padStart(2, "0")}</span>
        <span className="coordinate-comma">,</span>
        <span>{String(cell.y).padStart(2, "0")}</span>
      </div>
      <dl className="properties">
        <div>
          <dt>Energy</dt>
          <dd data-testid="cell-energy">
            {cell.energy} / {frame.config.energy_cap}
          </dd>
        </div>
        <div>
          <dt>Loose material</dt>
          <dd data-testid="cell-material">{cell.loose_material}</dd>
        </div>
        <div>
          <dt>Packed material</dt>
          <dd data-testid="cell-packed">
            {cell.packed_material} / {frame.config.packed_cap}
          </dd>
        </div>
        <div>
          <dt>Energy to enter</dt>
          <dd data-testid="cell-entry-cost">
            {entryCost === null ? "—" : `${entryCost} (1 + ⌊packed/2⌋)`}
          </dd>
        </div>
        <div>
          <dt>Cell index</dt>
          <dd>{cell.y * frame.config.width + cell.x}</dd>
        </div>
        <div>
          <dt>World tick</dt>
          <dd>{frame.tick}</dd>
        </div>
      </dl>
      {live && live.tick !== frame.tick && (
        <p className="small-copy" data-testid="cell-compare">
          {(() => {
            const now = live.cells[selected.y * live.config.width + selected.x];
            const d = (a: number, b: number) =>
              b === a ? "=" : b > a ? `+${b - a}` : `${b - a}`;
            return `Now (tick ${live.tick}): energy ${now.energy} (${d(cell.energy, now.energy)}), loose ${now.loose_material} (${d(cell.loose_material, now.loose_material)}), packed ${now.packed_material} (${d(cell.packed_material, now.packed_material)})`;
          })()}
        </p>
      )}
      <h3 className="subheading">Recent changes</h3>
      {recent.length === 0 ? (
        <p className="muted small-copy">
          No recorded changes to this cell yet.
        </p>
      ) : (
        <ul className="cell-history">
          {recent.map((change) => (
            <li key={`${change.event_sequence}-${change.field}`}>
              <button
                className="link-button"
                onClick={() => onSelectEvent(change.event_sequence)}
              >
                tick {change.tick}: {change.field} {change.before} →{" "}
                {change.after} (
                {change.delta > 0 ? `+${change.delta}` : change.delta})
                {change.actor_id ? ` by ${change.actor_id}` : ""}
              </button>
              <span className="event-rule">{change.rule_id}</span>
            </li>
          ))}
        </ul>
      )}
      <form
        className="coordinate-form"
        onSubmit={(event) => {
          event.preventDefault();
          const next = { x: Number(x), y: Number(y) };
          if (
            x !== "" &&
            y !== "" &&
            Number.isInteger(next.x) &&
            Number.isInteger(next.y) &&
            next.x >= 0 &&
            next.y >= 0 &&
            next.x < frame.config.width &&
            next.y < frame.config.height
          )
            onSelect(next);
        }}
      >
        <label>
          x
          <input
            aria-label="Cell x"
            type="number"
            min={0}
            max={frame.config.width - 1}
            required
            value={x}
            onChange={(e) => setX(e.target.value)}
          />
        </label>
        <label>
          y
          <input
            aria-label="Cell y"
            type="number"
            min={0}
            max={frame.config.height - 1}
            required
            value={y}
            onChange={(e) => setY(e.target.value)}
          />
        </label>
        <button type="submit" className="button small">
          Inspect
        </button>
      </form>
    </section>
  );
}
