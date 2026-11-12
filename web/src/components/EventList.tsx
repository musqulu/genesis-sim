import type { Cell, EventDetail, EventSummary } from "../api";

interface Props {
  events: EventSummary[];
  hasMore: boolean;
  selectedSequence: number | null;
  detail: EventDetail | null;
  detailError: string;
  onSelect: (sequence: number | null) => void;
  onLoadOlder: () => void;
  onSelectCell: (cell: Cell) => void;
  onOpenDecision?: (actorId: string, requestId: string) => void; // M14
}

export function describeEvent(event: EventSummary): string {
  const s = event.summary;
  if (event.type === "energy_inflow")
    return `+${s.credited_total} energy in ${s.cells_credited} cells (${s.blocked_total} blocked at cap)`;
  if (event.type === "run_created")
    return `Seed ${s.seed}: ${s.cells_total} cells, ${s.initial_energy} energy, ${s.initial_material} material`;
  const who = event.actor_id ?? "";
  if (event.type === "organism_born")
    return `${who} born at (${s.x}, ${s.y}) with ${s.energy} energy`;
  if (event.type === "upkeep")
    return `Upkeep: −${s.charged_total} energy from ${s.organisms} organism(s)`;
  if (event.type === "action_accepted") {
    if (s.action === "move")
      return `${who} moved ${s.direction} to (${s.to_x}, ${s.to_y}), −${s.energy_cost} energy`;
    if (s.action === "extract")
      return `${who} extracted ${s.amount} ${s.resource} at (${s.x}, ${s.y}), fee ${s.energy_cost}`;
    if (s.action === "modify")
      return `${who} ${s.mode}ed ${s.amount} at (${s.x}, ${s.y}): packed ${s.packed_before}→${s.packed_after}, entry cost now ${s.entry_cost_after}`;
    return `${who} waited`;
  }
  if (event.type === "action_rejected")
    return event.rule_id === "cognition.parse.v1"
      ? `${who} output unparsed: ${s.reason}`
      : `${who} proposal rejected: ${s.reason}`;
  if (event.type === "decision_round")
    return `Decision round: ${s.due} due, cognition fee −${s.charged_total}`;
  if (event.type === "organism_died")
    return `${who} died (${s.cause}) at age ${s.age}; ${s.retired_energy} energy and ${s.retired_material ?? 0} material retired`;
  if (event.type === "extinction")
    return `Extinction: no living organisms remain; run archived`;
  return event.type;
}

const MAX_ROWS = 200;

export default function EventList({
  events,
  hasMore,
  selectedSequence,
  detail,
  onOpenDecision,
  detailError,
  onSelect,
  onLoadOlder,
  onSelectCell,
}: Props) {
  return (
    <section className="events panel" aria-labelledby="events-title">
      <div className="panel-heading">
        <h2 id="events-title">Event list</h2>
        <span className="eyebrow">NEWEST FIRST</span>
      </div>
      <div className="events-body">
        <div className="event-column">
          <ol className="event-items" aria-label="Events">
            {events.map((event) => (
              <li key={event.sequence}>
                <button
                  className={`event-item ${event.sequence === selectedSequence ? "selected" : ""}`}
                  aria-pressed={event.sequence === selectedSequence}
                  onClick={() =>
                    onSelect(
                      event.sequence === selectedSequence
                        ? null
                        : event.sequence,
                    )
                  }
                >
                  <span className="event-tick">tick {event.tick}</span>
                  <span className="event-text">{describeEvent(event)}</span>
                  <span className="event-rule">{event.rule_id}</span>
                </button>
              </li>
            ))}
          </ol>
          {hasMore && (
            <button className="button small" onClick={onLoadOlder}>
              Load older events
            </button>
          )}
        </div>
        <div className="event-detail" aria-live="polite">
          {detailError ? (
            <p role="alert" className="error-text">
              {detailError}
            </p>
          ) : detail ? (
            <>
              <h3>
                Event {detail.sequence} · tick {detail.tick}
                {onOpenDecision &&
                  detail.actor_id &&
                  typeof detail.payload?.request_id === "string" && (
                    <>
                      {" "}
                      ·{" "}
                      <button
                        className="link-button"
                        onClick={() =>
                          onOpenDecision(
                            detail.actor_id!,
                            detail.payload!.request_id as string,
                          )
                        }
                      >
                        open decision
                      </button>
                    </>
                  )}
              </h3>
              <dl className="properties">
                <div>
                  <dt>Rule ID</dt>
                  <dd data-testid="event-rule">{detail.rule_id}</dd>
                </div>
                <div>
                  <dt>Cause</dt>
                  <dd>{detail.cause}</dd>
                </div>
                {Object.entries(detail.summary).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd data-testid={`event-${key}`}>{value}</dd>
                  </div>
                ))}
                {detail.ledger.map((entry) => (
                  <div key={`${entry.resource}-${entry.category}`}>
                    <dt>ledger: {entry.category}</dt>
                    <dd>
                      +{entry.amount} {entry.resource}
                    </dd>
                  </div>
                ))}
              </dl>
              {detail.changes.length > 0 ? (
                <>
                  <p className="muted small-copy">
                    {detail.changes.length} changed cells (outlined on the
                    grid). Select one to inspect it.
                  </p>
                  <div
                    className="change-table"
                    role="region"
                    aria-label="Changed cells"
                    tabIndex={0}
                  >
                    <table>
                      <thead>
                        <tr>
                          <th>Cell</th>
                          <th>Field</th>
                          <th>Before</th>
                          <th>After</th>
                          <th>Δ</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.changes.slice(0, MAX_ROWS).map((change) => (
                          <tr key={`${change.x},${change.y},${change.field}`}>
                            <td>
                              <button
                                className="link-button"
                                onClick={() =>
                                  onSelectCell({ x: change.x, y: change.y })
                                }
                              >
                                ({change.x}, {change.y})
                              </button>
                            </td>
                            <td>{change.field}</td>
                            <td>{change.before}</td>
                            <td>{change.after}</td>
                            <td>
                              {change.delta > 0
                                ? `+${change.delta}`
                                : change.delta}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {detail.changes.length > MAX_ROWS && (
                    <p className="muted small-copy">
                      Showing the first {MAX_ROWS} of {detail.changes.length}.
                      All are outlined on the grid.
                    </p>
                  )}
                </>
              ) : (
                <p className="muted small-copy">This event changed no cells.</p>
              )}
              {detail.organism_changes.length > 0 && (
                <div
                  className="change-table organism-changes"
                  role="region"
                  aria-label="Organism changes"
                  tabIndex={0}
                >
                  <table>
                    <thead>
                      <tr>
                        <th>Organism</th>
                        <th>Field</th>
                        <th>Before</th>
                        <th>After</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.organism_changes.map((change, i) => (
                        <tr key={i}>
                          <td>{change.organism_id}</td>
                          <td>{change.field}</td>
                          <td>{JSON.stringify(change.before)}</td>
                          <td>{JSON.stringify(change.after)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          ) : (
            <p className="muted small-copy">
              Select an event to see its rule, exact deltas and changed cells.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
