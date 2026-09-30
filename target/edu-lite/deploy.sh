#!/bin/bash
# edu-lite A/B 双实例部署（php -S 内置服务器，无 root 依赖）
# 用法: PHP_BIN=/path/to/php bash deploy.sh [a|b]   （不带参数 = 双实例；A=8081, B=8082）
# 进程管理：ss 按端口取 PID（避免 pkill -f 自匹配与 pidfile 竞态——D3 实战教训）
# 就绪探针：curl 轮询替代固定 sleep（评审 P1-B：xss 批跑第三轮"攻击零命中"即部署
# 竞态的警钟——sleep 在负载波动下不可靠，镜头前一次假阴性=演示失败。超时按故障退出，
# 让 runloop 快速失败在部署步，而不是慢速失败在攻击步。）
set -e
cd "$(dirname "$0")"
PHP_BIN="${PHP_BIN:-php}"
DEPLOY_TARGETS="${1:-a b}"
port_of() { case "$1" in a) echo 8081;; b) echo 8082;; esac; }

# 轮询直到 /login 出 HTTP 响应；$1=port $2=超时秒（默认 10，50ms 间隔）
wait_http_ready() {
  local deadline=$((SECONDS + ${2:-10}))
  while [ "$SECONDS" -lt "$deadline" ]; do
    code=$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://127.0.0.1:$1/login" || true)
    if [ "$code" = "200" ] || [ "$code" = "302" ]; then
      return 0
    fi
    sleep 0.05
  done
  return 1
}

# 本地素材服务（SSRF 插件合法拉取源，127.0.0.1:30010）——启动后同样等就绪
ASSET_PID=$(ss -tlnp 2>/dev/null | grep ":30010 " | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -z "$ASSET_PID" ]; then
  (setsid nohup "${PYTHON_BIN:-python3}" "$(dirname "$0")/tools/asset_server.py" > /tmp/asset_server.log 2>&1 < /dev/null &)
  asset_deadline=$((SECONDS + 10))
  until curl -s -o /dev/null -m 1 "http://127.0.0.1:30010/"; do
    if [ "$SECONDS" -ge "$asset_deadline" ]; then
      echo "FATAL: asset_server 10s 未就绪（查 /tmp/asset_server.log）" >&2
      exit 1
    fi
    sleep 0.05
  done
fi

for inst in $DEPLOY_TARGETS; do
  rt="runtime/$inst"
  port=$(port_of "$inst")  # 按端口取主 PID → 按【进程组】整组击杀（多 worker 下杀主进程会留孤儿 worker 占端口——D5 实战教训）
  pid=$(ss -tlnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' | head -1)
  if [ -n "$pid" ]; then
    pgid=$(ps -o pgid= -p "$pid" | tr -d " ")
    # 评审三轮 P2：kill 失败（如 EPERM）不得在 set -e 下静默退出——交给释放轮询去报 FATAL
    [ -n "$pgid" ] && { kill -- -"$pgid" 2>/dev/null || true; }
    # 等端口真正释放再绑新实例（替代原 sleep 1；上限 5s）
    free_deadline=$((SECONDS + 5))
    while ss -tln 2>/dev/null | grep -q ":$port "; do
      if [ "$SECONDS" -ge "$free_deadline" ]; then
        echo "FATAL: :$port 5s 未释放，疑似孤儿进程" >&2
        exit 1
      fi
      sleep 0.05
    done
  fi
  rm -rf "$rt"; mkdir -p "$rt/data" "$rt/uploads" "$rt/files"
  cp -r config src router.php files "$rt/" 2>/dev/null || cp -r config src router.php "$rt/"
  (cd "$rt"; EDU_INSTANCE=$inst PHP_CLI_SERVER_WORKERS=4 setsid nohup "$PHP_BIN" -S 127.0.0.1:$port router.php > serve.log 2>&1 < /dev/null &)
  if ! wait_http_ready "$port" 10; then
    echo "FATAL: instance $inst 10s 未就绪 :$port（查 runtime/$inst/serve.log）" >&2
    exit 1
  fi
  newpid=$(ss -tlnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' | head -1)
  echo "instance $inst pid=${newpid:-?} port=$port ready"
done

# 收尾就绪确认（对本轮部署目标，探针兜底）
for inst in $DEPLOY_TARGETS; do
  port=$(port_of "$inst")
  wait_http_ready "$port" 5 && echo "inst $inst up :$port" \
    || { echo "FATAL: inst $inst 收尾探针失败 :$port" >&2; exit 1; }
done

# 三轮评审 P1：恢复旧版"单实例部署时顺带检查另一实例"的隐性健康门——
# 非致命 WARN（b 不在听不是本次部署的责任，但演示日没人想中途才发现 B 端口是死的）
for other in a b; do
  case " $DEPLOY_TARGETS " in *" $other "*) continue ;; esac
  op=$(port_of "$other")
  ss -tln 2>/dev/null | grep -q ":$op " \
    || echo "WARN: instance $other (:$op) 未在听——本轮未部署它，双实例演示前请先查" >&2
done
