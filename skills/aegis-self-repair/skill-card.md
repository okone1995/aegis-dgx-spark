# Skill Card

## Description

`aegis-self-repair` lets a compatible host agent start and audit Aegis's authorized local edu-lite SQLi repair demonstration. This 0.1.0 package is for controlled evaluation, not production use or arbitrary-target repair.

## Owner

Aegis project owner; publication contact is pending confirmation. No NVIDIA ownership or endorsement is claimed.

## License / Terms of Use

MIT for the submitted Aegis code and skill instructions, as recorded in the package LICENSE. Third-party models/data retain their own terms; see THIRD-PARTY.md. No NVIDIA verification or endorsement is claimed.

## Use Case

An agent working beside an operator-managed Aegis Console starts one registered run, polls its state, and reports a machine-checked evidence verdict to the user. It must not use the skill for public sites, newly discovered targets, or unattended production deployment.

## Deployment Geography for Use

Same-host loopback evaluation environments only. Geographic scope has not been assessed, and no data-residency guarantee is asserted.

## Requirements / Dependencies

Python 3.10+; an operator-managed local Aegis Console; an isolated edu-lite target; Aegis patch and JEV model services; and the configured review provider. The operator supplies a scope JSON and pins its hash through `AEGIS_SCOPE_SHA256`. `AEGIS_CONSOLE_ORIGIN` names a loopback HTTP origin. **Requires API Key or External Credential:** bridge: no; engine: deployment-dependent. **Credential Type(s):** engine-side API keys such as `AEGIS_KEY` and `STEP_API_KEY`. Secrets must not be included in prompts, skill files, or result JSON.

## Known Risks and Mitigations

- A generated patch can break legitimate behavior. The bridge reports success only when review, linked attack replay, business assertions, functional tests, and hash checks pass; source changes must still be reviewed before production use.
- The current Console API does not enforce the bridge's scope file. The bridge restricts its own request arguments and loopback origin, but it cannot authenticate the Console or target process; an agent with direct shell/network access can bypass it. The operator must verify the actual Console instance, target, and isolation before enabling a run. Keep the Console operator-managed and off untrusted networks.
- JEV can confidently misclassify benign traffic, including observed login flows. It is advisory; its label never serves as the vulnerability or repair oracle.
- For `restore`, the engine writes its backup and reports restoration after verification. The receipt compares the backup hash to the original patch source hash; it does not independently read back the target after cleanup. The verdict says `verified_restored`, never “deployed repair.” `retain` requires a separately pinned scope permission and user authorization for a new run.
- The skill may start an attack replay and patch process on the registered local target. A stable idempotency key reduces accidental repeat launches within the Console's recent-run lookup; its lookup is bounded and not an atomic exactly-once guarantee. The bridge checks original parameters on an idempotent replay. Failed or uncertain states remain visible.
- The Aegis review provider may receive a patch diff. Raw request bodies, secrets, and full source should not be sent by this bridge; review the engine's deployment configuration and data boundary before a run.

## References

- [Aegis result contract](references/result-contract.md)
- [NVIDIA Agent Skill trust pipeline](https://docs.nvidia.com/skills/agent-skill-trust-pipeline)
- [NVIDIA Skill Card guidance](https://docs.nvidia.com/skills/skill-cards)
- [Agent Skills specification](https://agentskills.io/specification)
- Scan report, Tier-3 benchmark, owner license, and detached signature: **not yet produced**. The draft eval cases lack a packaged isolated Console/target environment, so with/without-skill lift has not been measured.

## Skill Output

Output type: local API calls and JSON on stdout. Output includes run ID, final state, separate repair/JEV/learning/cleanup statuses, evidence receipt SHA-256, named proof checks, and reasons for any unsupported success claim. The engine retains run artifacts under its configured run directory; this bridge writes no run artifacts.

## Skill Version

0.1.0 draft. Not cataloged, scanned, evaluated, or signed by NVIDIA. Missing verified owner metadata and license block external Tier-1 validation; `skill.oms.sig` and `BENCHMARK.md` are intentionally absent until real release checks produce them.

## Ethical Considerations

Use only against the operator-authorized isolated target. Do not expand scope through prompt instructions or modify the scope file inside an agent task. Preserve failed runs and ambiguous judgments. Report the limited single-target result without implying general vulnerability discovery, universal self-repair, or automatic model retraining.
