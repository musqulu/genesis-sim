import { useEffect, useState } from "react";
import { ApiError, createBackup, requestBackups, requestHealth } from "../api";
import type { BackupInfo, HealthReport } from "../api";

interface Props {
  refreshKey: string;
}

const gb = (bytes: number) => `${(bytes / 2 ** 30).toFixed(1)} GB`;
const mb = (bytes: number) => `${(bytes / 2 ** 20).toFixed(0)} MB`;

/** Technical health (M15): storage, durability, inference and recovery notes. */
export default function HealthPanel({ refreshKey }: Props) {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [backups, setBackups] = useState<BackupInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    let cancelled = false;
    requestHealth()
      .then((next) => {
        if (!cancelled && next.report) setReport(next.report);
      })
      .catch(() => undefined);
    requestBackups()
      .then((next) => {
        if (!cancelled) setBackups(next.backups);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const backup = async () => {
    setBusy(true);
    setNote("");
    try {
      const made = await createBackup();
      setNote(
        made.ok
          ? `Backup verified: integrity ${made.integrity}, ${made.runs.length} run(s) replayed to their recorded hash.`
          : `Backup written but NOT verified: ${made.failures.map((f) => f.error).join("; ") || made.integrity}`,
      );
      setBackups((await requestBackups()).backups);
    } catch (caught) {
      setNote(caught instanceof ApiError ? caught.message : "Backup failed.");
    } finally {
      setBusy(false);
    }
  };

  if (!report) return null;
  const m = report.metrics;
  return (
    <section className="health panel" aria-labelledby="health-title">
      <div className="panel-heading">
        <h2 id="health-title">Technical health</h2>
        <span
          className={`eyebrow health-${report.status}`}
          data-testid="health-status"
        >
          {report.status === "ok"
            ? "OK"
            : `ATTENTION · ${report.problems.join(", ")}`}
        </span>
      </div>
      <dl className="properties" data-testid="health-report">
        <div>
          <dt>Durable tick / live tick</dt>
          <dd>
            {report.durable_tick ?? "—"} / {report.live_tick}
          </dd>
        </div>
        <div>
          <dt>Decision barrier</dt>
          <dd>
            {report.pending
              ? `tick ${report.pending.tick}: ${report.pending.completed}/${report.pending.total} responses`
              : "idle"}
            {report.queue
              ? ` · queue ≤ ${report.queue.capacity} (max seen ${report.queue.max_depth_seen})`
              : ""}
          </dd>
        </div>
        <div>
          <dt>Model worker</dt>
          <dd>
            {report.provider ? String(report.provider.status ?? "—") : "none"}
          </dd>
        </div>
        <div>
          <dt>Inference</dt>
          <dd>
            {m.decisions} decisions · {m.unparsed} unparsed · median{" "}
            {m.latency_ms_median === null
              ? "—"
              : `${(m.latency_ms_median / 1000).toFixed(2)} s`}{" "}
            · {m.provider_failures} provider failures
          </dd>
        </div>
        <div>
          <dt>Ledger</dt>
          <dd>
            {report.invariants.ledger_balanced ? "balanced" : "IMBALANCE"}
          </dd>
        </div>
        <div>
          <dt>Disk</dt>
          <dd className={report.disk.ok ? "" : "error-text"}>
            {gb(report.disk.free_bytes)} free (minimum{" "}
            {mb(report.disk.min_free_bytes)}) · database{" "}
            {mb(report.disk.database_bytes)}
          </dd>
        </div>
      </dl>
      <button className="button" disabled={busy} onClick={() => void backup()}>
        {busy ? "Backing up and verifying…" : "Back up & verify"}
      </button>
      {note && (
        <p className="small-copy" data-testid="backup-note">
          {note}
        </p>
      )}
      {backups.length > 0 && (
        <p className="muted small-copy" data-testid="backup-list">
          Backups:{" "}
          {backups
            .slice(0, 5)
            .map(
              (b) =>
                `${b.name} (${mb(b.bytes)}, ${b.verified_ok ? "verified" : "unverified"})`,
            )
            .join(" · ")}
        </p>
      )}
      <h3 className="subheading">Technical notes (newest first)</h3>
      <ul className="cell-history" aria-label="Technical notes">
        {report.technical_log
          .slice()
          .reverse()
          .slice(0, 10)
          .map((e) => (
            <li key={`${e.at}-${e.kind}-${e.subject}`}>
              <span>
                {e.kind} · {e.subject}
              </span>
              <span className="event-rule">{e.detail}</span>
            </li>
          ))}
      </ul>
      <p className="muted small-copy">
        Technical recovery pauses logical time; it never changes the world,
        rescues organisms or reseeds an extinct run.
      </p>
    </section>
  );
}
