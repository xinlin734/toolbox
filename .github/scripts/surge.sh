#!/usr/bin/env bash
# The 10-second checks (surge.yml) for one region, kept going for
# SURGE_MINUTES after stock was last seen there.
#   surge.sh REGION want    exit 0 if stock was seen within SURGE_MINUTES
#   surge.sh REGION start   start the 10-second checks unless they are running
#   surge.sh REGION         start them if wanted and not running (a watchdog)
# When stock was last seen comes from state.sh (notify_stock.sh records it,
# and each 10-second runner still seeing stock refreshes it). Needs GH_TOKEN
# and GH_REPO.
set -uo pipefail
region=$1 mode=${2:-}
minutes=${SURGE_MINUTES:-30}
want() {
  local at
  at=$(bash "$(dirname "$0")/state.sh" seen-at "$region")
  [ -n "$at" ] && [[ "$at" > "$(date -u -d "-$minutes min" +%FT%TZ)" ]]
}

running() {
  local n
  n=$(gh api "repos/$GH_REPO/actions/workflows/surge.yml/runs?per_page=30" \
        --jq "[.workflow_runs[] | select(.status != \"completed\")
               | select(.display_title | startswith(\"surge $region\"))] | length") || return 1
  [ "${n:-0}" -gt 0 ]
}

start() {
  for i in 1 2 3; do
    gh workflow run surge.yml --ref "${GITHUB_REF_NAME:-main}" -f region="$region" && return 0
    sleep 5
  done
  echo "::warning::没能启动 10 秒加密检查" >&2
  return 1
}

case "$mode" in
  want) want ;;
  start) running || start ;;
  *) if want && ! running; then echo "有货后的 10 秒加密检查断了，重新启动"; start; fi ;;
esac
