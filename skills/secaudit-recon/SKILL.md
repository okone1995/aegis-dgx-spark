---
name: secaudit-recon
version: "0.1.0"
description: Map the attack surface of an authorized local target before an audit round — enumerate reachable endpoints, parameters and upload points from the plugin packs and the target's live pages, and write surface.json for the attack stage. Use when preparing a new target or after a patch changes the surface. Never use against targets outside scope.yaml.
license: Apache-2.0
metadata:
  author: Aegis Team
  tags: [security, recon, attack-surface, orchestration]
---

# secaudit-recon (attack surface mapping)

Build the attack-surface map for one in-scope target. You produce `surface.json`;
you do NOT attack, patch, or judge. The attack stage (secaudit-attack) consumes your output.

## Required questions

1. Which target from scope.yaml (by `name`)? Refuse targets not in the file.
2. Which instance (a = development, b = evaluation-only)? Refuse b — it is sealed for held-out evaluation.
3. Depth: plugin-declared endpoints only, or live-page crawl as well?

## Workflow

1. Run the scope gate first (engine/scope.py `Scope.check_url`); any violation aborts.
2. Load declared endpoints from the **target's own plugin assets** —
   `engine.target_profile.active().plugin_dir(cls, ROOT/'plugins')` (`endpoint` field);
   that resolver prefers `targets/<name>/plugins/<cls>/` (overlay) and only falls back to
   the repo `plugins/<cls>/` for the legacy target. **Do not assume the repo path.**
   Also fold in `profile.instances[instance].surface` (parameter names vary per target).
3. Crawl the target's public pages (login/news/profile) with the session from
   `engine.replay.admin_session`; record forms, parameter names and upload points.
4. Write `surface.json`: one entry per endpoint with method, path, param names and
   the plugin classes whose payloads target it.

## Tool entry (the actual executors — do not reimplement them)

- session/HTTP: `engine.replay.admin_session` / `engine.replay.fire`
- scope gate: `engine.scope.Scope.check_url`
- plugin endpoint index: `engine.target_profile.active().plugin_dir(cls, ...)` / `plugin.yaml`
- instance surface (param names): `profile.instances[instance].surface`

## Definite prohibitions

- NEVER crawl or probe a host not in scope.yaml; NEVER bypass the scope gate.
- NEVER store credentials in surface.json (session tokens are runtime-only).
- Never mark an endpoint as vulnerable — that judgment belongs to secaudit-attack
  and secaudit-detect.

## See also

- secaudit-attack (consumes surface.json), secaudit-run (orchestration)
- governance/scope.example.yaml
