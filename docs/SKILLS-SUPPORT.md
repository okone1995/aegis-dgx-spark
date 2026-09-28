# Submitted skills: supported use and acceptance

This submission contains four agent-facing prototype skills, six internal secaudit components and a retired report package. None is NVIDIA-Verified.

| Skill | Supported behavior | Acceptance / limits |
|---|---|---|
| aegis-self-repair | Start a registered restore repair, query status, verify an evidence receipt | New real CLI run `run-20260928-155647-3290`: verified_restored, 17/17 proof flags. Backend, models and operator-pinned scope required; registered edu-lite SQLi case accepted. Other registry entries are not automatically accepted |
| aegis-hunt | Scope/budget-constrained ladder probing and observed-exchange evidence | Historical known-target 21-request / 3-request comparison; unit checks for GET/POST exchange fidelity. Historical response summaries are quarantined; unsupported classes are not inferred safe |
| aegis-evolve | Operator-reviewed confirmed POC write-back and library reuse | Provenance-backed 21→3 case. Shell/environment gates record operation intent and reviewer; they are not OS-level security isolation |
| aegis-jevtrain | Collect candidates, report pairs, review/correct labels, approve samples, export drafts | Real main-run collection plus offline behavior tests. Label review is separate from admission. Missing sealed registry/quality checks keep training_ready=false. No autonomous training or deployment upgrade |
| secaudit-recon/detect/attack/patch/verify/run | Internal profile-driven components | Imports/selected functions accepted; current complete standalone runloop end-to-end acceptance remains incomplete |
| secaudit-report | Retired from recommended use | Unaccepted report generator; use receipts and release documents |

The public offline suite checks data/guards and the four bridges. It is not a blanket claim that all private/GPU integrations pass. Skills require the Aegis engine/backend; copying SKILL.md alone does not provide a model service or target environment.
