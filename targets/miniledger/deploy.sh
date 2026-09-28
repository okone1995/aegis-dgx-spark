#!/usr/bin/env bash
# MiniLedger 部署：建库（幂等）+ 起 PHP 内置服务器
# 用法: bash deploy.sh <instance>   (instance=a 端口 8095 / b 端口 8096)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTANCE="${1:-a}"
case "$INSTANCE" in
  a) PORT=8095 ;;
  b) PORT=8096 ;;
  *) echo "unknown instance: $INSTANCE" >&2; exit 2 ;;
esac

PHP_BIN="${PHP_BIN:-php}"
VAR="$HERE/var"
mkdir -p "$VAR"

# 建库（幂等）。种子脚本独立成文件：SQL 字符串字面量必须单引号，
# 以前写在 php -r 的双引号里被 SQLite 当成标识符 ⇒ 静默漏灌（T11 实测踩过）。
"$PHP_BIN" "$HERE/tools/seed.php" "$VAR/miniledger.sqlite"

# 停旧实例（同端口）
if [ -f "$VAR/pid.$INSTANCE" ]; then
  kill "$(cat "$VAR/pid.$INSTANCE")" 2>/dev/null || true
  sleep 0.3
fi

nohup "$PHP_BIN" -S "127.0.0.1:$PORT" -t "$HERE/src" >"$VAR/server.$INSTANCE.log" 2>&1 &
echo $! > "$VAR/pid.$INSTANCE"

# 就绪探测（最多 5s）
for _ in $(seq 1 25); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "deploy OK instance=$INSTANCE port=$PORT pid=$(cat "$VAR/pid.$INSTANCE")"
    exit 0
  fi
  sleep 0.2
done
echo "deploy FAILED: /health 未就绪 (instance=$INSTANCE)" >&2
exit 1
