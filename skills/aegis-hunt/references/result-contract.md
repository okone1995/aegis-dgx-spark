# aegis-hunt result contract

## Transport

- One JSON object on stdout. Errors: `{"status":"error","code":"<code>"}` + exit 2.
- A discovery that finds nothing is **not** an error: exit 0 with `claim`.

## Codes

| code | When |
| --- | --- |
| `scope_pin_missing_or_invalid` | `AEGIS_SCOPE_SHA256` absent or not a SHA-256 |
| `scope_hash_mismatch` | file bytes do not match the pin |
| `scope_schema_mismatch` / `invalid_scope` | shape/values wrong |
| `target_not_registered` | target not in the registry, or fields disagree |
| `invalid_target_base` | base is not loopback |
| `budget_over_hard_cap` | scope asks for more than the hard cap (200 req / 600 s) |
| `ladder_missing` | no ladder for the requested vuln class |
| `targets_file_unreadable` / `targets_schema_mismatch` | registry problem |

## Result fields

`claim`, `target`, `class`, `path`, `param`, `markers`, `column_count`,
`hit` (the confirming attempt, incl. `poc_candidate_id`), `attempts` (every
exchange: step/status/length/markers/url), `budget` (used vs caps), `notes`,
`queue` (candidate queue stats).

## What a claim does and does not mean

- `exploited_confirmed` = a configured marker appeared in a response, with the
  exchange recorded. It says nothing about other endpoints, other classes, or
  about exploitation outside the authorised scope.
- `anomaly_only` / `not_found` / `budget_exhausted` must never be reported as a
  clean bill of health for the target.

**条件存在的字段（消费方必须容忍缺失）**：`library_hit` 只在「库优先且库内某条命中」时出现；`column_count` 只在差分探测成功定出列宽时出现。按固定键名强解析的消费者必须把它们当可选。
