---
name: aegis-self-repair
description: Run and audit Aegis's authorized edu-lite SQLi self-repair demo through its local Console. Use only for the registered target, never arbitrary websites.
allowed-tools: Bash Read
metadata:
  version: "0.1.0"
  tags: [security, self-repair, agent, dgx-spark]
---

# Aegis self-repair — registered-target preview

## Purpose

This skill gives a host agent a small, executable interface to Aegis. The Aegis engine performs the attack replay, rule and JEV observations, model patch, review gates, deployment, verification, and evidence recording. Do not improvise those stages in the host agent.

## Requirements and scope

- This version supports **only** the registered `edu-lite-a` / `sqli` case. It does not discover or repair arbitrary targets. Use it only in an isolated, authorized local test environment.
- The operator must supply an existing local, non-link scope JSON file and pin its SHA-256 in `AEGIS_SCOPE_SHA256` outside the agent's prompt. The scope must permit `repair_restore`; `repair_retain` additionally permits a persistent change. The bundled [scope example](assets/scope.example.json) deliberately permits neither. The operator must separately verify that the Console process and target really are the intended isolated Aegis instance; the bridge cannot authenticate their identity.
- Set `AEGIS_CONSOLE_ORIGIN` to the Console's loopback origin. The script refuses non-loopback origins and redirects. The Console itself does **not** yet enforce this scope file, and an agent with direct shell/network access could bypass the bridge; this preview must not be exposed as a public service.
- Python 3.10+ is the only script dependency. The running Console, target, patch model, JEV service, and StepFun reviewer are engine-side dependencies. `selftest` checks the bridge and Console, not their full runtime health.

## Available Scripts

| Script | Purpose | Arguments |
| --- | --- | --- |
| `scripts/aegis_skill.py` | Check access, start one registered run, inspect status, or verify the final evidence | `selftest --scope`, `start --scope --request-id [--cleanup]`, `status --run-id`, `verdict --run-id` |

From this skill directory, invoke `python scripts/aegis_skill.py` (or the host's Python executable):

```text
python scripts/aegis_skill.py selftest --scope /operator/scope.json
python scripts/aegis_skill.py start --scope /operator/scope.json --request-id <stable-id> --cleanup restore
python scripts/aegis_skill.py status --run-id <run-id>
python scripts/aegis_skill.py verdict --run-id <run-id>
```

`start` is the only mutating command. It returns a run ID immediately; poll `status` until terminal, then call `verdict`. Reuse the same `--request-id` with the same cleanup setting if a start response is lost; the Console serializes creation across processes and persists the request fingerprint. The same key and parameters return the same run; changed parameters return HTTP 409. This prevents duplicate creation through this endpoint, but is not an exactly-once guarantee for all external side effects. On an idempotent replay the bridge checks the original `run.created` parameters and refuses a conflicting key. Do not create or alter the operator's scope file or hash from within the agent task. `--cleanup retain` is allowed only when the pinned scope explicitly permits `repair_retain` and the user has authorized keeping the patch. A read-only `verdict` on an older run describes its evidence and cannot prove how that run was authorized.

Read [the result contract](references/result-contract.md) when interpreting a run. Treat `run.json`'s final state as authoritative; the evidence receipt can be written before a later downgrade. A `verified_restored` verdict means the patch passed the isolated verification and the engine reported restoration from backup. The bridge does not independently read the target after cleanup. Only `verified_retained` reports a patch left in place according to the engine. Neither verdict proves generalization to another target.

JEV supplies a binary attack/benign/abstain traffic judgment after an HTTP exchange. It does not decide that a vulnerability exists or that a patch succeeded. The queue stage can finish with zero new candidates when flows are duplicates; inspect `learning_counts`. Queued candidates await review and are not a newly trained model. Report unavailable JEV, failed gates, network errors, missing evidence, and degraded run states without turning them into success.

## Access and data boundary

The script reads the pinned scope file, reads `AEGIS_SCOPE_SHA256` and `AEGIS_CONSOLE_ORIGIN`, and sends GET requests for run metadata and whitelisted artifacts to the configured loopback Console. `start` sends one POST to `/api/demo/runs`; the engine may attack and modify only its registered local target and may send a patch diff to the configured StepFun reviewer. The script does not read arbitrary source files, send credentials, or contact external sites. Do not paste raw requests, responses, passwords, or patch source into agent output; report run ID, evidence hash, state, and failure reason.

For other targets, do not edit this scope example into a claim of portability. A target adapter, engine-side scope enforcement, and separate migration evidence are required first. This skill is a reviewable preview, not NVIDIA-Verified; see [skill-card.md](skill-card.md).

## Troubleshooting

| Error code | Meaning | Next step |
| --- | --- | --- |
| `scope_hash_mismatch` or `operation_not_authorized` | The supplied scope differs from the operator's pin or does not allow this action. | Stop; ask the operator to review the scope. Do not rewrite it in the agent task. |
| `console_http_409` | Another run or target lock is active. | Inspect existing runs and retry later with the same request ID only if no run was created. |
| `idempotency_parameter_mismatch` | A request ID already belongs to different run settings. | Stop; inspect that run instead of starting another with the conflicting key. |
