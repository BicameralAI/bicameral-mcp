// Shared, side-effect-free formatting helpers for the Pulse section
// components. None of these emit HTML — they return plain strings that
// the components then render as JSX text children (Preact auto-escapes).

/** Mirror the CLI renderer: show only the local-part of a signer email. */
export function pulseSignerLabel(signer: string | null): string {
  if (!signer) return "";
  const s = String(signer);
  const at = s.indexOf("@");
  return at > 0 ? s.slice(0, at) : s;
}

/**
 * Fixed-shape `YYYY-MM-DD` date string. Derived only — never raw user HTML.
 * An unparseable value falls back to its own string form (still a text
 * child downstream, so still escaped).
 */
export function fmtDate(value: string | null): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toISOString().slice(0, 10);
}
