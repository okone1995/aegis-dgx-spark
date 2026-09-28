# aegis-evolve result contract

## Codes (exit 2)

| code | When |
| --- | --- |
| `scope_pin_missing_or_invalid` / `scope_hash_mismatch` | pin missing or file differs |
| `scope_schema_mismatch` | scope shape wrong |
| `target_not_registered` | target not in the operator registry |
| `invalid_target_base` | base is not loopback |
| `operator_gate_not_satisfied` | `AEGIS_EVOLVE_OPERATOR` unset, or `--reviewer` ≠ it |
| `review_refused` | engine refused (no hard evidence / already reviewed) |
| `engine_not_found` | cannot locate the Aegis engine |

## Fields

- `report`: `stats`, `eligible_for_writeback`, `pending_with_evidence`,
  `pending_without_evidence` — never claims anything is "trained".
- `verify-written`: `written`, `hits`, `results[]` with `status`, `markers_hit`,
  and the payload's `provenance`.
- `compare`: `before` / `after` columns (`claim`, `requests_used`,
  `seconds_used`, `first_hit_at`, `hit_step`, `library_hit`) plus a `delta`
  block. The two columns are independent observations of two separate rounds —
  same target, different starting knowledge — and must be reported as such.

**条件存在的字段（消费方必须容忍缺失）**：`library_hit` 只在「库优先且库内某条命中」时出现；`column_count` 只在差分探测成功定出列宽时出现。按固定键名强解析的消费者必须把它们当可选。
