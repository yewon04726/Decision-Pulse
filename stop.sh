#!/usr/bin/env bash
# ./dev.sh 가 시작한 프로세스만 종료한다 (.run/<name>.pid 기준).
# - dev.sh 실행 전부터 떠 있던 서비스는 건드리지 않는다.
# - PID 재사용으로 엉뚱한 프로세스를 죽이지 않도록, 기록된 시작 시각과 명령 패턴이 모두 일치할 때만 종료한다.
# - Dify는 dev.sh가 직접 올린 경우에만 'docker compose stop' 으로 멈춘다 (컨테이너/볼륨 유지).
#   docker compose down -v 는 절대 사용하지 않는다.

set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="$ROOT/.run"
DIFY_DIR="$HOME/dify/docker"

if [ -t 1 ]; then
  G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; B=$'\033[1m'; N=$'\033[0m'
else
  G=""; Y=""; R=""; B=""; N=""
fi

stop_pidfile() {
  local file="$1" wait_sec="${2:-10}" name pid started pattern cmd now_started i
  name="$(basename "$file" .pid)"
  pid="$(cut -f1 "$file")"
  started="$(cut -f2 "$file")"
  pattern="$(cut -f3- "$file")"

  if ! kill -0 "$pid" 2>/dev/null; then
    printf '  %s•%s %s: 이미 종료됨 (pid %s)\n' "$Y" "$N" "$name" "$pid"
    rm -f "$file"
    return 0
  fi
  now_started="$(ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^ *//; s/ *$//')"
  if [ -z "$started" ] || [ "$now_started" != "$started" ]; then
    printf '  %s!%s %s: pid %s 의 시작 시각이 기록과 달라(재사용된 PID) 종료하지 않음\n' "$R" "$N" "$name" "$pid"
    rm -f "$file"
    return 1
  fi
  cmd="$(ps -p "$pid" -o command= 2>/dev/null)"
  case "$cmd" in
    *"$pattern"*) ;;
    *)
      printf '  %s!%s %s: pid %s 가 다른 프로세스(%s)라 종료하지 않음\n' "$R" "$N" "$name" "$pid" "$cmd"
      rm -f "$file"
      return 1
      ;;
  esac

  # dev.sh가 별도 프로세스 그룹으로 띄웠으므로 그룹 전체(자식 포함)에 TERM
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  for i in $(seq 1 "$wait_sec"); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
    printf '  %s✓%s %s: 강제 종료 (pid %s)\n' "$G" "$N" "$name" "$pid"
  else
    printf '  %s✓%s %s: 종료 (pid %s)\n' "$G" "$N" "$name" "$pid"
  fi
  rm -f "$file"
}

printf '%sDecision Debt MVP — dev 환경 종료%s\n' "$B" "$N"

found=0
for name in web backend paperclip check-metric llama-swap dify-logs; do
  if [ -f "$RUN_DIR/$name.pid" ]; then
    found=1
    # Paperclip은 embedded PostgreSQL을 정상 종료할 시간을 더 준다
    if [ "$name" = paperclip ]; then stop_pidfile "$RUN_DIR/$name.pid" 30; else stop_pidfile "$RUN_DIR/$name.pid"; fi
  fi
done

if [ -f "$RUN_DIR/dify.started" ]; then
  found=1
  if (cd "$DIFY_DIR" && docker compose stop) >>"$ROOT/logs/dify.log" 2>&1; then
    printf '  %s✓%s Dify: docker compose stop (컨테이너·볼륨 유지)\n' "$G" "$N"
    rm -f "$RUN_DIR/dify.started"
  else
    printf '  %s✗%s Dify: docker compose stop 실패 → logs/dify.log 확인\n' "$R" "$N"
  fi
else
  printf '  %s•%s Dify: dev.sh가 시작하지 않았으므로 그대로 둠\n' "$Y" "$N"
fi

[ "$found" -eq 0 ] && printf '  이 프로젝트가 시작한 프로세스가 없습니다.\n'

still=""
for port in 3000 8001 3110 8000 8080; do
  lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1 && still="$still :$port"
done
[ -n "$still" ] && printf '  (dev.sh 이전부터 실행 중이던 서비스는 유지됨:%s)\n' "$still"

printf '\n%sHermes gateway 종료%s\n' "$B" "$N"

if hermes gateway stop --yes >>"$ROOT/logs/hermes.log" 2>&1; then
  printf '  %s✓%s Hermes gateway: 종료 (gbrain MCP도 함께 종료)\n' "$G" "$N"
else
  printf '  %s!%s Hermes gateway: 종료 실패 또는 이미 종료됨 → logs/hermes.log 확인\n' "$Y" "$N"
fi

exit 0

