import type { OrganismDetail } from "../api";
import TendencyBars from "./TendencyBars";

interface Props {
  profile: NonNullable<OrganismDetail["profile"]>;
}

/** Starting tendencies beside what the organism actually did (recorded events only). */
export default function ProfilePanel({ profile }: Props) {
  const s = profile.stats;
  return (
    <div data-testid="profile-panel">
      <h3 className="subheading">
        Tendencies ({profile.tendency_mode}) and behaviour
      </h3>
      {profile.tendencies ? (
        <TendencyBars tendencies={profile.tendencies} />
      ) : (
        <p className="muted small-copy">No tendencies in this run.</p>
      )}
      {s && (
        <p className="small-copy" data-testid="profile-stats">
          {s.decisions} decisions: {s.accepted} accepted, {s.rejected} rejected,{" "}
          {s.unparsed} unparsed ·{" "}
          {Object.entries(s.actions)
            .map(([a, n]) => `${a} ${n}`)
            .join(", ") || "no accepted actions"}{" "}
          · {s.cells_visited} cells visited · extracted {s.energy_extracted}{" "}
          energy, {s.material_extracted} material · gifts {s.gifts_given} given
          / {s.gifts_received} received · messages {s.messages_sent} sent /{" "}
          {s.messages_received} received
        </p>
      )}
      <p className="muted small-copy">
        Tendencies are inclinations stated in its own prompt only; other
        organisms never see them. Counts describe actions, not intentions.
      </p>
    </div>
  );
}
