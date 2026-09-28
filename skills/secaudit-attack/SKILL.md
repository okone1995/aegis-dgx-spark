---
name: secaudit-attack
version: "0.1.0"
description: Replay and mutation-launch the plugin payload set against an in-scope local target, record every exchange into flow.jsonl, and promote confirmed hits to findings on the blackboard. Use inside an audit round after recon. Never launch a payload rejected by the policy gate, and never point at a target outside scope.yaml.
license: Apache-2.0
metadata:
  author: Aegis Team
  tags: [security, red-team, replay, mutation, attack]
---

# secaudit-attack (red side)

You are the red side. You launch the plugin payload set (replay of pre-validated
requests plus bounded online mutation from the wave5 freeform channel), record every
exchange, and write confirmed hits to the blackboard as open findings. You do NOT
detect, patch, or judge — that is blue's job.

## Required questions

1. Target name and instance (must exist in scope.yaml; instance b is refused)?
2. Which vulnerability class (one per round from plugins/)?
3. Max rounds and whether human confirmation is required before each patch?

## Workflow

1. Scope gate (`engine.scope`), then session via `engine.replay.admin_session`
   (or `user_session` for low-privilege perspectives — IDOR requires alice, not admin).
2. Load the class plugin pack: `plugins/<class>/payloads/attack/` (verify-group
   payloads are FORBIDDEN to you — they belong to the gate).
3. Move selection: pick from the 13-move menu (`engine.attacker.menu_for`); freeform
   mutation only via the wave5 channel (syntax gate + policy gate, both mandatory,
   rejects are recorded with jail_note).
4. For each payload: `engine.replay.check_payload_policy` FIRST, then
   `engine.replay.replay_payload` with `on_exchange` recording into flow.jsonl.
5. Confirmed hits (marker match) → `blackboard.add_finding` with
   `evidence.marker_hit=true` (flywheel label source ①).

## Tool entry

- session/mutation/replay: `engine.attacker` (menu_for / plan_free) + `engine.replay`
- payload location: `engine.target_profile.active().plugin_dir(cls, ROOT/'plugins')` -> `payloads/attack/*.txt`; overlay first, **never assume the repo plugins/ holds this target's payloads**
- policy gate: `governance.payload_policy.assert_allowed`
- blackboard: `engine.blackboard.Blackboard.add_finding`

## Definite prohibitions

- NEVER launch a payload that the policy gate or syntax gate rejected — rejects are
  recorded, not retried.
- NEVER use the verify-group payloads during attack; they are held-out for the gate.
- NEVER touch a host outside scope.yaml; NEVER attempt persistence or destructive
  actions even if the target seems to invite it.

## Common issues

- 401/403 on everything → your session died (patch redeploy wipes SQLite);
  re-authenticate, never reuse a stale cookie.
- Zero hits on a class you know is vulnerable → the canonical may still be patched
  from a previous run; restore first.

## See also

- secaudit-detect (blue side), secaudit-patch, governance/payload_policy.py
