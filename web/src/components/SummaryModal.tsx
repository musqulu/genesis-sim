import type { ReactNode } from "react";

interface Props {
  markdown: string;
  path: string;
  onClose: () => void;
}

/** Inline `code` spans; everything is rendered as text, never as HTML. */
function inline(text: string): ReactNode[] {
  return text
    .split(/(`[^`]+`)/)
    .map((part, i) =>
      part.startsWith("`") && part.endsWith("`") && part.length > 1 ? (
        <code key={i}>{part.slice(1, -1)}</code>
      ) : (
        part
      ),
    );
}

/** A deliberately tiny Markdown subset: headings, quotes, list items, rules, paragraphs. */
export function renderSummary(markdown: string): ReactNode[] {
  const out: ReactNode[] = [];
  let list: ReactNode[] = [];
  const flush = () => {
    if (list.length) out.push(<ul key={`ul-${out.length}`}>{list}</ul>);
    list = [];
  };
  markdown.split("\n").forEach((line, i) => {
    if (line.startsWith("- ")) {
      list.push(<li key={i}>{inline(line.slice(2))}</li>);
      return;
    }
    flush();
    if (!line.trim()) return;
    if (line.startsWith("### "))
      out.push(<h5 key={i}>{inline(line.slice(4))}</h5>);
    else if (line.startsWith("## "))
      out.push(<h4 key={i}>{inline(line.slice(3))}</h4>);
    else if (line.startsWith("# "))
      out.push(<h3 key={i}>{inline(line.slice(2))}</h3>);
    else if (line.startsWith("> "))
      out.push(<blockquote key={i}>{inline(line.slice(2))}</blockquote>);
    else if (line.trim() === "---") out.push(<hr key={i} />);
    else out.push(<p key={i}>{inline(line)}</p>);
  });
  flush();
  return out;
}

/** The plain-English run summary (facts from recorded history), as saved to disk. */
export default function SummaryModal({ markdown, path, onClose }: Props) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section
        className="summary-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Run summary"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dashboard-heading">
          <h2>Run summary</h2>
          <button className="button small" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="summary-body" data-testid="summary-text">
          {renderSummary(markdown)}
        </div>
        <p className="muted small-copy" data-testid="summary-path">
          Saved as {path}
        </p>
      </section>
    </div>
  );
}
