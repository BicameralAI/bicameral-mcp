interface SuggestedNextMoveProps {
  /** `suggested_next_move` from ProjectPulseSummary — user-sourced text. */
  move: string;
}

// Suggested Next Move — the single recommended action from the backend.
// `move` reaches the DOM as a JSX text child, so Preact escapes it.
export function SuggestedNextMove({ move }: SuggestedNextMoveProps) {
  return (
    <section class="pulse-section">
      <div class="pulse-section-name">Suggested Next Move</div>
      <div class="pulse-next">{move || "No suggestion right now."}</div>
    </section>
  );
}
