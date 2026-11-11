import { useEffect, useState } from "react";
import { requestPopulation } from "../api";
import type { PopulationInfo } from "../api";
import TendencyBars from "./TendencyBars";

interface Props {
  runId: string;
  refreshKey: number;
  maxTick?: number;
  selected: string | null;
  onSelect: (id: string) => void;
}

/** Compare starting tendencies with observed behaviour across the population (M10). */
export default function PopulationPanel({
  runId,
  refreshKey,
  maxTick,
  selected,
  onSelect,
}: Props) {
  const [info, setInfo] = useState<PopulationInfo | null>(null);

  useEffect(() => {
    let cancelled = false;
    requestPopulation(maxTick)
      .then((next) => {
        if (!cancelled && next.run_id === runId) setInfo(next);
      })
      .catch(() => undefined); // an older backend has no population view
    return () => {
      cancelled = true;
    };
  }, [runId, refreshKey, maxTick]);

  if (!info || info.organisms.length === 0) return null;
  return (
    <section className="population panel" aria-labelledby="population-title">
      <div className="panel-heading">
        <h2 id="population-title">Population</h2>
        <span className="eyebrow">
          TENDENCIES {String(info.tendency_mode ?? "none").toUpperCase()} · TICK{" "}
          {info.tick}
        </span>
      </div>
      <div
        className="change-table"
        role="region"
        aria-label="Population comparison"
        tabIndex={0}
      >
        <table>
          <thead>
            <tr>
              <th>Organism</th>
              <th title={info.tendency_names.join(", ")}>
                Tendencies ({info.tendency_names.map((n) => n[0]).join("")})
              </th>
              <th>Decisions</th>
              <th>Moves</th>
              <th>Cells</th>
              <th>Extracted E/M</th>
              <th>Gifts out/in</th>
              <th>Msgs out/in</th>
            </tr>
          </thead>
          <tbody>
            {info.organisms.map((o) => {
              const s = o.stats;
              return (
                <tr
                  key={o.id}
                  className={`${o.alive ? "" : "muted"} ${o.id === selected ? "selected-row" : ""}`}
                >
                  <td>
                    <button
                      className="link-button"
                      onClick={() => onSelect(o.id)}
                    >
                      {o.id}
                    </button>
                    {o.alive ? "" : ` † t${o.death_tick}`}
                  </td>
                  <td>
                    {o.tendencies ? (
                      <TendencyBars tendencies={o.tendencies} compact />
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{s ? `${s.accepted}/${s.decisions}` : "—"}</td>
                  <td>{s?.actions.move ?? 0}</td>
                  <td>{s?.cells_visited ?? 0}</td>
                  <td>
                    {s ? `${s.energy_extracted}/${s.material_extracted}` : "—"}
                  </td>
                  <td>{s ? `${s.gifts_given}/${s.gifts_received}` : "—"}</td>
                  <td>
                    {s ? `${s.messages_sent}/${s.messages_received}` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small-copy">
        Columns M C A E T M order: maintenance, caution, curiosity, affiliation,
        experimentation, transmission. Decisions show accepted/total. Counts are
        from recorded events; an absence of difference is a valid result.
      </p>
    </section>
  );
}
