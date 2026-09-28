"""把一轮 hunt 的 JSON 压成两行摘要（供 bash 演练脚本调用，避免内联 python）。"""
import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
label = sys.argv[2] if len(sys.argv) > 2 else ""
bud = d.get("budget") or {}
hit = d.get("hit") or {}
print("  [%s] claim=%s 发数=%s/%s 秒=%.2f 命中在第 %s 发 | 步骤=%s | 列宽=%s | 库命中=%s" % (
    label, d.get("claim"), bud.get("requests_used"), bud.get("requests_cap"),
    float(bud.get("seconds_used") or 0), hit.get("n"), hit.get("step"),
    d.get("column_count"), d.get("library_hit")))
for n in (d.get("notes") or [])[:3]:
    print("    note:", n)
