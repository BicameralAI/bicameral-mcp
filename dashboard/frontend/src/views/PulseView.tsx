import { useEffect, useState } from "preact/hooks";
import { fetchPulse } from "../api";
import {
  isPulseError,
  type LearnedItem,
  type NeedsAttentionItem,
  type ProjectPulseSummary,
  type PulseHealth,
  type PulseResponse,
} from "../types";

// ── XSS discipline (load-bearing) ───────────────────────────────
// This is the native Preact port of the #437 legacy renderPulse view.
// Every user-sourced field — summary, source_ref, source_type, signer,
// decision_id, suggested_next_move, error — is rendered as a JSX text
// child or prop, which Preact auto-escapes. There is NO
// dangerouslySetInnerHTML and NO string-concatenated DOM anywhere here.

/** Mirror the CLI renderer: show only the local-part of a signer email. */
function pulseSignerLabel(signer: string | null): string {
  if (!signer) return "";
  const s = String(signer);
  const at = s.indexOf("@");
  return at > 0 ? s.slice(0, at) : s;
}

/** Fixed-shape date string (derived, never raw user HTML). */
function fmtDate(value: string | null): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toISOString().slice(0, 10);
}

function HealthSection({ health }: { health: PulseHealth }) {
  const h = health ?? ({} as PulseHealth);
  const stat = (num: number, label: string, drift = false) => (
    <div class="pulse-stat">
      <span class={"pulse-stat-num" + (drift ? " drift" : "")}>
        {num ?? 0}
      </span>
      <span class="pulse-stat-label">{label}</span>
    </div>
  );
  return (
    <section class="pulse-section">
      <div class="pulse-section-name">Health</div>
      <div class="pulse-health">
        {stat(h.decisions_reflected, "reflected")}
        {stat(h.decisions_drifted, "drifted", (h.decisions_drifted || 0) > 0)}
        {stat(h.decisions_pending, "pending")}
        {stat(h.decisions_ungrounded, "ungrounded")}
        {stat(
          h.drifted_regions,
          "drifted regions",
          (h.drifted_regions || 0) > 0,
        )}
      </div>
      <div class="pulse-sync">
        Last sync: {h.last_sync ? fmtDate(h.last_sync) : "never"}
      </div>
    </section>
  );
}

function NeedsAttentionSection({ items }: { items: NeedsAttentionItem[] }) {
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

function RecentlyLearnedSection({ items }: { items: LearnedItem[] }) {
  const list = items ?? [];
  return (
    <section class="pulse-section">
      <div class="pulse-section-name">Recently Learned</div>
      {list.length === 0 ? (
        <div class="pulse-empty">No decisions recorded yet.</div>
      ) : (
        list.map((it, i) => {
          const srcBits = [it.source_type, it.source_ref]
            .filter(Boolean)
            .join(" · ");
          return (
            <div
              class="pulse-row"
              key={`${it.decision_id}-${i}`}
              data-decision-id={it.decision_id}
            >
              {srcBits ? (
                <span class="pulse-row-src">{srcBits}</span>
              ) : null}
              <span class="pulse-row-summary">{it.summary}</span>
              {it.date ? (
                <span class="pulse-row-meta">{fmtDate(it.date)}</span>
              ) : null}
            </div>
          );
        })
      )}
    </section>
  );
}

function PulseBody({ summary }: { summary: ProjectPulseSummary }) {
  return (
    <div class="pulse-card">
      <div class="pulse-title">Project Pulse</div>
      {summary.is_all_clear ? (
        <div class="pulse-allclear">
          Bicameral checked project memory.
          <br />
          No drift, no pending signoffs — memory is current.
        </div>
      ) : null}
      <HealthSection health={summary.health} />
      <NeedsAttentionSection items={summary.needs_attention} />
      <RecentlyLearnedSection items={summary.recently_learned} />
      <section class="pulse-section">
        <div class="pulse-section-name">Suggested Next Move</div>
        <div class="pulse-next">{summary.suggested_next_move}</div>
      </section>
    </div>
  );
}

export function PulseView() {
  const [state, setState] = useState<
    { status: "loading" } | { status: "done"; data: PulseResponse }
  >({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchPulse().then((data) => {
      if (!cancelled) setState({ status: "done", data });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div class="content-pad">
      {state.status === "loading" ? (
        <div class="pulse-card">
          <div class="pulse-title">Project Pulse</div>
          <div class="pulse-loading">Reading project memory…</div>
        </div>
      ) : isPulseError(state.data) ? (
        <div class="pulse-card">
          <div class="pulse-title">Project Pulse</div>
          <div class="pulse-error">
            Project Pulse unavailable: {state.data.error || "no data"}
          </div>
        </div>
      ) : (
        <PulseBody summary={state.data} />
      )}
    </div>
  );
}
