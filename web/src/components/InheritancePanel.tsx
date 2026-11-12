import type { BirthRecord } from "../api";

interface Props {
  birth: BirthRecord;
  onSelect: (id: string) => void;
  onSelectEvent: (sequence: number) => void;
}

const NAMES = [
  "maintenance",
  "caution",
  "curiosity",
  "affiliation",
  "experimentation",
  "transmission",
];
const shortId = (id: string) => id.slice(id.lastIndexOf("-") + 1);

/** What this organism received at birth: the logged draws, not a reconstruction. */
export default function InheritancePanel({
  birth,
  onSelect,
  onSelectEvent,
}: Props) {
  const log = birth.inheritance;
  const memories = log?.memories;
  return (
    <div data-testid="inheritance-panel">
      <h3 className="subheading">Inheritance</h3>
      <p className="small-copy">
        Born at tick {birth.tick} to{" "}
        <button
          className="link-button"
          onClick={() => onSelect(birth.parent_id)}
        >
          {birth.parent_id}
        </button>{" "}
        (generation {birth.generation}) ·{" "}
        <button
          className="link-button"
          onClick={() => onSelectEvent(birth.event_sequence)}
        >
          event {birth.event_sequence}
        </button>
      </p>
      {log?.mask && log.parent && log.child && (
        <div
          className="change-table"
          role="region"
          aria-label="Inherited tendencies"
          tabIndex={0}
        >
          <table>
            <thead>
              <tr>
                <th>Tendency</th>
                <th>Parent</th>
                <th>Child</th>
                <th>How</th>
              </tr>
            </thead>
            <tbody>
              {NAMES.map((name, i) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td>{log.parent![i]}</td>
                  <td>{log.child![i]}</td>
                  <td>{log.mask![i] ? "inherited ±" : "fresh draw"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {memories && (
        <p className="small-copy" data-testid="inherited-memories">
          {memories.selected.length === 0
            ? "Its parent passed on no memories."
            : `Parent selected ${memories.selected.map(shortId).join(", ")}: kept ${
                memories.retained.map(shortId).join(", ") || "none"
              }; dropped ${memories.dropped.map(shortId).join(", ") || "none"}.`}
        </p>
      )}
      <p className="muted small-copy">
        Inherited memories are copies of the parent&apos;s claims about its own
        past; the parent&apos;s records are unchanged.
      </p>
    </div>
  );
}
