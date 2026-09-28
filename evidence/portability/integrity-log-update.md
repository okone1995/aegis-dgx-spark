# Integrity log amendment · 2026-09-28T16:00Z or later

This is an operator-side retrospective record. Exact first-session and disclosure times were not independently recovered; no approximate time is presented as an exact measurement.

## Header amendment

The construction-time statement “the first blind session has not happened” describes the state when the log was started. It is superseded by this append-only amendment. The working copy has since been exercised; the candidate/integration side has read target internals, so subsequent discovery/repair results are adapted results. Existing package seals are not overwritten.

## Entry 4 — reported first exercise

The candidate side reports an initial exercise against a working copy. An independent precise start time and a complete first-session transcript were not recovered in this finalization. This entry registers the reported event and its evidence limitation; it does not certify blind-discovery success. Later adapted run IDs cannot substitute for the missing first-session record.

## Entry 5 — disclosure boundary

The integration side subsequently read delivery documentation and adapted target/profile/plugin assets. The exact disclosure time is unknown; disclosure had already happened before the adapted run below. From that point, new discoveries on this target cannot be claimed as truly unseen discoveries.

## Entry 6 — adapted repair, exact observed UTC time

- Run: `run-20260927-103447-1026`.
- `started_at`: **2026-09-27T10:34:47.941Z**; `ended_at`: **2026-09-27T10:35:40.094Z**. The earlier draft's `02:34:47Z` was incorrect.
- State: succeeded / verified / restored, `patch_mode=llm_guided`.
- Same attack replay: 0/2; three gates passed; two business assertions and six functional tests passed.
- Receipt reports `restored_sha256=a0127046b3968ef9ae1ecfa5a940b8c0d7fd501cd71ebdc7c5e00d58da5d58b3`, matching the pre-patch canonical hash in that run. A Windows working copy can have CRLF byte differences: source equivalence after LF normalization is distinct from raw-byte equality to every copy of the sealed file.
- This is repair portability after adaptation, not blind discovery.

## Entry 7 — independent reports and limits

- Independent third-layer run: `run-20260928-135732-7657`, approximately 32 seconds, effect-level repair/replay/business checks passed. Its patch provider was **StepFun**, after local-model authentication failed. This does not establish local 125B patching on that independent run.
- Reports: `aegis/agent_run/blind-layer3-report.md` and `aegis/agent_run/oracle-review-report.md`; frozen original reports are preserved.
- Oracle review supports response-level repair effect. The package does **not** supply the strict oracle verdict artifact, the 54-assertion suite for that repaired run, or reset/key-rotation evidence. Do not call strict oracle acceptance complete.
- Reviewer correction: one report says to use the JEV verdict as truth for a disputed flow. That sentence is not adopted. JEV is advisory; traffic intent requires independent evidence/label review.
- Changes in this finalization touch **this log only** in the sealed-source checkout. Application, seed, oracle code and historical package outputs are unchanged. A dirty documentation tree after this append is not evidence of application-source tampering; retain the old seals.
