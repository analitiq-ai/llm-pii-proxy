#!/usr/bin/env bash
# SC-002 / SC-008: run every task in tasks.yaml 3 times direct and 3 times through the proxy, in a fresh
# copy of tests/fixtures/pii-project/ each time, and print both success rates and their ratio.
#
# Calls the real API. Start the proxy first (quickstart "Run"); to also capture traffic for analyze.py,
# point the proxy's upstream at recorder.py. Usage: scripts/e2e/run_tasks.sh [task id ...]
#   PROXY_URL  proxy base URL (default http://127.0.0.1:8787)
#   RUNS       runs per task per mode (default 3)
#   OUT_DIR    where results go (default: a new temporary directory)
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
FIXTURE="$REPO/tests/fixtures/pii-project"
TASKS="$HERE/tasks.yaml"
PROXY_URL=${PROXY_URL:-http://127.0.0.1:8787}
RUNS=${RUNS:-3}
OUT_DIR=${OUT_DIR:-$(mktemp -d)}
PY=(uv run --project "$REPO" python)

if [ $# -gt 0 ]; then
  IDS=("$@")
else
  mapfile -t IDS < <("${PY[@]}" -c 'import sys, yaml; [print(t["id"]) for t in yaml.safe_load(open(sys.argv[1]))["tasks"]]' "$TASKS")
fi

declare -A PASSED=([direct]=0 [proxy]=0) TOTAL=([direct]=0 [proxy]=0)
for id in "${IDS[@]}"; do
  prompt=$("${PY[@]}" -c 'import sys, yaml; print(next(t["prompt"] for t in yaml.safe_load(open(sys.argv[1]))["tasks"] if t["id"] == sys.argv[2]))' "$TASKS" "$id")
  for mode in direct proxy; do
    for run in $(seq 1 "$RUNS"); do
      work="$OUT_DIR/$id/$mode-$run"
      mkdir -p "$work" && cp -a "$FIXTURE/." "$work/"
      base=()
      [ "$mode" = proxy ] && base=(ANTHROPIC_BASE_URL="$PROXY_URL")
      # Both modes alike: thinking on (exercises signature checks), no attribution header (research R3).
      (cd "$work" && env "${base[@]}" CLAUDE_CODE_ATTRIBUTION_HEADER=0 claude -p \
        --setting-sources project --settings '{"alwaysThinkingEnabled": true}' \
        --permission-mode acceptEdits --allowedTools "Read,Edit,Write,Bash(cat:*),Bash(ls:*)" \
        --output-format json "$prompt" > "$OUT_DIR/$id/$mode-$run.json" 2> "$OUT_DIR/$id/$mode-$run.err") || true
      verdict=$("${PY[@]}" "$HERE/check.py" "$TASKS" "$id" "$FIXTURE" "$work" "$OUT_DIR/$id/$mode-$run.json") && PASSED[$mode]=$(( PASSED[$mode] + 1 ))
      TOTAL[$mode]=$(( TOTAL[$mode] + 1 ))
      echo "$id $mode $run: $verdict"
    done
  done
done

rate() { awk -v p="$1" -v t="$2" 'BEGIN { printf "%.1f%%", t ? 100 * p / t : 0 }'; }
echo
echo "direct: ${PASSED[direct]}/${TOTAL[direct]} ($(rate "${PASSED[direct]}" "${TOTAL[direct]}"))"
echo "proxy:  ${PASSED[proxy]}/${TOTAL[proxy]} ($(rate "${PASSED[proxy]}" "${TOTAL[proxy]}"))"
awk -v d="${PASSED[direct]}" -v p="${PASSED[proxy]}" \
  'BEGIN { if (d) printf "ratio:  %.1f%% (SC-008: >= 95%%)\n", 100 * p / d; else print "ratio:  n/a (no direct passes)" }'
echo "results: $OUT_DIR (holds real values from the fixture; delete when done)"
