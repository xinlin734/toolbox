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
region=$1 mode=${2:-} group=${3:-}
minutes=${SURGE_MINUTES:-30}
# The owner wants the US asked every 5 seconds at all times (2026-10-27), so
# its 5-second lanes never stop for lack of stock and the watchdog keeps
# them going.
always=$([ "$region" = us ] && echo 1)
[ "$region" = us ] && export PACE=${PACE:-16} STEP=${STEP:-2}
want() {
  local at
  [ -n "$always" ] && return 0
  at=$(bash "$(dirname "$0")/state.sh" seen-at "$region")
  [ -n "$at" ] && [[ "$at" > "$(date -u -d "-$minutes min" +%FT%TZ)" ]]
}

count() {
  gh api "repos/$GH_REPO/actions/workflows/surge.yml/runs?per_page=30" \
    --jq "[.workflow_runs[] | select(.status != \"completed\")
           | select(.display_title | startswith(\"surge $region\"))
           | select(\"$group\" == \"\" or (.display_title | contains(\" $group:\")))] | length"
}

running() {
  # With a group (北方 …), only that group's lanes count: lanes end on their
  # own once their group has had no stock for 30 minutes.
  local n
  n=$(gh api "repos/$GH_REPO/actions/workflows/surge.yml/runs?per_page=30" \
        --jq "[.workflow_runs[] | select(.status != \"completed\")
               | select(.display_title | startswith(\"surge $region\"))
               | select(\"$group\" == \"\" or (.display_title | contains(\" $group:\")))] | length") || return 1
  [ "${n:-0}" -gt 0 ]
}

# The lanes (surge_lanes.sh) with no run in progress or queued. Asked twice,
# 20 seconds apart, so a lane caught between two runs of its chain (or a
# rescue that is still being created) is not taken for a gap.
missing() {
  local want have again
  want=$(bash "$(dirname "$0")/surge_lanes.sh" "$region" | jq -r '.[].lane' | sort)
  lanes() {
    gh api "repos/$GH_REPO/actions/workflows/surge.yml/runs?per_page=50" \
      --jq ".workflow_runs[] | select(.status != \"completed\")
            | select(.display_title | startswith(\"surge $region \")) | .display_title
            | split(\" \") | last" | sort -u
  }
  have=$(lanes) || return 1
  [ -z "$(comm -23 <(echo "$want") <(echo "$have"))" ] && return 0
  sleep 20
  again=$(lanes) || return 1
  comm -23 <(echo "$want") <(sort -u <(echo "$have"; echo "$again"))
}

# Restart one lane on its own, keeping the common clock (each lane's phase
# is fixed, so it can start at any time).
start_lane() {
  for i in 1 2 3 4 5; do
    gh workflow run surge.yml --ref "${GITHUB_REF_NAME:-main}" -f region="$region" \
      -f chain="watchdog-${GITHUB_RUN_ID:-0}" -f lane="$1" ${always:+-f keep_until=9999999999} && return 0
    sleep $(( i * 5 ))
  done
  echo "::warning::没能补上快查 $1" >&2
  return 1
}

start() {
  for i in 1 2 3; do
    gh workflow run surge.yml --ref "${GITHUB_REF_NAME:-main}" -f region="$region" \
      ${always:+-f keep_until=9999999999} && return 0
    sleep 5
  done
  echo "::warning::没能启动 10 秒加密检查" >&2
  return 1
}

case "$mode" in
  want) want ;;
  running) running ;;  # surge.sh REGION running [GROUP]
  count) count ;;      # surge.sh REGION count [GROUP]: runs in progress
  start) running || start ;;
  *) if want && ! running; then echo "有货后的 10 秒加密检查断了，重新启动"; start
     elif [ -n "$always" ]; then
       # Lanes always run here, so a single lane whose chain broke (a failed
       # hand-over, say) is restarted on its own while the others keep going.
       for lane in $(missing); do echo "快查 $lane 断了，补上"; start_lane "$lane"; done
     fi ;;
esac
