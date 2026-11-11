import type { MessagesDetail, MessageRecord } from "../api";

interface Props {
  organismId: string;
  messages: MessagesDetail;
}

const STATE: Record<string, string> = {
  pending: "sent, arrives next tick",
  delivered: "in inbox (unread)",
  read: "read",
  expired: "expired unread",
  recipient_dead: "undeliverable (recipient died)",
};

function describe(m: MessageRecord, self: string): string {
  const other =
    m.sender_id === self ? `to ${m.recipient_id}` : `from ${m.sender_id}`;
  const what = m.kind === "transfer" ? `gift of ${m.text}` : `“${m.text}”`;
  return `${other}: ${what}`;
}

/** Observer view of an organism's inbox and recent exchanges (text shown, never run). */
export default function MessagesPanel({ organismId, messages }: Props) {
  const unread = messages.inbox.filter((m) => m.kind === "message").length;
  return (
    <div data-testid="messages-panel">
      <h3 className="subheading">Exchanges</h3>
      <p className="small-copy" data-testid="inbox-usage">
        Inbox: {messages.inbox.length} item(s), {unread} /{" "}
        {messages.inbox_capacity} message slots used.
      </p>
      {messages.recent.length === 0 ? (
        <p className="muted small-copy">
          No gifts or messages sent or received yet.
        </p>
      ) : (
        <ul className="cell-history" data-testid="message-history">
          {messages.recent.map((m) => (
            <li key={m.id}>
              <span>
                t{m.sent_tick} · {describe(m, organismId)}
              </span>
              <span className="event-rule">
                {STATE[m.state] ?? m.state}
                {m.ended_tick !== undefined ? ` (t${m.ended_tick})` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small-copy">
        Messages are claims by their senders; the engine never interprets them.
      </p>
    </div>
  );
}
