interface Props {
  tendencies: Record<string, number>;
  compact?: boolean;
}

/** Six starting tendencies, 0–100, as labelled bars (display only). */
export default function TendencyBars({ tendencies, compact = false }: Props) {
  return (
    <ul className={`tendency-bars ${compact ? "compact" : ""}`}>
      {Object.entries(tendencies).map(([name, value]) => (
        <li key={name} title={`${name}: ${value} / 100`}>
          {!compact && <span className="tendency-name">{name}</span>}
          <span className="tendency-track">
            <span className="tendency-fill" style={{ width: `${value}%` }} />
          </span>
          <span className="tendency-value">{value}</span>
        </li>
      ))}
    </ul>
  );
}
