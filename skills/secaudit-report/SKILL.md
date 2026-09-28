---
name: secaudit-report
version: "0.1.0"
description: Assemble the Chinese-language repair report and the convergence curve for one audit run — every number quoted from claims.yaml-backed blackboard artifacts, executive summary first, bounded statements verbatim from EVIDENCE.md. Use at the end of a run or on demand. Never invent a number, and never let a claim into the report that claims.yaml cannot assert.
license: Apache-2.0
metadata:
  author: Aegis Team
  tags: [security, reporting, stepfun, evidence]
---

> RETIRED FROM THE SUBMISSION SUPPORT MATRIX: not accepted for report generation. Use run receipts and the final release documents.

# secaudit-report (documentation layer)

You turn a finished run into a report a non-security executive can read: what was
found, what was repaired, how convergence was proven, what remains — in that order.
You generate via the StepFun cloud provider (the cloud-enhanced tier; the audit
main chain stays local — say so in the report header).

## Required questions

1. Which workspace run (rounds dir) and instance?
2. Audience: executive summary only, or full technical appendix?

## Workflow

1. Read the blackboard: findings + rounds snapshots + verify results + patches.
2. Build the convergence numbers from rounds/*.json (never from memory).
3. `engine.llm.chat` with purpose="report" on the step provider generates the
   narrative from the blackboard facts you pass in; you validate every number
   against claims.yaml afterwards.
4. Embed the convergence curve image and the CEO summary (found X, repaired Y,
   remaining risk Z, human attention W).

## Tool entry

- narrative: `engine.llm.chat` (provider=step, purpose="report", role="报告智能体")
- data: `engine.blackboard.Blackboard` read models + rounds/*.json
- curve: console chart export or ECharts render

## Definite prohibitions

- NEVER write a number that claims.yaml cannot assert (the pytest gate will fail
  the commit — by design).
- NEVER use prohibited wording (see lint_language.py list): no "unseen attack
  generalization", no "no memory drift", no TBD.
- NEVER mix the two latency calibers (single-shot vs batch) in one sentence.

## See also

- EVIDENCE.md (the claims discipline you obey), secaudit-run (your data source)
