import type { OrganismDetail, OrganismView } from "../api";

interface Props {
  organism: OrganismView;
  model?: string | null; // its own model in a mixed-model run
  detail: OrganismDetail | null;
  x: number;
  y: number;
}

function lastAction(detail: OrganismDetail | null): string | null {
  const d = detail?.latest_decision;
  if (!d) return null;
  const s = d.summary;
  if (d.type === "action_rejected")
    return d.rule_id === "cognition.parse.v1"
      ? `reply unparsed (${s.reason})`
      : `tried ${s.action}, rejected (${s.reason})`;
  if (s.action === "move") return `moved ${s.direction}`;
  if (s.action === "extract") return `extracted ${s.amount} ${s.resource}`;
  if (s.action === "modify") return `${s.mode}ed ${s.amount} material`;
  return "waited";
}

/** Hover details for an organism on the board (display only). */
export default function OrganismTooltip({
  organism: o,
  model,
  detail,
  x,
  y,
}: Props) {
  const action = lastAction(detail);
  const explanation =
    typeof detail?.latest_decision?.payload?.explanation === "string"
      ? detail.latest_decision.payload.explanation
      : null;
  return (
    <div
      className="organism-tooltip"
      role="tooltip"
      data-testid="organism-tooltip"
      style={{ left: x + 14, top: y + 14 }}
    >
      <strong>{o.id}</strong> <span className="muted">{o.label}</span>
      <dl>
        {model && (
          <div>
            <dt>Model</dt>
            <dd>{model}</dd>
          </div>
        )}
        <div>
          <dt>Energy</dt>
          <dd>
            {o.energy} / {o.energy_capacity}
          </dd>
        </div>
        <div>
          <dt>Carried</dt>
          <dd>
            {o.carried_material} / {o.material_capacity}
          </dd>
        </div>
        <div>
          <dt>Age</dt>
          <dd>{o.age}</dd>
        </div>
        <div>
          <dt>Position</dt>
          <dd>
            ({o.x}, {o.y})
          </dd>
        </div>
      </dl>
      {action && (
        <p className="tooltip-action">
          Last: {action}
          {detail?.latest_decision
            ? ` · tick ${detail.latest_decision.tick}`
            : ""}
        </p>
      )}
      {explanation && <p className="tooltip-quote">“{explanation}”</p>}
    </div>
  );
}
