I want to capture a constraint we should be tracking for the cherry-pick implementation:

> "The cherry-pick implementation should never require interactive prompts during conflict resolution — conflicts must always be resolvable through the visual conflict UI, not via stdin."

It's a load-bearing decision (it affects how the conflict-handling code path can evolve), and right now it lives only in conversation. Capture it as a session-end correction and ingest it into the bicameral ledger using the `agent_session` source — it's coming from this current conversation rather than a doc or transcript.

After ingesting, confirm the decision_id and the signoff state.
