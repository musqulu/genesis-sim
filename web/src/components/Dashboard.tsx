import type { ReactNode } from "react";

interface Props {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}

/** The detailed inspectors, kept intact but out of the way of the visualization. */
export default function Dashboard({ open, onClose, children }: Props) {
  if (!open) return null;
  return (
    <aside className="dashboard" id="dashboard" aria-label="Dashboard">
      <div className="dashboard-heading">
        <h2>Dashboard</h2>
        <button className="button small" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="dashboard-body">{children}</div>
    </aside>
  );
}
