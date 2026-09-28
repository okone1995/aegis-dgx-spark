# Result contract

The bridge returns one JSON object to stdout. Transport and configuration errors are `{"status":"error","code":...}` with a nonzero process exit. A failed Aegis run is a valid result with exit code 0; inspect `claim` and `state`.

`start` returns `status=accepted`, `run_id`, and the stable `request_id`. It never waits for a patch. `status` returns `state`, `stage`, `repair_outcome`, `judge_status`, `learning_status`, `cleanup`, and whether the run is terminal.

`verdict` uses the final run snapshot and the whitelisted `evidence-receipt` artifact. It checks the artifact SHA-256 from the run manifest, candidate/deployed hash equality, three review gates, a nonempty linked replay set with zero hits, authenticated replay, no network error, normal business checks, functional tests, and cleanup status. It returns each check under `proof` and any missing or failed checks under `reasons`. When available, `learning_counts.new_candidates` and `.duplicates` expose the receipt's actual queue counts; zero new candidates does not become a training claim.

`claim` meanings:

| Claim | Meaning |
| --- | --- |
| `in_progress` | No terminal result yet. |
| `verified_restored` | The isolated repair passed verification; the engine reported restoring the pre-patch backup. There is no independent post-cleanup read-back. |
| `verified_retained` | The repair passed verification; the engine reported retaining the patch. Use only with explicit `repair_retain` authorization. |
| `partial` | Some repair evidence may be valid, but the full run is degraded or a required service was unavailable. |
| `unverified` | Failed, cancelled, interrupted, missing evidence, or any verification check failed. |

JEV's `judge_status=ok` means the service returned a judgment, not that its attack/benign label was correct. `learning_status=queued` means the queue stage completed; it does not guarantee a new sample, approval, training, or publication. A `verified_retained` result describes the observed cleanup state; it is not proof that a historical run had `repair_retain` authorization. The receipt hash is checked against the Console's manifest, not an independent signature of the Console or target. The v0 bridge is restricted to the registered SQLi demo and does not validate a new target's behavior.
