#!/usr/bin/env bash
# Run check.py for one target and put only counts in the public log.
#   run_check.sh REGION "<product> <location>" [check.py flags...]
# Optional env LOCATIONS and MODELS: see below.
# Writes code=<exit code> to $GITHUB_OUTPUT: 0 ok, 2 blocked or failed, 4 not configured.
set -uo pipefail
region=$1 target=$2
shift 2
read -r product location <<< "$target" || true
if [ -z "${location:-}" ]; then
  echo "::error::这一组的 Secret 还没设置"
  echo "code=4" >> "$GITHUB_OUTPUT"
  exit 0
fi
echo "::add-mask::$product"
echo "::add-mask::$location"
for word in $location; do echo "::add-mask::$word"; done

# Order link for the alerts; the product slug in it is masked above.
case "$region" in
  cn) export BUY_URL="https://www.apple.com.cn/shop/buy-iphone/$product" ;;
  *) export BUY_URL="https://www.apple.com/shop/buy-iphone/$product" ;;
esac

# LOCATIONS, if set, replaces the secret's location with a public list of
# places ("省 市; 省 市") and counts every store they reach.
if [ -n "${LOCATIONS:-}" ]; then
  args=(--region "$region" --product "$product" --location "$LOCATIONS")
else
  args=(--region "$region" --product "$product" --location "$location")
  # Mainland China: "省 市" — keep only stores in that city.
  [ "$region" = cn ] && args+=(--city "${location##* }")
fi
# MODELS, if set, keeps only those models ("A|B"): alerts, the checkout Mac
# and delivery dates see nothing else.
[ -n "${MODELS:-}" ] && args+=(--model "$MODELS")
python3 check.py "${args[@]}" "$@" > run.log 2>&1
code=$?
echo "code=$code" >> "$GITHUB_OUTPUT"

# run.log names nearby stores; only counts and error lines (masked) reach the public log.
grep -m1 '共找到' run.log
grep -E '⚠️|失败|拦截|没找到|Apple 提示' run.log | sort | uniq -c | head -5
rounds=$(grep -c '可自提 [0-9]* 个' run.log)
echo "查了 $rounds 轮，最后一轮 $(grep -o '[0-9]* 家店 × [0-9]* 个型号 → 可自提 [0-9]* 个' run.log | tail -1)"
# Counts as an annotation too, so a monitor can check every run's rounds
# through the API without downloading logs.
echo "::notice title=rounds::rounds=$rounds failed=$(grep -c '查询失败' run.log) code=$code"
closed=$(grep -c '还没开放自提' run.log)
[ "$closed" -gt 0 ] && echo "其中 $closed 轮自提还没开放"
[ "$code" -eq 0 ] || echo "::warning::被 Apple 拦截或查询失败（不代表没货），换一台机器继续"
exit 0
