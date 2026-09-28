"""把 verify-written 的 JSON 压成摘要。"""
import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
print("  written=%s hits=%s" % (d.get("written"), d.get("hits")))
for r in d.get("results", []):
    prov = r.get("provenance") or {}
    print("    %-34s status=%-4s hit=%-5s reviewer=%s" % (
        r.get("payload"), r.get("status"), r.get("hit"), prov.get("reviewer")))
