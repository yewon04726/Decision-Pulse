#!/usr/bin/env bash
# Decision Debt MVP 개발 환경 전체 실행.
#   1) Dify (docker compose up -d)   2) llama-swap   3) 기존 check_metric
#   4) Paperclip (Deep Review control plane, 127.0.0.1:3110)
#   5) backend (FastAPI)             6) web (Next.js)       7) Hermes gateway
#
# - 이미 실행 중인 서비스(포트 LISTEN 중)는 건드리지 않고 건너뛴다.
# - 이 스크립트가 시작한 프로세스만 .run/<name>.pid 에 기록한다 → ./stop.sh 가 이것만 종료한다.
# - 로그: logs/<name>.log
# - 기존 Dify / llama-swap / check_metric 설정 파일은 읽기만 하고 수정하지 않는다.
# - docker compose down -v 는 절대 사용하지 않는다.

set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT/logs"
RUN_DIR="$ROOT/.run"
mkdir -p "$LOG_DIR" "$RUN_DIR"

DIFY_DIR="$HOME/dify/docker"
LLAMA_SWAP_CONFIG="$HOME/llama-swap/config.yaml"
TOOL_DIR="$HOME/decision-debt-tool"
BACKEND_DIR="$ROOT/backend"
WEB_DIR="$ROOT/web"
PAPERCLIP_DIR="$ROOT/paperclip"   # 이 프로젝트 전용 Paperclip instance (data: paperclip/data, launchd 서비스 설치 안 함)

if [ -t 1 ]; then
  G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; B=$'\033[1m'; N=$'\033[0m'
else
  G=""; Y=""; R=""; B=""; N=""
fi

info() { printf '%s\n' "$*"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
skip() { printf '  %s•%s %s\n' "$Y" "$N" "$*"; }
fail() { printf '  %s✗%s %s\n' "$R" "$N" "$*"; }

port_listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1; }

# .run/<name>.pid = pid <TAB> 프로세스 시작 시각 <TAB> 명령 패턴
# stop.sh는 세 값이 모두 일치할 때만 종료한다 (PID 재사용으로 엉뚱한 프로세스를 죽이지 않도록).
record_pid() {
  local name="$1" pid="$2" pattern="$3" started
  started="$(ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^ *//; s/ *$//')"
  printf '%s\t%s\t%s\n' "$pid" "$started" "$pattern" >"$RUN_DIR/$name.pid"
}

# start_bg <name> <port> <workdir> <ps-pattern> <command...>
# 별도 프로세스 그룹으로 실행해서 stop.sh가 자식 프로세스(next-server 등)까지 함께 종료할 수 있게 한다.
start_bg() {
  local name="$1" port="$2" dir="$3" pattern="$4"
  shift 4
  if port_listening "$port"; then
    skip "$name: :$port 이미 실행 중 → 건너뜀 (stop.sh 대상 아님)"
    return 0
  fi
  if [ ! -d "$dir" ]; then
    fail "$name: 디렉터리 없음 $dir"
    return 1
  fi
  {
    printf '\n===== %s start %s =====\n' "$name" "$(date '+%F %T')"
    printf '$ (cd %s) %s\n' "$dir" "$*"
  } >>"$LOG_DIR/$name.log"
  cd "$dir" || return 1
  set -m
  nohup "$@" >>"$LOG_DIR/$name.log" 2>&1 &
  local pid=$!
  set +m
  cd "$ROOT" || return 1
  record_pid "$name" "$pid" "$pattern"
  ok "$name: 시작 (pid $pid) → logs/$name.log"
}

# ------------------------------------------------------------------ 1) Dify
start_dify() {
  if ! docker info >/dev/null 2>&1; then
    info "  Docker Desktop이 실행 중이 아닙니다 → 실행 후 대기 (최대 120초)"
    open -a Docker
    local i=0
    until docker info >/dev/null 2>&1; do
      i=$((i + 1))
      if [ "$i" -ge 60 ]; then
        fail "Dify: Docker가 시작되지 않았습니다. Docker Desktop을 확인하세요."
        return 1
      fi
      sleep 2
    done
  fi
  local running
  running="$(cd "$DIFY_DIR" && docker compose ps --services --status running 2>/dev/null)"
  if printf '%s\n' "$running" | grep -qx api && printf '%s\n' "$running" | grep -qx nginx; then
    skip "Dify: 컨테이너 이미 실행 중 → 건너뜀 (stop.sh 대상 아님)"
  else
    printf '\n===== dify up %s =====\n' "$(date '+%F %T')" >>"$LOG_DIR/dify.log"
    if (cd "$DIFY_DIR" && docker compose up -d) >>"$LOG_DIR/dify.log" 2>&1; then
      date '+%F %T' >"$RUN_DIR/dify.started"
      ok "Dify: docker compose up -d → logs/dify.log"
    else
      fail "Dify: docker compose up -d 실패 → logs/dify.log 확인"
      return 1
    fi
  fi
  # 컨테이너 로그를 logs/dify.log로 수집 (로그 수집 프로세스만 이 프로젝트가 관리)
  if [ ! -f "$RUN_DIR/dify-logs.pid" ] || ! kill -0 "$(cut -f1 "$RUN_DIR/dify-logs.pid")" 2>/dev/null; then
    cd "$DIFY_DIR" || return 1
    set -m
    nohup docker compose logs -f --no-color --since 1m api worker nginx plugin_daemon >>"$LOG_DIR/dify.log" 2>&1 &
    local pid=$!
    set +m
    cd "$ROOT" || return 1
    record_pid dify-logs "$pid" "compose logs -f"
  fi
}

