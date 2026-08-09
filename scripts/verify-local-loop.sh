#!/usr/bin/env bash
# One-command interview proof for the shipped local memory loop.
#
# Starts only missing local services, refreshes one real T2 day card through the
# private MLX adapter, then verifies the same store over REST, Diary and MCP.
# T3 is queried read-only: this never writes a fake preference into real memory.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VERIFY_DATE="${1:-}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mindbridge-verify.XXXXXX")"
STARTED_DOCKER=0
STARTED_OLLAMA=0
STARTED_DB=0
STARTED_REDIS=0
MLX_PID=""
API_PID=""
WEB_PID=""

finish() {
  status=$?
  trap - EXIT INT TERM

  for pid in "$WEB_PID" "$API_PID" "$MLX_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  if ((STARTED_REDIS)); then docker compose stop redis >/dev/null 2>&1 || true; fi
  if ((STARTED_DB)); then docker compose stop db >/dev/null 2>&1 || true; fi
  if ((STARTED_OLLAMA)); then
    osascript -e 'tell application "Ollama" to quit' >/dev/null 2>&1 || true
  fi
  if ((STARTED_DOCKER)); then
    osascript -e 'tell application "Docker" to quit' >/dev/null 2>&1 || true
  fi
  find "$TMP_DIR" -type f -delete 2>/dev/null || true
  rmdir "$TMP_DIR" 2>/dev/null || true
  exit "$status"
}
trap finish EXIT INT TERM

fail() { printf 'FAIL  %s\n' "$*" >&2; exit 1; }

wait_for_url() {
  label="$1"
  url="$2"
  pid="${3:-}"
  attempts="${4:-120}"
  for ((i = 1; i <= attempts; i++)); do
    if curl --silent --fail --max-time 2 "$url" >/dev/null 2>&1; then
      printf 'READY %s\n' "$label"
      return 0
    fi
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      fail "$label exited before becoming ready (see $TMP_DIR)"
    fi
    sleep 1
  done
  fail "$label did not become ready (see $TMP_DIR)"
}

[[ -x .venv/bin/python ]] || fail "missing .venv; install requirements first"
[[ -x .venv/bin/mlx_lm.server ]] || fail "mlx_lm.server is not installed in .venv"
[[ -f train/outputs/mlx-adapters/adapters.safetensors ]] || \
  fail "missing private adapter: train/outputs/mlx-adapters/adapters.safetensors"
command -v docker >/dev/null || fail "Docker CLI is not installed"
command -v curl >/dev/null || fail "curl is not installed"

if ! docker info >/dev/null 2>&1; then
  printf 'START Docker Desktop\n'
  open -gj -a Docker
  STARTED_DOCKER=1
  for ((i = 1; i <= 120; i++)); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  docker info >/dev/null 2>&1 || fail "Docker Desktop did not become ready"
fi

[[ -n "$(docker compose ps --status running -q db)" ]] || STARTED_DB=1
[[ -n "$(docker compose ps --status running -q redis)" ]] || STARTED_REDIS=1
printf 'START Postgres + Redis\n'
docker compose up -d --wait db redis

if ! curl --silent --fail --max-time 2 http://127.0.0.1:11434/api/tags \
  >"$TMP_DIR/ollama-tags.json" 2>/dev/null; then
  printf 'START Ollama\n'
  open -gj -a Ollama
  STARTED_OLLAMA=1
  wait_for_url "Ollama" "http://127.0.0.1:11434/api/tags" "" 60
  curl --silent --fail http://127.0.0.1:11434/api/tags >"$TMP_DIR/ollama-tags.json"
fi
.venv/bin/python - "$TMP_DIR/ollama-tags.json" <<'PY' || \
  fail "Ollama model nomic-embed-text is missing; run: ollama pull nomic-embed-text"
import json, sys
models = json.load(open(sys.argv[1], encoding="utf-8")).get("models", [])
raise SystemExit(0 if any(m.get("name", "").startswith("nomic-embed-text") for m in models) else 1)
PY

if ! curl --silent --fail --max-time 2 http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then
  printf 'START Qwen2.5-3B + private MLX adapter\n'
  .venv/bin/mlx_lm.server \
    --model mlx-community/Qwen2.5-3B-Instruct-4bit \
    --adapter-path train/outputs/mlx-adapters \
    --host 127.0.0.1 --port 8080 --max-tokens 1200 --temp 0.2 \
    >"$TMP_DIR/mlx.log" 2>&1 &
  MLX_PID=$!
  wait_for_url "MLX server" "http://127.0.0.1:8080/v1/models" "$MLX_PID" 180
fi

if ! curl --silent --fail --max-time 2 http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
  printf 'START FastAPI\n'
  .venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 \
    >"$TMP_DIR/api.log" 2>&1 &
  API_PID=$!
  wait_for_url "FastAPI" "http://127.0.0.1:8000/healthz" "$API_PID" 60
fi

if ! curl --silent --fail --max-time 2 http://127.0.0.1:3000/api/diary >/dev/null 2>&1; then
  if [[ ! -f .next/BUILD_ID ]]; then
    printf 'BUILD Next.js\n'
    npm run build >"$TMP_DIR/next-build.log" 2>&1
  fi
  printf 'START Next.js diary\n'
  node_modules/.bin/next start --hostname 127.0.0.1 --port 3000 \
    >"$TMP_DIR/next.log" 2>&1 &
  WEB_PID=$!
  wait_for_url "Next.js diary" "http://127.0.0.1:3000/api/diary" "$WEB_PID" 60
fi

args=()
if [[ -n "$VERIFY_DATE" ]]; then args+=(--date "$VERIFY_DATE"); fi
.venv/bin/python -m scripts.verify_local_loop "${args[@]}"
