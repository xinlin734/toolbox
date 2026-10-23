#!/usr/bin/env bash
# The lanes of the fast checks for REGION, as a JSON list: one per place
# group and STEP-second phase of a PACE-second round (15 / 5: three lanes,
# each place asked every 5 seconds).
#   surge_lanes.sh REGION
set -euo pipefail
if [ "$1" = cn ]; then
  # The same groups as check-cn.yml.
  groups='[
    {"group": "北方", "locations": "天津 天津; 辽宁 沈阳; 山东 烟台; 河南 郑州; 湖北 武汉"},
    {"group": "华东", "locations": "浙江 宁波; 江苏 南京; 福建 厦门; 浙江 温州"},
    {"group": "南方", "locations": "广东 广州; 重庆 重庆; 湖南 长沙; 云南 昆明; 广西壮族自治区 南宁"}]'
else
  groups='[{"group": "美国", "locations": ""}]'
fi
jq -c --argjson pace "${PACE:-15}" --argjson step "${STEP:-5}" '[range(0; $pace; $step) as $p | .[] | . + {phase: $p, lane: "\(.group):\($p)"}]' <<< "$groups"