# ------------------------------------------------------------------ health check
# wait_http <label> <port> <url> <timeout-sec> [expected-code-regex]
wait_http() {
  local label="$1" port="$2" url="$3" timeout="$4" expect="${5:-^2}"
  local code="000" elapsed=0
  while [ "$elapsed" -lt "$timeout" ]; do
    code="$(curl -s -o /dev/null -m 3 -w '%{http_code}' "$url" 2>/dev/null)"
    if printf '%s' "$code" | grep -Eq "$expect"; then
      printf '  %s✓%s %-12s :%-5s %s → %s\n' "$G" "$N" "$label" "$port" "$url" "$code"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  printf '  %s✗%s %-12s :%-5s %s → %s (%ss 대기 후 실패)\n' "$R" "$N" "$label" "$port" "$url" "$code" "$timeout"
  return 1
}

# ------------------------------------------------------------------ main
info "${B}Decision Debt MVP — dev 환경 시작${N}"
info ""
info "${B}[1/7] Dify${N}"
start_dify

info "${B}[2/7] llama-swap${N}"
start_bg llama-swap 8080 "$HOME" "llama-swap --config" \
  llama-swap --config "$LLAMA_SWAP_CONFIG" --listen 0.0.0.0:8080

info "${B}[3/7] check_metric (기존 ~/decision-debt-tool)${N}"
start_bg check-metric 8000 "$TOOL_DIR" "uvicorn app:app --host 0.0.0.0 --port 8000" \
  "$TOOL_DIR/.venv/bin/uvicorn" app:app --host 0.0.0.0 --port 8000

info "${B}[4/7] Paperclip (Deep Review)${N}"
# foreground 'paperclipai run'을 dev.sh 프로세스 그룹으로 띄운다 → stop.sh가 함께 종료 (embedded PostgreSQL 포함).
# 'paperclipai service install'(launchd)은 쓰지 않는다. bind는 config의 loopback(127.0.0.1)
if [ ! -x "$PAPERCLIP_DIR/node_modules/.bin/paperclipai" ]; then
  skip "Paperclip: 설치 안 됨 → cd paperclip && npm install (Deep Review만 비활성, 다른 기능은 영향 없음)"
elif [ ! -f "$PAPERCLIP_DIR/data/instances/default/config.json" ]; then
  skip "Paperclip: instance 없음 → README의 Deep Review 설정 참고 (Deep Review만 비활성)"
else
  start_bg paperclip 3110 "$PAPERCLIP_DIR" "paperclipai run" \
    env PAPERCLIP_TELEMETRY_DISABLED=1 "$PAPERCLIP_DIR/node_modules/.bin/paperclipai" run -d "$PAPERCLIP_DIR/data"
fi

info "${B}[5/7] backend${N}"
if [ ! -x "$BACKEND_DIR/.venv/bin/uvicorn" ]; then
  fail "backend: .venv 없음 → cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
else
  start_bg backend 8001 "$BACKEND_DIR" "uvicorn app:app --host 127.0.0.1 --port 8001" \
    "$BACKEND_DIR/.venv/bin/uvicorn" app:app --host 127.0.0.1 --port 8001
fi

info "${B}[6/7] web${N}"
if [ ! -d "$WEB_DIR/node_modules" ]; then
  fail "web: node_modules 없음 → cd web && npm install"
else
  start_bg web 3000 "$WEB_DIR" "npm run dev" npm run dev
fi

info "${B}[7/7] Hermes gateway${N}"
if hermes gateway start >>"$LOG_DIR/hermes.log" 2>&1; then
  if hermes gateway ensure --json --timeout 60 >>"$LOG_DIR/hermes.log" 2>&1; then
    ok "Hermes gateway: ready → gbrain MCP 포함"
  else
    fail "Hermes gateway: 시작했지만 ready 확인 실패 → logs/hermes.log 확인"
    failed=1
  fi
else
  fail "Hermes gateway: 시작 실패 → logs/hermes.log 확인"
  failed=1
fi

info ""
info "${B}Health check${N}"
failed=0
wait_http "Dify"         80   "http://localhost/"                         120 '^[23]' || failed=1
wait_http "llama-swap"   8080 "http://localhost:8080/v1/models"            30      || failed=1
wait_http "check_metric" 8000 "http://localhost:8000/metrics/user_count"   30      || failed=1
if [ -f "$PAPERCLIP_DIR/data/instances/default/config.json" ]; then
  wait_http "paperclip"  3110 "http://127.0.0.1:3110/api/health"           90      || failed=1
fi
wait_http "backend"      8001 "http://127.0.0.1:8001/api/health"           30      || failed=1
wait_http "web"          3000 "http://localhost:3000/"                     90      || failed=1

health="$(curl -s -m 3 http://127.0.0.1:8001/api/health 2>/dev/null)"
if [ -n "$health" ]; then
  info "  backend → metric sources: $(printf '%s' "$health" | sed -E 's/.*"metric_sources":(.*)\}$/\1/')"
fi

info ""
if [ "$failed" -eq 0 ]; then
  info "${G}${B}모든 서비스 준비 완료${N} → http://localhost:3000"
else
  info "${R}${B}일부 서비스가 응답하지 않습니다.${N} logs/ 의 해당 로그를 확인하세요."
fi
info "종료: ./stop.sh  (이 스크립트가 시작한 프로세스만 종료)"
exit "$failed"
