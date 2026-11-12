import type { OrganismView } from "../api";

interface Props {
  organisms: OrganismView[];
  tick: number;
  selected: string | null;
  onSelect: (id: string) => void;
}

/** Parent/child tree of every organism that existed by this tick (seeds are roots). */
export default function GenealogyPanel({
  organisms,
  tick,
  selected,
  onSelect,
}: Props) {
  if (organisms.length === 0) return null;
  const children = new Map<string, OrganismView[]>();
  for (const o of organisms)
    for (const parent of o.parent_ids)
      children.set(parent, [...(children.get(parent) ?? []), o]);
  const roots = organisms.filter(
    (o) =>
      o.parent_ids.length === 0 ||
      !organisms.some((p) => p.id === o.parent_ids[0]),
  );
  const deepest = Math.max(...organisms.map((o) => o.generation));
  const node = (o: OrganismView) => (
    <li key={o.id}>
      <button
        className={`link-button ${o.id === selected ? "selected-node" : ""}`}
        aria-pressed={o.id === selected}
        onClick={() => onSelect(o.id)}
      >
        {o.id}
      </button>{" "}
      <span className="muted">
        gen {o.generation} · t{o.birth_tick}–
        {o.alive ? "" : `t${o.death_tick} † ${o.death_cause}`}
      </span>
      {children.has(o.id) && <ul>{children.get(o.id)!.map(node)}</ul>}
    </li>
  );
  return (
    <section className="genealogy panel" aria-labelledby="genealogy-title">
      <div className="panel-heading">
        <h2 id="genealogy-title">Genealogy</h2>
        <span className="eyebrow">
          TICK {tick} · {organisms.length} LIVES · DEEPEST GEN {deepest}
        </span>
      </div>
      <ul className="genealogy-tree" aria-label="Genealogy tree">
        {roots.map(node)}
      </ul>
      <p className="muted small-copy">
        Every organism that existed by this tick. Children are listed under the
        parent that chose to reproduce.
      </p>
    </section>
  );
}
