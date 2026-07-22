#!/usr/bin/env bash
# after.sh — append a one-line, human-readable entry to .auto/learnings.md
# for every run, so the history is skimmable without parsing log.jsonl.
#
# Install:
#   cp skills/autoresearch-hooks/examples/journal.sh .auto/hooks/after.sh
#   chmod +x .auto/hooks/after.sh
#
# Needs `jq` on PATH.
set -euo pipefail

payload="$(cat)"
cwd="$(jq -r '.cwd' <<<"$payload")"
run="$(jq -r '.run_entry.run // "?"' <<<"$payload")"
status="$(jq -r '.run_entry.status // "unknown"' <<<"$payload")"
metric="$(jq -r '.run_entry.metric // "?"' <<<"$payload")"
description="$(jq -r '.run_entry.description // ""' <<<"$payload")"
metric_name="$(jq -r '.session.metric_name // "metric"' <<<"$payload")"
metric_unit="$(jq -r '.session.metric_unit // ""' <<<"$payload")"

journal="$cwd/.auto/learnings.md"
mkdir -p "$(dirname "$journal")"
[ -f "$journal" ] || printf '# Learnings\n\n' > "$journal"

printf -- '- #%s **%s** — %s: %s%s — %s\n' \
  "$run" "$status" "$metric_name" "$metric" "$metric_unit" "$description" \
  >> "$journal"

# Echoed back as a note so the agent sees its own history summarised without
# having to open the file.
tail -n 5 "$journal"
