import type { MemoryDetail, MemoryRecordView } from "../api";

interface Props {
  memory: MemoryDetail;
  onSelectEvent: (sequence: number) => void;
}

const shortId = (id: string) => id.slice(id.lastIndexOf("-") + 1);

function EventLink({
  sequence,
  onSelectEvent,
}: {
  sequence: number | null | undefined;
  onSelectEvent: (sequence: number) => void;
}) {
  if (sequence === null || sequence === undefined) return <>—</>;
  return (
    <button className="link-button" onClick={() => onSelectEvent(sequence)}>
      {sequence}
    </button>
  );
}

function RecordRows({
  records,
  forgotten,
  onSelectEvent,
}: {
  records: MemoryRecordView[];
  forgotten: boolean;
  onSelectEvent: (sequence: number) => void;
}) {
  return (
    <table>
      <thead>
        <tr>
          <th>Record</th>
          <th>Kind</th>
          <th>Tick</th>
          <th>At</th>
          <th>Text (as stored)</th>
          <th>Bytes</th>
          <th>{forgotten ? "Forgotten" : "Event"}</th>
        </tr>
      </thead>
      <tbody>
        {records.map((m) => (
          <tr key={m.id}>
            <td title={m.id}>{shortId(m.id)}</td>
            <td>{m.kind}</td>
            <td>{m.tick}</td>
            <td>
              ({m.x}, {m.y})
            </td>
            <td className="memory-text">{m.text}</td>
            <td>{m.bytes}</td>
            <td>
              {forgotten ? (
                <>
                  t{m.forgotten_tick} ·{" "}
                  <EventLink
                    sequence={m.forgotten_sequence}
                    onSelectEvent={onSelectEvent}
                  />
                </>
              ) : (
                <EventLink
                  sequence={m.added_sequence}
                  onSelectEvent={onSelectEvent}
                />
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function MemoryInspector({ memory, onSelectEvent }: Props) {
  const { limits } = memory;
  const recall = memory.last_recall;
  const scores = recall?.recall?.scores ?? [];
  return (
    <div data-testid="memory-inspector">
      <h3 className="subheading">Memory</h3>
      <p className="small-copy" data-testid="memory-usage">
        {memory.active_records} / {limits.max_records} records,{" "}
        {memory.active_bytes} / {limits.max_bytes} bytes active;{" "}
        {memory.written_total} written in its life.
      </p>
      <p className="muted small-copy">
        Writing costs {limits.write_fee_per_kib} energy per started KiB; keeping
        costs {limits.upkeep_per_kib} per started KiB every{" "}
        {limits.upkeep_interval} ticks. Oldest records are forgotten first.
        Positions are observer-only: the organism receives offsets from where it
        is.
      </p>
      {recall && (
        <>
          <h4 className="subheading">
            Recalled for its latest decision · tick {recall.tick}
          </h4>
          {scores.length === 0 ? (
            <p className="muted small-copy" data-testid="recall-empty">
              Nothing to recall (no active memory at that decision).
            </p>
          ) : (
            <div
              className="change-table"
              role="region"
              aria-label="Recall scores"
              tabIndex={0}
            >
              <table data-testid="recall-scores">
                <thead>
                  <tr>
                    <th>Record</th>
                    <th>Given?</th>
                    <th>Score</th>
                    <th>Recency</th>
                    <th>Relevance</th>
                    <th>Salience</th>
                  </tr>
                </thead>
                <tbody>
                  {scores.map((s) => (
                    <tr key={s.id} className={s.selected ? "" : "muted"}>
                      <td title={s.id}>{shortId(s.id)}</td>
                      <td>{s.selected ? "yes" : "no"}</td>
                      <td>{s.score.toFixed(3)}</td>
                      <td>{s.recency.toFixed(3)}</td>
                      <td>{s.relevance.toFixed(3)}</td>
                      <td>{s.salience.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {recall.recall && (
            <p className="muted small-copy">
              {recall.recall.version}; query keywords:{" "}
              {recall.recall.query.length
                ? recall.recall.query.join(", ")
                : "none"}
              . Scores are recorded, not recomputed.
            </p>
          )}
        </>
      )}
      <h4 className="subheading">Active records (newest first)</h4>
      {memory.active.length === 0 ? (
        <p className="muted small-copy">No active records.</p>
      ) : (
        <div
          className="change-table"
          role="region"
          aria-label="Active memory"
          tabIndex={0}
        >
          <RecordRows
            records={memory.active}
            forgotten={false}
            onSelectEvent={onSelectEvent}
          />
        </div>
      )}
      {memory.forgotten.length > 0 && (
        <details className="prompt-details" data-testid="forgotten-memory">
          <summary>
            Forgotten records ({memory.forgotten.length}) — observer only; the
            organism can no longer recall these
          </summary>
          <div
            className="change-table"
            role="region"
            aria-label="Forgotten memory"
            tabIndex={0}
          >
            <RecordRows
              records={memory.forgotten}
              forgotten
              onSelectEvent={onSelectEvent}
            />
          </div>
        </details>
      )}
    </div>
  );
}
