#!/usr/bin/env bash
# Warn the owner when the checkout Mac has gone quiet.
#   mac_heartbeat.sh
# The owner's Mac mini, which checks out US stock handed to it through
# MAC_TRIGGER, posts a heartbeat every few minutes to the same ntfy topic
# with "-hb" appended (every 15 minutes, which keeps it under ntfy's free
# daily message quota). If none arrived in the last STALE_MINUTES (default
# 40), send an ordinary Bark push, at most once an hour: a Mac that is
# offline or asleep misses every hit.
# Needs GH_TOKEN and GH_REPO (state.sh), MAC_TRIGGER and BARK_URL.
set -uo pipefail
dir=$(dirname "$0")
stale=${STALE_MINUTES:-40}
[ -n "${MAC_TRIGGER:-}" ] || { echo "没设置 MAC_TRIGGER，跳过"; exit 0; }
# Only a successful answer with no heartbeat in it means the Mac is quiet;
# an ntfy error or timeout says nothing about the Mac.
ok=""
for i in 1 2 3; do
  body=$(curl -sS -f -m 20 "${MAC_TRIGGER}-hb/json?poll=1&since=${stale}m") && { ok=1; break; }
  [ "$i" -lt 3 ] && sleep 5
done
if [ -z "$ok" ]; then
  echo "::warning::读不到心跳（ntfy 出错），这一轮不判断 Mac 是否在线"; exit 0
fi
beats=$(printf '%s\n' "$body" | grep -c '"event":"message"' || true)
if [ "${beats:-0}" -gt 0 ]; then
  echo "Mac 在线：最近 $stale 分钟有 $beats 次心跳"; exit 0
fi
line="mac-heartbeat"
if bash "$dir/state.sh" pushed 60 | grep -qxF "$line"; then
  echo "Mac 没有心跳，这一小时已经提醒过"; exit 0
fi
echo "::warning::Mac 已经 $stale 分钟没有心跳"
BARK_LEVEL=active python3 "$dir/push_bark.py" "Mac 掉线了" \
  "抢购用的 Mac mini 已经 $stale 分钟没有报平安，有货也下不了单。看看它的网络、是不是睡眠或关机了。"
bash "$dir/state.sh" push "$line"
