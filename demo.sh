#!/bin/bash
# demo.sh —— Aegis 一键演示（D6：完整性最值钱的单品）
# 环境自检 → 靶场重置 → 多轮对抗收敛 → 收敛曲线与黑板终态
# 用法: bash demo.sh   （Spark 上，~/aegis 目录）
set -e
cd "$(dirname "$0")"
PY="${PY:-$HOME/venvs/aegis/bin/python}"
export PHP_BIN="${PHP_BIN:-$HOME/tools/php/php}"

echo "==== [1/4] 环境自检 ===="
ok=0; fail=0
for pair in "8081:靶场A" "8082:靶场B" "8000:Console" "30010:素材服务" "30000:LLM(125B)"; do
  port="${pair%%:*}"; name="${pair##*:}"
  if ss -tln | grep -q ":$port "; then echo "  ✓ $name (:$port)"; ok=$((ok+1));
  else echo "  ✗ $name (:$port) DOWN"; fail=$((fail+1)); fi
done
[ "$fail" -gt 0 ] && { echo "FATAL: $fail 个服务不在监听，先 bash target/edu-lite/deploy.sh"; exit 1; }

echo "==== [2/4] 靶场重置（漏洞态） ===="
# 评审 P1（D6 复核+kimi/K3 双挑）：旧版只查仓库根 .canonical-backup，而 runloop/marathon
# 的备份写在 workspace/.canonical-backup/——路径永远对不上，补丁态遗留会被静默带上演示台。
PRISTINE=""
for cand in workspace/.canonical-backup/app.php .pristine/app.php .canonical-backup/app.php; do
  [ -f "$cand" ] && PRISTINE="$cand" && break
done
if grep -q "\[patched" target/edu-lite/src/app.php; then
  if [ -n "$PRISTINE" ]; then
    cp "$PRISTINE" target/edu-lite/src/app.php
    echo "  canonical 处于补丁态 -> 已从 $PRISTINE 重置漏洞态"
  else
    echo "FATAL: canonical 含 [patched 污染且无任何纯净备份——拒绝演示（防假收敛）"
    echo "       恢复路径：git checkout -- target/edu-lite/src/app.php 后重试"
    exit 1
  fi
fi
if grep -q "\[patched" target/edu-lite/src/app.php; then
  echo "FATAL: 恢复后仍带补丁标记"; exit 1
fi
if [ -z "$PRISTINE" ]; then
  echo "  WARN: 无纯净备份（当前确认是漏洞态，可继续；建议留一份 .pristine）"
fi
bash target/edu-lite/deploy.sh a

echo "==== [3/4] 多轮对抗收敛（8 类 × 2 轮） ===="
# 旧演示数据归档（飞轮纪律：只搬不删）。
# 契约 §3（T0-contract-freeze F2）：workspace/runs/ 是新单案例演示的持久化目录，
# 归档仅限旧入口自己的工作内容，runs/ 原地不动。
if [ -d workspace ] && [ -n "$(ls -A workspace 2>/dev/null)" ]; then
  ts=$(date +%Y%m%d_%H%M%S)
  archive="dataset/raw/runloop_archive/${ts}_demo_pre"
  mkdir -p "$archive"
  for item in workspace/* workspace/.[!.]*; do
    [ -e "$item" ] || continue
    base=$(basename "$item")
    [ "$base" = "runs" ] && continue
    mv "$item" "$archive/$base"
  done
  echo "  旧 workspace（除 runs/）归档 -> $archive"
fi
$PY -m engine.marathon --base http://127.0.0.1:8081 --instance a \
    --runtime "$PWD/target/edu-lite/runtime/a" --rounds 2

echo "==== [4/4] 战果摘要 ===="
$PY - <<'EOF'
import json, pathlib
ws = pathlib.Path("workspace")
rounds = sorted(ws.glob("rounds/round_*.json"), key=lambda p: int(p.stem.split("_")[1]))
print("  收敛曲线:")
for p in rounds:
    r = json.loads(p.read_text())
    c = r["counts"]
    remain = c.get("open", 0) + c.get("patched", 0) + c.get("regressed", 0)
    print(f"    R{r['round']}: 剩余漏洞={remain} 已验证={c.get('verified', 0)}")
f = json.loads((ws / "findings.json").read_text())["findings"]
print(f"  黑板终态: {len(f)} findings, verified={sum(1 for x in f if x['status']=='verified')}")
print("  数据出域: 0 字节（审计主链路全本地；StepFun 仅云增强道）")
EOF
echo "==== demo 完成。Console: http://127.0.0.1:8000 （⟲ 时光回放可重放本轮） ===="
