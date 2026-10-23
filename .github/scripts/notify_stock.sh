#!/usr/bin/env bash
# Sync the stock issue with the hits file written by check.py.
# One open issue while stock exists: created on first sighting, commented when
# the list changes, closed when everything is gone. The issue is public, so it
# carries only store and model names.
# Each new sighting also goes to Bark when BARK_URL is set. The order link is
# the model page written by check.py --link-file (tapping the push opens that
# model ready to add to bag), else BUY_URL, the product's buy page.
# Stock also starts the 10-second checks for SURGE_REGION, if set.
# Needs GH_TOKEN, OWNER and STOCK_ISSUE_TITLE in the environment.
set -euo pipefail
hits=${1:-hits.txt}
order=$(head -n1 "${2:-link.txt}" 2>/dev/null || true)
order=${order:-${BUY_URL:-}}

# The latest issue with this title: open, or closed within the last 30
# minutes (stock that comes and goes keeps one issue and is reopened).
since=$(date -u -d "-30 min" +%FT%TZ)
read -r num state < <(gh api "repos/$GH_REPO/issues?state=all&sort=updated&direction=desc&per_page=50" \
  --jq "[.[] | select(.pull_request == null and .title == env.STOCK_ISSUE_TITLE)] | .[0] // empty
        | select(.state == \"open\" or .closed_at >= \"$since\") | \"\(.number) \(.state)\"") || true

if [ -s "$hits" ]; then
  sig=$(sha256sum "$hits" | cut -c1-12)
  link=${order:+下单：$order}
  body="$(printf '%s\n\n%s\n\n<!-- sig:%s -->' "$(cat "$hits")" "$link" "$sig")"
  new=$(cat "$hits")
  if [ -z "${num:-}" ]; then
    gh issue create --title "$STOCK_ISSUE_TITLE" --body "$body" > /dev/null
  else
    history=$(gh api "repos/$GH_REPO/issues/$num" --jq "select(.created_at >= \"$since\") | .body"; \
              gh api "repos/$GH_REPO/issues/$num/comments?per_page=100" \
                --jq ".[] | select(.created_at >= \"$since\") | .body")
    [ "$state" = closed ] && gh issue reopen "$num" > /dev/null
    case "$(gh api "repos/$GH_REPO/issues/$num/comments?per_page=100" --jq '.[-1].body // empty')" in
      *"sig:$sig"*) ;;
      *) gh issue comment "$num" --body "$body" > /dev/null ;;
    esac
    # Push only for a store and model not already pushed in the last 30
    # minutes: several runners see the same stock, and it flickers.
    new=$(grep -vxF -f <(printf '%s\n' "$history") "$hits" || true)
  fi
  if [ -n "$new" ]; then
    python3 "$(dirname "$0")/push_bark.py" "$STOCK_ISSUE_TITLE" "$(cat "$hits")
点开直接进这款的购买页" "$order"
  fi
  # Then ask every 10 seconds for the next half hour (SURGE_REGION: cn or us).
  if [ -n "${SURGE_REGION:-}" ]; then
    bash "$(dirname "$0")/surge.sh" "$SURGE_REGION" start || true
  fi
elif [ -n "${num:-}" ] && [ "$state" = open ]; then
  gh issue close "$num" --comment "没货了。" > /dev/null
fi
