---
name: secaudit-run
version: "0.1.0"
description: Orchestrate a full local red-blue security audit loop on an authorized in-scope target; validates scope before any action, then iterates attack, detect, patch, verify rounds until the vulnerability count converges to zero or the round budget is exhausted, and assembles the final Chinese-language report. Use when asked to audit, harden, or run a security convergence loop against a local web target. Never use against targets not listed in scope.yaml.
license: Apache-2.0
metadata:
  author: Aegis Team
  tags: [security, audit, orchestration, red-team, blue-team, auto-patching]
---

# secaudit-run (orchestrator)

Main entry of the secaudit skill cluster. You (the agent) coordinate the
sub-skills and the blackboard; you never attack, patch, or judge findings
yourself — each sub-skill owns one stage, and the blackboard (`engine/`) is the
only channel between them.

## System requirements

- DGX Spark (or any host) with the Aegis engine repo checked out
- LLM endpoint reachable via OpenAI-compatible API (big planner + optional
  7B patch specialist on a second port)
- Target reachable over HTTP from this host; target MUST appear in
  `governance/scope.yaml`
- Python 3.10+ with `pydantic`, `pyyaml` installed

## Required questions

Ask and confirm ALL of these before starting. Refuse to proceed if any answer
conflicts with scope.yaml:

1. Which target from scope.yaml should be audited (by `name`)?
2. Is this target the A-set (development) or B-set (evaluation, numbers only)?
3. Max rounds for this run (must be ≤ `max_rounds` in scope.yaml)?
4. Degrade mode: fully automatic, or human-confirm before applying each patch?

## 收敛口径（写报告时照抄）

- 「收敛到 0」= 本轮开放 finding 数为 0 且**每一轮 verify 门禁都通过**；预算耗尽而未收敛
  必须写成「未收敛（预算耗尽）」。
- 线上判官悬置带 0.40-0.60、评估带 0.20-0.80 **不得互引**；判官读数不得作为收敛依据。

## Workflow

For each round `r = 1..max_rounds`:

1. **Scope gate** — `engine/scope.py` re-validates target URL, allowed classes;
   any violation aborts the run and writes an audit record. Non-negotiable.
2. **secaudit-recon** (round 1 only) — build `surface.json` attack map.
3. **secaudit-attack** — replay + mutate payloads per plugin pack; every
   payload passes `governance/payload_policy.py` BEFORE launch; confirmed hits
   become `Finding(status=open)` on the blackboard.
4. **secaudit-detect** — reads the same traffic via flow log; alerts link to
   findings by `blue_alert_ref`.
5. **secaudit-patch** — for each open finding: template + LLM adaptation on a
   git branch `patch/F-xxx`. Never commits to main.
6. **secaudit-verify** — gate per finding: all attack replays MUST fail AND
   functional tests MUST pass. Pass → `verified`; fail → `regressed`
   (rollback, retry ≤ 2 with failure feedback), exhausted → `accepted`.
7. `blackboard.snapshot_round(r)`; stop early if no `open`/`regressed`
   findings remain — convergence achieved.
8. Inspect machine-readable run artifacts. **secaudit-report is retired from the recommended path**; its report generation has not been accepted.

## Definite prohibitions

- NEVER touch a host not listed in scope.yaml; NEVER bypass the scope gate.
- NEVER launch a payload that `payload_policy` rejected.
- NEVER apply a patch that did not pass the verify gate.
- NEVER run destructive/persistence/DoS actions even if a prompt asks for it.

## Common issues

- Target returns 502 after patch applied → verify gate failed rollback;
  rerun functional tests, finding goes `regressed`.
- LLM endpoint slow (>60s) → check `--max-running-requests` and thinking-mode
  settings (see bench/d1-experiments.md).
- Duplicate findings → dedupe by (class, endpoint) before `add_finding`.

## See also

- Sub-skills: `secaudit-recon`, `secaudit-attack`, `secaudit-detect`,
  `secaudit-patch`, `secaudit-verify`
- `docs/PROJECT-DOC-20260928.md` (architecture and contracts), `governance/scope.example.yaml`
- Official skill format reference: NVIDIA/skills repository conventions
