#!/usr/bin/env bash
# after.sh — native desktop notification when a run lands.
#
# Install:
#   cp skills/autoresearch-hooks/examples/notify.sh .auto/hooks/after.sh
#   chmod +x .auto/hooks/after.sh
#
# Needs `jq` on PATH. Uses osascript on macOS, notify-send on Linux; silently
# does nothing on anything else (or if neither is available) rather than
# failing the hook.
set -euo pipefail

payload="$(cat)"
status="$(jq -r '.run_entry.status // "unknown"' <<<"$payload")"
metric="$(jq -r '.run_entry.metric // empty' <<<"$payload")"
description="$(jq -r '.run_entry.description // ""' <<<"$payload")"
metric_name="$(jq -r '.session.metric_name // "metric"' <<<"$payload")"
goal="$(jq -r '.session.goal // "autoresearch"' <<<"$payload")"

title="$goal — $status"
body="$metric_name: $metric — $description"

if command -v osascript >/dev/null 2>&1; then
  osascript -e "display notification \"$body\" with title \"$title\"" || true
elif command -v notify-send >/dev/null 2>&1; then
  notify-send "$title" "$body" || true
fi

# Nothing printed on stdout — the notification is the point, not a note back
# to the agent. Print something here if you also want it echoed as a note.
