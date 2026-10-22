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

num=$(gh issue list --state open --limit 100 --json number,title \
      --jq 'map(select(.title == env.STOCK_ISSUE_TITLE)) | .[0].number // empty')

if [ -s "$hits" ]; then
  sig=$(sha256sum "$hits" | cut -c1-12)
  link=${order:+下单：$order}
  body="$(printf '@%s\n\n%s\n\n%s\n\n<!-- sig:%s -->' "$OWNER" "$(cat "$hits")" "$link" "$sig")"
  if [ -z "$num" ]; then
    gh issue create --title "$STOCK_ISSUE_TITLE" --body "$body" > /dev/null
  else
    last=$(gh issue view "$num" --json body,comments --jq '([.body] + [.comments[].body]) | last')
    case "$last" in
      *"sig:$sig"*) exit 0 ;;
      *) gh issue comment "$num" --body "$body" > /dev/null ;;
    esac
  fi
  python3 "$(dirname "$0")/push_bark.py" "$STOCK_ISSUE_TITLE" "$(cat "$hits")
点开直接进这款的购买页" "$order"
  # Then ask every 10 seconds for the next half hour (SURGE_REGION: cn or us).
  if [ -n "${SURGE_REGION:-}" ]; then
    bash "$(dirname "$0")/surge.sh" "$SURGE_REGION" start || true
  fi
elif [ -n "$num" ]; then
  gh issue close "$num" --comment "没货了。" > /dev/null
fi
