// Cycle 1 transitional Ledger view: bridges the proven legacy dashboard
// (served at /legacy) inside the v2 shell via an iframe. The iframe fills
// the routed content area — the v2 sidebar, header, and Team Sync pill
// chrome stay around it. The native Ledger component port is M1b.
export function LedgerView() {
  return (
    <div class="ledger-wrap">
      <div class="ledger-subtitle">
        <strong>Ledger</strong>
        Complete decision history, source groups, ratification state,
        implementation linkage, and drift indicators.
      </div>
      <iframe
        class="ledger-frame"
        src="/legacy"
        title="Bicameral Decision Ledger"
      />
    </div>
  );
}
