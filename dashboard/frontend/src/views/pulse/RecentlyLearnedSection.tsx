import type { LearnedItem } from "../../types";
import { fmtDate } from "./format";

interface RecentlyLearnedSectionProps {
  items: LearnedItem[];
}

// Recently Learned — a dated activity feed of decisions the ledger has
// absorbed. Each row carries its `date` rendered through fmtDate.
//
// XSS: `summary`, `source_type`, `source_ref`, `decision_id`, `date` are
// all user-sourced. Summary/source bits/date are JSX text children;
// decision_id is a JSX prop. Preact escapes both — fmtDate also normalises
// the date to a fixed YYYY-MM-DD shape before it ever reaches the DOM.
export function RecentlyLearnedSection({
  items,
}: RecentlyLearnedSectionProps) {
  const list = items ?? [];
  return (
    <section class="pulse-section">
      <div class="pulse-section-name">Recently Learned</div>
      {list.length === 0 ? (
        <div class="pulse-empty">No decisions recorded yet.</div>
      ) : (
        <div class="pulse-feed">
          {list.map((it, i) => {
            const srcBits = [it.source_type, it.source_ref]
              .filter(Boolean)
              .join(" · ");
            return (
              <div
                class="pulse-feed-item"
                key={`${it.decision_id}-${i}`}
                data-decision-id={it.decision_id}
              >
                <span class="pulse-feed-date">
                  {it.date ? fmtDate(it.date) : "—"}
                </span>
                <div class="pulse-feed-body">
                  <span class="pulse-row-summary">{it.summary}</span>
                  {srcBits ? (
                    <span class="pulse-row-src">{srcBits}</span>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
