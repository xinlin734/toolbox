#!/usr/bin/env bash
# Push new stock in the hits file written by check.py to Bark.
#   notify_stock.sh HITS LINK
# No GitHub issues or comments: the owner watches this repo, so each one
# would also be an email. What has been pushed, and when the region last
# showed stock, is kept as commit statuses on the repo's first commit
# (state.sh), which send no notifications.
# Several runners see the same stock and it flickers, so a store and model
# is pushed at most once per 30 minutes. The order link is the model page
# written by check.py --link-file, else BUY_URL, the product's buy page.
# Stock also starts the 10-second checks for SURGE_REGION, if set.
# Needs GH_TOKEN, GH_REPO and STOCK_ISSUE_TITLE (the push title).
set -uo pipefail
dir=$(dirname "$0")
hits=${1:-hits.txt}
[ -s "$hits" ] || exit 0
order=$(head -n1 "${2:-link.txt}" 2>/dev/null || true)
order=${order:-${BUY_URL:-}}
region=${SURGE_REGION:-us}

bash "$dir/state.sh" seen "$region"
pushed=$(bash "$dir/state.sh" pushed 30)
new=$(grep -vxF -f <(printf '%s\n' "$pushed") "$hits" || true)
if [ -n "$new" ]; then
  python3 "$dir/push_bark.py" "$STOCK_ISSUE_TITLE" "$(cat "$hits")
点开直接进这款的购买页" "$order"
  while IFS= read -r line; do bash "$dir/state.sh" push "$line"; done <<< "$new"
fi
# Then ask every 10 seconds for the next half hour.
if [ -n "${SURGE_REGION:-}" ]; then
  bash "$dir/surge.sh" "$SURGE_REGION" start || true
fi
