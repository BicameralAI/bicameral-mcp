import type { NeedsAttentionItem } from "../../types";
import { pulseSignerLabel } from "./format";

interface NeedsAttentionSectionProps {
  items: NeedsAttentionItem[];
}

// Needs Attention — decisions awaiting signoff. Ported from M1.
//
// XSS: `kind`, `summary`, `signer`, `decision_id` are all user-sourced.
// `kind` and `summary` and the signer label are JSX text children; the
// `decision_id` is a JSX prop (data-decision-id). Preact escapes both
// contexts — no dangerouslySetInnerHTML, no string-built DOM.
export function NeedsAttentionSection({ items }: NeedsAttentionSectionProps) {
  const list = items ?? [];
  return (
    <section class="pulse-section">
      <div class="pulse-section-name">Needs Attention</div>
      {list.length === 0 ? (
        <div class="pulse-empty">Nothing awaiting attention.</div>
      ) : (
        list.map((it, i) => {
          const kind = String(it.kind || "").replace(/_/g, " ");
          const signer = pulseSignerLabel(it.signer);
          return (
            <div
              class="pulse-row"
              key={`${it.decision_id}-${i}`}
              data-decision-id={it.decision_id}
            >
              <span class="pulse-row-kind">{kind}</span>
              <span class="pulse-row-summary">{it.summary}</span>
              {signer ? (
                <span class="pulse-row-meta">{signer}</span>
              ) : null}
            </div>
          );
        })
      )}
    </section>
  );
}
