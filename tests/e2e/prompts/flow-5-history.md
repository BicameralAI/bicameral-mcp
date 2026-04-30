Show me the full decision history for this repo. Group decisions by feature area and for each one, surface BOTH axes:

- **status** — code-compliance side: reflected | drifted | pending | ungrounded
- **signoff.state** — human-approval side: proposed | ratified | rejected | superseded | collision_pending | context_pending

Before you call history, ingest two seed decisions so the response isn't empty:

1. "Reorder commits via drag/drop" (feature_group: Improved commit history) — leave at default proposed/ungrounded.
2. "Native support for Apple silicon machines" (feature_group: Apple silicon) — ingest, then ratify it so it shows ratified × ungrounded in the readout.

After history returns, render a brief table showing each decision's two axes so I can scan it.
