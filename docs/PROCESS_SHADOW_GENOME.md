# Process Shadow Genome

Runtime-readable JSONL log of unaddressed process drift. Read by
`qor.scripts.check_shadow_threshold` and `qor.scripts.shadow_process`.

For narrative failure-mode entries (HALLUCINATION, ORPHAN/SCOPE_CREEP),
see `SHADOW_GENOME.md` — separate artifact, human-readable, not parsed
by the runtime.

---

{"id":"cc0465076ca583f34c75fdf0204a5d9898cb5a4c233c125966f4d8fa27f394d9","ts":"2026-05-02T01:05:02Z","skill":"qor-audit","session_id":"2026-05-02T0052-2d49b8","event_type":"capability_shortfall","severity":2,"details":{"capability":"agent-teams","source":"docs/SYSTEM_STATE.md","label":"shadow-001"},"addressed":true,"issue_url":null,"addressed_ts":"2026-05-02T01:06:47Z","addressed_reason":"remediated","source_entry_id":null}
{"id":"ea423388410601995b6b02d8484ec6c950d1a6f131c3413c7ba8285e21c68880","ts":"2026-05-02T01:05:02Z","skill":"qor-audit","session_id":"2026-05-02T0052-2d49b8","event_type":"capability_shortfall","severity":2,"details":{"capability":"codex-plugin","source":"docs/SYSTEM_STATE.md","label":"shadow-002"},"addressed":true,"issue_url":null,"addressed_ts":"2026-05-02T01:06:47Z","addressed_reason":"remediated","source_entry_id":null}
{"id":"244ab430f67e6e2643b69c90071cd8b7d25b421a0b787468cc61e61d031d3ce3","ts":"2026-05-02T01:05:17Z","skill":"qor-repo-scaffold","session_id":"2026-05-02T0052-2d49b8","event_type":"degradation","severity":3,"details":{"gap":"SECURITY.md missing in repo root","source":"docs/BACKLOG.md#S1","label":"shadow-003"},"addressed":true,"issue_url":null,"addressed_ts":"2026-05-02T01:06:17Z","addressed_reason":"remediated","source_entry_id":null}
{"id":"d4625e2f08e9d95c385f5869c3bb2e1ce1dad996fc91902589b55db34458c593","ts":"2026-05-02T01:46:48Z","skill":"qor-plan","session_id":"2026-05-02T0052-2d49b8","event_type":"gate_override","severity":1,"details":{"phase":"plan","prior_phase":"research","reason":"Issue #146 body is research-grade analysis; treated as research substrate. No qor-research artifact authored."},"addressed":false,"issue_url":null,"addressed_ts":null,"addressed_reason":null,"source_entry_id":null}
