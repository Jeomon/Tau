#!/usr/bin/env bash
# Benchmark: cold-process wall-clock time to a ready-to-type TUI.
#
# Runs the interactive startup path (Runtime.create -> App.create) in a
# fresh `python` process, timed from the outside so python-interpreter and
# import overhead — often the biggest lever for CLI startup latency — is
# included, not just the async setup work inside it.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  PYTHON="python3"
fi

runs=5
best=""
for i in $(seq 1 "$runs"); do
  start=$(date +%s.%N)
  if ! "$PYTHON" .auto/bench_tui_startup.py > /tmp/ar-out.txt 2>&1; then
    cat /tmp/ar-out.txt
    exit 1
  fi
  end=$(date +%s.%N)
  elapsed=$(echo "$end - $start" | bc)
  if [ -z "$best" ] || (( $(echo "$elapsed < $best" | bc -l) )); then
    best="$elapsed"
  fi
done

echo "METRIC seconds=$best"
