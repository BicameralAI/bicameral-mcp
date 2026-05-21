import { NotAvailable } from "./NotAvailable";

// Dependency Pulse — amendment §6.3. The Dependency Layer (scope-creep
// signals, blast-radius warnings, affected features/owners, EM-routed
// issues) has NO backend whatsoever — `/pulse` produces no such data.
//
// Rather than drop the section or invent numbers, it renders a single,
// unmistakable NotAvailable card so the information architecture stays
// complete and the absence is honest.
export function DependencyPulseSection() {
  return (
    <section class="pulse-section">
      <div class="pulse-section-name">Dependency Pulse</div>
      <NotAvailable
        title="Blast-radius & scope-creep analysis"
        detail={
          "Affected features, owners and EM-routed issues arrive in a " +
          "later milestone. No dependency data is produced by the Pulse " +
          "endpoint yet."
        }
      />
    </section>
  );
}
