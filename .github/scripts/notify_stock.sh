#!/usr/bin/env bash
# Sync the stock issue with the hits file written by check.py.
# One open issue while stock exists: created on first sighting, commented when
# the list changes, closed when everything is gone. The issue is public, so it
# carries only store and model names.
# Each new sighting also goes to Bark when BARK_URL is set. BUY_URL, if set,
# is added as the order link.
# Needs GH_TOKEN, OWNER and STOCK_ISSUE_TITLE in the environment.
set -euo pipefail
hits=${1:-hits.txt}

num=$(gh issue list --state open --limit 100 --json number,title \
      --jq 'map(select(.title == env.STOCK_ISSUE_TITLE)) | .[0].number // empty')

if [ -s "$hits" ]; then
  sig=$(sha256sum "$hits" | cut -c1-12)
  link=${BUY_URL:+下单：$BUY_URL}
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
  python3 "$(dirname "$0")/push_bark.py" "$STOCK_ISSUE_TITLE" "$(cat "$hits")" "${BUY_URL:-}"
elif [ -n "$num" ]; then
  gh issue close "$num" --comment "没货了。" > /dev/null
fi
