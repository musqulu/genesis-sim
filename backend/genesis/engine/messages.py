"""Inbox rules (state schema 8): delivery, expiry and reading of items between organisms.

An item sent at tick t (a `communicate` message, or the receipt of a `transfer`) is
`pending` until tick t+1, when it is delivered into the recipient's inbox, before anyone
observes. A delivered item is shown at the recipient's next decision and is then read:
it leaves the inbox and (with memory) becomes a paid `message` memory record. Unread items
expire `message_ttl` ticks after delivery. Items for a dead recipient are removed. Every
removal keeps the full record in history; the engine never interprets message text.
"""

from dataclasses import dataclass

from genesis.domain.events import (
    DELIVERY_RULE,
    MESSAGES_DELIVERED,
    MESSAGES_READ,
    READ_RULE,
    MessageChange,
    WorldEvent,
)
from genesis.domain.state import Message, World


def enabled(world: World) -> bool:
    return world.schema >= 8 and world.life is not None and world.life.exchange_enabled


@dataclass(frozen=True, slots=True)
class InboxView:
    """What a recipient may see of one delivered item."""

    id: str
    kind: str
    sender_id: str
    ticks_ago: int  # since it was sent
    text: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "from": self.sender_id,
            "ticks_ago": self.ticks_ago,
            "text": self.text,
        }


def unread(world: World, organism_id: str) -> tuple[Message, ...]:
    return tuple(m for m in world.inbox_of(organism_id) if m.status == "delivered")


def views(world: World, organism_id: str) -> tuple[InboxView, ...]:
    return tuple(
        InboxView(m.id, m.kind, m.sender_id, world.tick - m.sent_tick, m.text)
        for m in unread(world, organism_id)
    )


def delivery_event(world: World) -> WorldEvent | None:
    """Deliver items sent before this tick; expire old unread ones; drop items whose
    recipient has died. One environmental event per tick, only if something changed."""
    ttl = world.life.message_ttl
    alive = {o.id for o in world.living}
    changes: list[MessageChange] = []
    counts = {"delivered": 0, "expired": 0, "recipient_dead": 0}
    for message in world.messages:
        record = message.to_dict()
        if message.recipient_id not in alive:
            changes.append(MessageChange("remove", record, "recipient_dead"))
            counts["recipient_dead"] += 1
        elif message.status == "pending" and message.sent_tick < world.tick:
            delivered = {**record, "status": "delivered", "delivered_tick": world.tick}
            changes += [
                MessageChange("remove", record, "delivered"),
                MessageChange("add", delivered),
            ]
            counts["delivered"] += 1
        elif message.status == "delivered" and world.tick - message.delivered_tick >= ttl:
            changes.append(MessageChange("remove", record, "expired"))
            counts["expired"] += 1
    if not changes:
        return None
    return WorldEvent(
        tick=world.tick,
        phase="environment",
        type=MESSAGES_DELIVERED,
        rule_id=DELIVERY_RULE,
        cause="environment",
        summary=counts,
        message_changes=tuple(changes),
    )


def read_event(world: World, organism_id: str, shown: tuple[InboxView, ...]) -> WorldEvent | None:
    """Items shown at a decision leave the inbox once that decision is resolved."""
    present = {m.id: m for m in world.messages}
    removed = [present[v.id] for v in shown if v.id in present]
    if not removed:
        return None
    return WorldEvent(
        tick=world.tick,
        phase="memory",
        type=MESSAGES_READ,
        rule_id=READ_RULE,
        cause="organism",
        actor_id=organism_id,
        summary={"read": [m.id for m in removed], "from": [m.sender_id for m in removed]},
        message_changes=tuple(MessageChange("remove", m.to_dict(), "read") for m in removed),
    )
