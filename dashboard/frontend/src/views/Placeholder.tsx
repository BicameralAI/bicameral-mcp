interface PlaceholderProps {
  /** Human-readable view name, e.g. "Ratification". */
  name: string;
}

// "Coming soon" view, reused for the 6 unbuilt M1 surfaces (Ratification,
// Drift, Sources, Audit, Integrations, Settings). Nav is present; content
// is stubbed this milestone.
export function Placeholder({ name }: PlaceholderProps) {
  return (
    <div class="placeholder">
      <div class="placeholder-name">{name}</div>
      <span class="placeholder-tag">Coming soon</span>
      <p>
        The {name} view is part of the Dashboard v2 program and lands in a
        later milestone. The navigation entry is in place so the information
        architecture is complete.
      </p>
    </div>
  );
}
