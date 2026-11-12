import type { ActionPreview, OrganismDetail, OrganismView } from "../api";
import MemoryInspector from "./MemoryInspector";
import MessagesPanel from "./MessagesPanel";
import ProfilePanel from "./ProfilePanel";
import InheritancePanel from "./InheritancePanel";

function describeProposal(proposal: ActionPreview["proposal"]): string {
  const { action, ...args } = proposal;
  const parts = Object.values(args).map(String);
  return parts.length ? `${action} ${parts.join(" ")}` : String(action);
}

interface Props {
  organisms: OrganismView[];
  selected: string | null;
  detail: OrganismDetail | null;
  error: string;
  showLocalView: boolean;
  onSelect: (id: string) => void;
  onToggleLocalView: (show: boolean) => void;
  onSelectEvent: (sequence: number) => void;
}

function describeOutcome(detail: OrganismDetail): string {
  const decision = detail.latest_decision;
  if (!decision) return "No decision recorded yet";
  if (decision.type === "action_rejected")
    return decision.rule_id === "cognition.parse.v1"
      ? `Unparsed: ${decision.summary.reason}`
      : `Rejected: ${decision.summary.reason}`;
  const s = decision.summary;
  if (s.action === "move")
    return `Moved ${s.direction} (−${s.energy_cost} energy)`;
  if (s.action === "extract")
    return `Extracted ${s.amount} ${s.resource} (fee ${s.energy_cost})`;
  if (s.action === "modify")
    return `${s.mode === "pack" ? "Packed" : "Unpacked"} ${s.amount} (packed ${s.packed_before}→${s.packed_after}, −${s.energy_cost} energy)`;
  if (s.action === "reproduce") {
    const passed = (
      s.inheritance as unknown as
        { memories?: { selected: string[]; retained: string[] } } | undefined
    )?.memories;
    const memories = passed?.selected.length
      ? `; passed ${passed.retained.length} of ${passed.selected.length} memories`
      : "";
    return `Gave birth to ${s.child_id} (endowment ${s.endowment}, cost ${s.energy_cost}${memories})`;
  }
  if (s.action === "reflect")
    return `Reflected: ${s.count} interpretation(s)${s.energy_cost ? ` (−${s.energy_cost} energy)` : ""}`;
  if (s.action === "transfer")
    return `Gave ${s.amount} ${s.resource} to ${s.target} (fee ${s.energy_cost})`;
  if (s.action === "communicate")
    return `Told ${s.target}: “${s.text}” (−${s.energy_cost} energy)`;
  return "Waited";
}

