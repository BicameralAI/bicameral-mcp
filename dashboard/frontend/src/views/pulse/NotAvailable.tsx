interface NotAvailableProps {
  /** Section name, e.g. "Dependency Pulse". Rendered as a JSX text child. */
  title: string;
  /** One-line explanation of what lands later. Rendered as a text child. */
  detail: string;
}

// Shared "not yet available" placeholder.
//
// This is deliberately, unmistakably a *forthcoming-feature* card — it must
// never read as a real-but-empty section or imply a fabricated number. It
// carries its own visual treatment (.pulse-na) distinct from data sections,
// a "Not yet available" tag, and an honest forward-looking sentence.
//
// `title` and `detail` are static caller-supplied strings; they still reach
// the DOM as JSX text children, so Preact escapes them regardless.
export function NotAvailable({ title, detail }: NotAvailableProps) {
  return (
    <div class="pulse-na" role="note">
      <div class="pulse-na-head">
        <span class="pulse-na-title">{title}</span>
        <span class="pulse-na-tag">Not yet available</span>
      </div>
      <p class="pulse-na-detail">{detail}</p>
    </div>
  );
}
