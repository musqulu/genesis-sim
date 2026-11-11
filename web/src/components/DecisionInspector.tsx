import { useEffect, useState } from "react";
import { ApiError, requestDecision, requestDecisions } from "../api";
import type { DecisionRecord } from "../api";

interface Props {
  runId: string;
  organismId: string | null;
  refreshKey: number;
  maxTick?: number;
  onSelectEvent: (sequence: number) => void;
}

function outcomeLabel(d: DecisionRecord): string {
  if (d.outcome === "accepted") return `accepted (${d.outcome_rule})`;
  if (d.outcome === "unparsed") return `unparsed: ${d.parse_status}`;
  return `rejected: ${d.outcome_reason}`;
}

const unknown = (value: number | null | undefined) =>
  value === null || value === undefined ? "unknown" : String(value);

export default function DecisionInspector({
  runId,
  organismId,
  refreshKey,
  maxTick,
  onSelectEvent,
}: Props) {
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<DecisionRecord | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setSelected(null);
    setDetail(null);
  }, [runId, organismId]);

  useEffect(() => {
    if (!organismId) return setDecisions([]);
    let cancelled = false;
    requestDecisions(organismId, maxTick)
      .then((page) => {
        if (!cancelled && page.run_id === runId) setDecisions(page.decisions);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [runId, organismId, refreshKey, maxTick]);

  const current = selected ?? decisions[0]?.request_id ?? null;
  useEffect(() => {
    if (!current) return setDetail(null);
    let cancelled = false;
    requestDecision(current)
      .then((next) => {
        if (!cancelled) {
          setDetail(next);
          setError("");
        }
      })
      .catch((caught) => {
        if (!cancelled)
          setError(
            caught instanceof ApiError
              ? caught.message
              : "Could not load decision.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [current]);

  if (!organismId) return null;
  const ctx = detail?.request?.context;
  return (
    <section className="decisions panel" aria-labelledby="decisions-title">
      <div className="panel-heading">
        <h2 id="decisions-title">Decision inspector</h2>
        <span className="eyebrow">{organismId}</span>
      </div>
      {decisions.length === 0 ? (
        <p className="muted small-copy">No consumed decision requests yet.</p>
      ) : (
        <div className="decisions-body">
          <ol className="decision-items" aria-label="Decision attempts">
            {decisions.map((d) => (
              <li key={d.request_id}>
                <button
                  className={`event-item ${d.request_id === current ? "selected" : ""}`}
                  aria-pressed={d.request_id === current}
                  onClick={() => setSelected(d.request_id)}
                >
                  <span className="event-tick">tick {d.tick}</span>
                  <span className={`decision-outcome ${d.outcome}`}>
                    {outcomeLabel(d)}
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className="decision-detail" data-testid="decision-detail">
            {error && (
              <p role="alert" className="error-text small-copy">
                {error}
              </p>
            )}
            {detail && (
              <>
                <dl className="properties">
                  <div>
                    <dt>Request</dt>
                    <dd>{detail.request_id}</dd>
                  </div>
                  <div className="stacked">
                    <dt>Input hash (binds the response)</dt>
                    <dd className="hash-value">{detail.input_hash}</dd>
                  </div>
                  <div>
                    <dt>Provider</dt>
                    <dd>
                      {String(detail.provider.kind)}:{" "}
                      {String(detail.provider.name)}
                    </dd>
                  </div>
                  <div>
                    <dt>Previous result given</dt>
                    <dd>
                      {ctx?.previous_result
                        ? JSON.stringify(ctx.previous_result)
                        : "none"}
                    </dd>
                  </div>
                  <div>
                    <dt>Observation given</dt>
                    <dd>
                      {ctx
                        ? `${ctx.observation.cells.length} cells, energy ${ctx.observation.energy}, age ${ctx.observation.age}`
                        : "—"}
                    </dd>
                  </div>
                  {ctx?.recalled && (
                    <div>
                      <dt>Memories recalled</dt>
                      <dd data-testid="recalled-count">
                        {ctx.recalled.length} of{" "}
                        {ctx.memory?.active_records ?? "?"} active
                      </dd>
                    </div>
                  )}
                </dl>
                {ctx?.inbox && ctx.inbox.length > 0 && (
                  <ul className="cell-history" data-testid="inbox-given">
                    {ctx.inbox.map((m) => (
                      <li key={m.id}>
                        <span>
                          inbox · {m.kind === "transfer" ? "gift" : "message"}{" "}
                          from {m.from}, {m.ticks_ago} ticks ago — “{m.text}”
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {ctx?.recalled && ctx.recalled.length > 0 && (
                  <ul className="cell-history" data-testid="recalled-given">
                    {ctx.recalled.map((m) => (
                      <li key={m.id}>
                        <span>
                          {m.id}: {m.kind}, {m.ticks_ago} ticks ago at offset (
                          {m.dx}, {m.dy}) — “{m.text}”
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <h3 className="subheading">1 · Raw output (verbatim text)</h3>
                <pre className="raw-output" data-testid="raw-output">
                  {detail.raw_output === "" ? "(empty)" : detail.raw_output}
                </pre>
                <h3 className="subheading">2 · Parse</h3>
                <p className="small-copy" data-testid="parse-status">
                  {detail.parse_status}
                  {detail.parse_error ? ` — ${detail.parse_error}` : ""}
                </p>
                <h3 className="subheading">3 · Parsed proposal</h3>
                <p className="small-copy" data-testid="parsed-proposal">
                  {detail.proposal ? (
                    <code>{JSON.stringify(detail.proposal)}</code>
                  ) : (
                    "none"
                  )}
                </p>
                <h3 className="subheading">4 · Engine outcome</h3>
                <p className="small-copy" data-testid="decision-outcome">
                  {outcomeLabel(detail)}{" "}
                  <button
                    className="link-button"
                    onClick={() => onSelectEvent(detail.event_sequence)}
                  >
                    event {detail.event_sequence}
                  </button>
                </p>
                {detail.prompt && (
                  <details className="prompt-details">
                    <summary>
                      Exact prompt sent (
                      {String(detail.detail?.prompt_version ?? "")})
                    </summary>
                    {detail.prompt.map((m, i) => (
                      <div key={i}>
                        <h4 className="subheading">{m.role}</h4>
                        <pre className="raw-output">{m.content}</pre>
                      </div>
                    ))}
                    <pre
                      className="raw-output"
                      data-testid="inference-settings"
                    >
                      {JSON.stringify(
                        {
                          sampling: detail.detail?.sampling,
                          limits: detail.detail?.limits,
                          runtime: detail.detail?.runtime,
                          finish_reason: detail.usage.finish_reason,
                        },
                        null,
                        1,
                      )}
                    </pre>
                  </details>
                )}
                <p className="muted small-copy">
                  Tokens in/out: {unknown(detail.usage.input_tokens)} /{" "}
                  {unknown(detail.usage.output_tokens)}; latency{" "}
                  {unknown(detail.usage.latency_ms)} ms
                  {detail.usage.reused
                    ? "; response reused after recovery"
                    : ""}
                  . Raw output is shown as text, never rendered.
                </p>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
