#!/usr/bin/env bash
# Small shared state for the checkers, kept as commit statuses on the repo's
# first commit: unlike issues they send no emails. Best effort: a failed
# call prints a warning and the checks go on.
#   state.sh seen REGION      record that REGION shows stock now
#   state.sh seen-at REGION   print when REGION last showed stock (ISO time), or nothing
#   state.sh push LINE        record that LINE (a store and model) was pushed
#   state.sh pushed MINUTES   print the lines pushed in the last MINUTES
#   state.sh claim LINE       record LINE as pushed; succeed only if no other
#                             runner recorded it in the last minute first
# Needs GH_TOKEN and GH_REPO; the workflow needs statuses: write.
set -uo pipefail
sha=96f27b21317d17e1fa0975a594ef21c8c53bf444
post() {
  gh api -X POST "repos/$GH_REPO/statuses/$sha" -f state=success -f context="$1" -f description="$2" \
    > /dev/null 2>&1 || echo "::warning::状态没记上：$1" >&2
}
case "$1" in
  seen) post "seen/$2" "$(date +%s)" ;;
  seen-at)
    gh api "repos/$GH_REPO/commits/$sha/status?per_page=100" \
      --jq ".statuses[] | select(.context == \"seen/$2\") | .updated_at" 2>/dev/null ;;
  push) post "pushed/$(printf '%s' "$2" | sha1sum | cut -c1-12)" "${2:0:140}" ;;
  claim)
    # Runners a few seconds apart see the same stock; the first to record
    # the line (lowest status id) pushes it, the rest stay quiet.
    context="pushed/$(printf '%s' "$2" | sha1sum | cut -c1-12)"
    mine=$(gh api -X POST "repos/$GH_REPO/statuses/$sha" -f state=success -f context="$context" \
             -f description="${2:0:140}" --jq .id 2>/dev/null) || exit 0  # can't tell: push
    since=$(date -u -d "-1 min" +%FT%TZ)
    first=$(gh api "repos/$GH_REPO/commits/$sha/statuses?per_page=100" \
              --jq "[.[] | select(.context == \"$context\" and .created_at >= \"$since\") | .id] | min" \
              2>/dev/null) || exit 0
    [ -z "$first" ] || [ "$first" = null ] || [ "$first" -ge "$mine" ] ;;
  pushed)
    since=$(date -u -d "-$2 min" +%FT%TZ)
    # Newest first; three pages reach well past 30 minutes.
    for page in 1 2 3; do
      gh api "repos/$GH_REPO/commits/$sha/statuses?per_page=100&page=$page" \
        --jq ".[] | select(.created_at >= \"$since\") | select(.context | startswith(\"pushed/\")) | .description" \
        2>/dev/null
    done ;;
esac
