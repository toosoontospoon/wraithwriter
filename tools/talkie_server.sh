#!/usr/bin/env bash
# Local CPU server for the talkie base model. Weights stay outside this repo.
#
#   TALKIE_MODEL=/path/to/model.gguf tools/talkie_server.sh start
#   tools/talkie_server.sh start /path/to/model.gguf
#   tools/talkie_server.sh stop
#
# TALKIE_THREADS sets both generation threads (-t) and batch threads (-tb).
# TALKIE_WAIT_LOAD, when set, waits until the 1-minute load average is below it.
set -euo pipefail

IMAGE="${TALKIE_IMAGE:-ghcr.io/ggml-org/llama.cpp:server}"
NAME="${TALKIE_CONTAINER:-talkie}"
PORT="${TALKIE_PORT:-8080}"
THREADS="${TALKIE_THREADS:-8}"
CTX="${TALKIE_CTX:-16384}"
NP="${TALKIE_NP:-4}"
WAIT_LOAD="${TALKIE_WAIT_LOAD:-}"

cmd="${1:-start}"
if [[ $# -gt 0 ]]; then
  shift
fi

wait_for_load() {
  if [[ -z "$WAIT_LOAD" ]]; then
    return
  fi
  echo "waiting until load average is below ${WAIT_LOAD}"
  while true; do
    load=$(cut -d' ' -f1 /proc/loadavg)
    if awk -v l="$load" -v t="$WAIT_LOAD" 'BEGIN { exit !(l+0 < t+0) }'; then
      return
    fi
    echo "busy load ${load}"
    sleep 15
  done
}

wait_for_health() {
  echo "waiting for http://127.0.0.1:${PORT}/health"
  for _ in $(seq 1 120); do
    if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null; then
      echo "talkie ready"
      return
    fi
    sleep 2
  done
  echo "talkie did not become healthy" >&2
  exit 1
}

stop_server() {
  docker stop "$NAME"
}

start_server() {
  local model="${1:-${TALKIE_MODEL:-}}"
  if [[ -z "$model" ]]; then
    echo "Pass a GGUF path, or set TALKIE_MODEL." >&2
    exit 2
  fi
  if [[ ! -f "$model" ]]; then
    echo "Model file not found." >&2
    exit 2
  fi
  wait_for_load
  if [[ -n "$(docker ps -q -f "name=^${NAME}$")" ]]; then
    echo "container ${NAME} is already running; reusing it"
    wait_for_health
    return
  fi
  local dir base
  dir=$(dirname "$(realpath "$model")")
  base=$(basename "$model")
  docker run -d --name "$NAME" --rm \
    -p "127.0.0.1:${PORT}:8080" \
    -v "${dir}:/models:ro" \
    "$IMAGE" \
    -m "/models/${base}" \
    --host 0.0.0.0 --port 8080 \
    -c "$CTX" -np "$NP" -t "$THREADS" -tb "$THREADS"
  wait_for_health
}

case "$cmd" in
  start) start_server "${1:-}" ;;
  stop) stop_server ;;
  *)
    echo "usage: talkie_server.sh start [MODEL] | stop" >&2
    exit 2
    ;;
esac
