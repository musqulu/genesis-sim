import type { Interaction } from "../api";

interface Props {
  exchanges: (Interaction & { tick: number; age: number })[];
}

/** Caption over the stage: the latest gifts and messages (display only, text as sent). */
export default function ExchangeFeed({ exchanges }: Props) {
  if (exchanges.length === 0) return null;
  const latest = [...exchanges].reverse().slice(0, 4);
  return (
    <ol className="exchange-feed" aria-label="Recent exchanges">
      {latest.map((e, i) => (
        <li
          key={`${e.tick}-${e.from_id}-${e.to_id}-${i}`}
          className={e.kind}
          style={{ opacity: Math.max(0.35, 1 - e.age / 8) }}
        >
          <span className="exchange-tick">t{e.tick}</span>{" "}
          <strong>{e.from_id}</strong> → <strong>{e.to_id}</strong>
          {e.kind === "transfer" ? <> gave {e.detail}</> : <>: “{e.detail}”</>}
        </li>
      ))}
    </ol>
  );
}