export default function OrganismPanel({
  organisms,
  selected,
  detail,
  error,
  showLocalView,
  onSelect,
  onToggleLocalView,
  onSelectEvent,
}: Props) {
  if (organisms.length === 0)
    return (
      <section className="organisms panel" aria-labelledby="organisms-title">
        <div className="panel-heading">
          <h2 id="organisms-title">Organisms</h2>
          <span className="eyebrow">NONE</span>
        </div>
        <p className="muted small-copy">
          This run has no organisms. In M04 only labeled test fixtures seed
          organisms; autonomous cognition arrives in later milestones.
        </p>
      </section>
    );
  const o = detail?.organism;
  const decision = detail?.latest_decision;
  const observation = decision?.payload.observation;
  const radius = observation?.perception_radius ?? 0;
  return (
    <section className="organisms panel" aria-labelledby="organisms-title">
      <div className="panel-heading">
        <h2 id="organisms-title">Organisms</h2>
        <span className="eyebrow">
          {organisms.filter((x) => x.alive).length} LIVING / {organisms.length}
        </span>
      </div>
      <ul className="organism-list" aria-label="Organism list">
        {organisms.map((item) => (
          <li key={item.id}>
            <button
              className={`organism-item ${item.id === selected ? "selected" : ""}`}
              aria-pressed={item.id === selected}
              onClick={() => onSelect(item.id)}
            >
              <span className={`status-dot ${item.alive ? "alive" : "dead"}`} />
              <strong>{item.id}</strong>
              <span className="muted">
                {item.alive ? "alive" : `died t${item.death_tick}`}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="error-text small-copy">
          {error}
        </p>
      )}
      {o && detail && (
        <>
          <p className="fixture-note">
            {o.label.startsWith("TEST POLICY")
              ? `${o.label} — scripted, not autonomous`
              : `${o.label} — decisions from a live model (nondeterministic)`}
          </p>
          <dl className="properties" data-testid="organism-detail">
            <div>
              <dt>Status</dt>
              <dd data-testid="organism-status">
                {o.alive ? "alive" : `dead (${o.death_cause})`}
              </dd>
            </div>
            <div>
              <dt>Energy</dt>
              <dd data-testid="organism-energy">
                {o.energy} / {o.energy_capacity}
              </dd>
            </div>
            {o.material_capacity > 0 && (
              <div>
                <dt>Carried material</dt>
                <dd data-testid="organism-carried">
                  {o.carried_material} / {o.material_capacity}
                </dd>
              </div>
            )}
            <div>
              <dt>Age</dt>
              <dd data-testid="organism-age">{o.age}</dd>
            </div>
            <div>
              <dt>Position</dt>
              <dd data-testid="organism-position">
                ({o.x}, {o.y})
              </dd>
            </div>
            <div>
              <dt>Born / generation</dt>
              <dd>
                tick {o.birth_tick} / {o.generation}
              </dd>
            </div>
            <div>
              <dt>Parent</dt>
              <dd data-testid="organism-parent">
                {o.parent_ids.length ? (
                  <button
                    className="link-button"
                    onClick={() => onSelect(o.parent_ids[0])}
                  >
                    {o.parent_ids[0]}
                  </button>
                ) : (
                  "none (seed)"
                )}
              </dd>
            </div>
            {!o.alive && (
              <>
                <div>
                  <dt>Died at tick</dt>
                  <dd>{o.death_tick}</dd>
                </div>
                <div>
                  <dt>Retired at death</dt>
                  <dd data-testid="organism-retired">
                    {o.retired_energy} energy, {o.retired_material} material
                  </dd>
                </div>
              </>
            )}
            <div>
              <dt>Sampled lifespan</dt>
              <dd data-testid="organism-lifespan">
                {o.lifespan === null
                  ? "hidden while alive"
                  : `${o.lifespan} ticks`}
              </dd>
            </div>
          </dl>
          {detail.action_preview.length > 0 && (
            <>
              <h3 className="subheading">Cost preview (engine rules, now)</h3>
              <div
                className="change-table"
                role="region"
                aria-label="Cost preview"
                tabIndex={0}
              >
                <table>
                  <thead>
                    <tr>
                      <th>Proposal</th>
                      <th>Outcome</th>
                      <th>Energy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.action_preview.map((p) => (
                      <tr key={describeProposal(p.proposal)}>
                        <td>{describeProposal(p.proposal)}</td>
                        <td>{p.accepted ? "valid" : p.reason}</td>
                        <td>{p.accepted ? `−${p.energy_cost}` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted small-copy">
                Evaluated by the same validator against the current committed
                state; the next tick applies inflow and upkeep first.
              </p>
            </>
          )}
          <h3 className="subheading">
            Latest decision {decision ? `· tick ${decision.tick}` : ""}
          </h3>
          <p className="small-copy" data-testid="organism-outcome">
            {describeOutcome(detail)}
            {decision && (
              <>
                {" "}
                <button
                  className="link-button"
                  onClick={() => onSelectEvent(decision.sequence)}
                >
                  event {decision.sequence}
                </button>
              </>
            )}
          </p>
          {observation && decision?.payload.proposal && (
            <>
              <p className="muted small-copy">
                Proposal received:{" "}
                <code>{JSON.stringify(decision.payload.proposal)}</code>.
                Observation supplied (relative offsets only; this is everything
                it was given):
              </p>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={showLocalView}
                  onChange={(e) => onToggleLocalView(e.target.checked)}
                />
                Show this local view on the grid
              </label>
              <div
                className="change-table"
                role="region"
                aria-label="Supplied observation"
                tabIndex={0}
              >
                <table>
                  <thead>
                    <tr>
                      <th>dx,dy</th>
                      <th>Energy</th>
                      <th>Loose</th>
                      <th>Packed</th>
                      <th>Other?</th>
                    </tr>
                  </thead>
                  <tbody>
                    {observation.cells.map((cell) => (
                      <tr key={`${cell.dx},${cell.dy}`}>
                        <td>
                          {cell.dx},{cell.dy}
                        </td>
                        <td>{cell.energy}</td>
                        <td>{cell.loose_material}</td>
                        <td>{cell.packed_material}</td>
                        <td>{cell.occupied ? "yes" : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted small-copy" data-testid="observation-summary">
                {observation.cells.length} cells within radius {radius}; own
                energy {observation.energy}, age {observation.age}. “
                {observation.life_note}”
              </p>
            </>
          )}
          {detail.inheritance && (
            <InheritancePanel
              birth={detail.inheritance}
              onSelect={onSelect}
              onSelectEvent={onSelectEvent}
            />
          )}
          {detail.profile && <ProfilePanel profile={detail.profile} />}
          {detail.messages && (
            <MessagesPanel organismId={o.id} messages={detail.messages} />
          )}
          {detail.memory && (
            <MemoryInspector
              memory={detail.memory}
              onSelectEvent={onSelectEvent}
            />
          )}
          {detail.events.length > 0 && (
            <>
              <h3 className="subheading">Its recent events</h3>
              <ul className="cell-history">
                {detail.events.slice(0, 8).map((event) => (
                  <li key={event.sequence}>
                    <button
                      className="link-button"
                      onClick={() => onSelectEvent(event.sequence)}
                    >
                      tick {event.tick}: {event.type}
                      {event.summary.reason ? ` (${event.summary.reason})` : ""}
                    </button>
                    <span className="event-rule">{event.rule_id}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}
