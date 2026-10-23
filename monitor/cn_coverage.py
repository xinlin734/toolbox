"""Coverage report for check-cn: per group, how much of the last N minutes a
check job was running, the gaps, and blocks.  usage: coverage.py MINUTES"""
import json, subprocess, sys
from datetime import datetime, timezone, timedelta

def gh(path):
    return json.loads(subprocess.check_output(["gh", "api", path]))

def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None

mins = int(sys.argv[1]) if len(sys.argv) > 1 else 30
now = datetime.now(timezone.utc)
start = now - timedelta(minutes=mins)
runs = gh("repos/xinlin734/toolbox/actions/workflows/check-cn.yml/runs?per_page=60")["workflow_runs"]
spans = {"北方": [], "华东": [], "南方": []}
done_jobs = []
nruns = rescues = failed = 0
for r in runs:
    if r["event"] == "push" or r["head_branch"] != "main":
        continue
    if ts(r["updated_at"]) < start - timedelta(minutes=25):
        continue
    nruns += 1
    if r["conclusion"] not in (None, "success", "skipped"):
        failed += 1
    for j in gh(f"repos/xinlin734/toolbox/actions/runs/{r['id']}/jobs")["jobs"]:
        name = j["name"]
        if not name.startswith("check (") or j["conclusion"] == "skipped":
            continue
        g = name[7:9]
        a, b = ts(j["started_at"]), ts(j["completed_at"]) or now
        if b - a < timedelta(seconds=20):  # skipped steps in a rescue run
            continue
        spans[g].append((a, b))
        if j["completed_at"]:
            done_jobs.append((g, j["id"], b - a))
print(f"{start:%H:%M}–{now:%H:%M} UTC 共 {nruns} 次运行，失败 {failed}")
for g, sp in spans.items():
    sp.sort()
    cur, gaps, covered = start, [], timedelta()
    for a, b in sp:
        a, b = max(a, start), min(b, now)
        if b <= cur:
            continue
        if a > cur:
            gaps.append((cur, a))
            covered += b - a
        else:
            covered += b - cur
        cur = b
    if cur < now - timedelta(seconds=90):
        gaps.append((cur, now))
    big = [f"{x:%H:%M:%S}-{y:%H:%M:%S}" for x, y in gaps if y - x > timedelta(seconds=90)]
    pct = 100 * covered / (now - start)
    print(f"  {g}: 覆盖 {pct:.0f}%  超过90秒的空档 {len(big)} {' '.join(big)}")
# Each finished job posts a "rounds" annotation (from 2026-10-22 15:00 UTC):
# a full 4-minute job should have about 4 rounds, about one a minute.
short, failed, missing = [], 0, 0
blocked_notes = []  # from 2026-10-23 00:35 UTC blocks are run annotations, not issue comments
for g, jid, dur in done_jobs:
    anns = gh(f"repos/xinlin734/toolbox/check-runs/{jid}/annotations")
    blocked_notes += [n["message"] for n in anns if n.get("title") == "blocked"]
    notes = [n["message"] for n in anns if n.get("title") == "rounds"]
    if not notes:
        missing += 1
        continue
    kv = dict(x.split("=") for x in notes[0].split())
    failed += int(kv.get("failed", 0))
    if kv.get("surge"):  # stepped aside for the fast checks
        continue
    if dur.total_seconds() > 200 and int(kv.get("rounds", 0)) < 3:
        short.append(f"{g}:{jid}:{kv.get('rounds')}轮")
print(f"逐台核对: {len(done_jobs) - missing} 台有记录，查询失败 {failed} 次，"
      f"轮数不足 {len(short)} 台 {' '.join(short[:5])}（无记录 {missing} 台）")
iss = gh("repos/xinlin734/toolbox/issues?state=all&per_page=20")
for i in iss:
    if i["title"] == "检查被拦记录":
        cs = gh(f"repos/xinlin734/toolbox/issues/{i['number']}/comments?per_page=100")
        notes = [(i["created_at"], i["body"])] + [(c["created_at"], c["body"]) for c in cs]
        recent = [b for t, b in notes if ts(t) >= start]
        print(f"被拦 {len(recent)} 次 (issue #{i['number']})")
        for b in recent[-6:]:
            print("   ", b.split("[运行记录]")[0].replace("@xinlin734 ", ""))
        break
else:
    print("被拦 0 次")

# --- US: one watcher chain. Before MINUTE_FROM a run asks once and exits
# while Apple has pickup switched off, so judge it by the gap between check
# starts; from MINUTE_FROM on it should be covered like China.
us = gh("repos/xinlin734/toolbox/actions/workflows/check-us.yml/runs?per_page=60")["workflow_runs"]
starts, uspans, usfail = [], [], 0
for r in us:
    if r["event"] == "push" or r["head_branch"] != "main" or ts(r["updated_at"]) < start - timedelta(minutes=25):
        continue
    if r["conclusion"] not in (None, "success", "skipped"):
        usfail += 1
    for j in gh(f"repos/xinlin734/toolbox/actions/runs/{r['id']}/jobs")["jobs"]:
        if j["name"] == "check" and j["started_at"]:
            starts.append(ts(j["started_at"]))
            uspans.append((ts(j["started_at"]), ts(j["completed_at"]) or now))
starts = sorted(s for s in starts if s >= start - timedelta(minutes=10))
gaps = [(a, b) for a, b in zip(starts, starts[1:] + [now]) if b - a > timedelta(minutes=6)]
print(f"美国: {len(starts)} 次检查，失败 {usfail}，两次检查间隔超过 6 分钟 {len(gaps)} "
      + " ".join(f"{a:%H:%M}-{b:%H:%M}" for a, b in gaps))

# The 10-second surge runs (surge.yml, from 2026-10-22 23:24 UTC): per group,
# whether lanes were running throughout, and each lane's round count.
sruns = []
for page in range(1, 8):  # ~9 runs a minute while the fast checks run
    batch = gh(f"repos/xinlin734/toolbox/actions/workflows/surge.yml/runs?per_page=100&page={page}")["workflow_runs"]
    sruns += batch
    if not batch or ts(batch[-1]["updated_at"]) < start:
        break
sruns = [r for r in sruns if ts(r["updated_at"]) >= start]
if sruns:
    lanes, sfail, sshort, sbad = {}, 0, [], 0
    for r in sruns:
        if r["conclusion"] not in (None, "success"):
            sbad += 1
        title = r["display_title"].split()
        for j in gh(f"repos/xinlin734/toolbox/actions/runs/{r['id']}/jobs")["jobs"]:
            if not j["name"].startswith("check") or not j["started_at"] or j["conclusion"] == "skipped":
                continue
            # One lane per run ("surge cn <chain> 北方:10"); before 23:31 a matrix "check (北方, ...)".
            g = title[3].split(":")[0] if len(title) > 3 else j["name"][7:9]
            lane = title[3] if len(title) > 3 else g
            lanes.setdefault(lane, []).append((ts(j["started_at"]), ts(j["completed_at"]) or now))  # noqa
            if j["completed_at"]:
                anns = gh(f"repos/xinlin734/toolbox/check-runs/{j['id']}/annotations")
                blocked_notes += [n["message"] for n in anns if n.get("title") == "blocked"]
                notes = [n["message"] for n in anns if n.get("title") == "rounds"]
                if notes:
                    kv = dict(x.split("=") for x in notes[0].split())
                    sfail += int(kv.get("failed", 0))
                    if int(kv.get("rounds", 0)) < 1:
                        sshort.append(f"{g}:{j['id']}")
    print(f"快速检查(5秒): {len(sruns)} 次运行，失败 {sbad}，查询失败 {sfail}，轮数不足 {len(sshort)} {' '.join(sshort[:5])}")
    # Each lane (a group and a 10-second phase) should always have a runner:
    # the next one starts before the last one stops.
    gaps = []
    for lane, sp in sorted(lanes.items()):
        sp.sort()
        for (a1, b1), (a2, b2) in zip(sp, sp[1:]):
            if b1 < now and a2 - b1 > timedelta(seconds=15):  # b1 == now: still running
                gaps.append(f"{lane} {b1:%H:%M:%S}-{a2:%H:%M:%S}")
    print(f"  {len(lanes)} 条，每条接力断开超过15秒 {len(gaps)} 次 {' '.join(gaps[:6])}")
print(f"被拦（运行注释）{len(blocked_notes)} 次")
for b in blocked_notes[-6:]:
    print("   ", b)
# What matters: every group checked all the time, by its regular job or its
# fast-check lanes (a group's lanes can end while others run).
def gaps_of(sp):
    sp = sorted(sp)
    cur, out = start, []
    for x, y in sp:
        if x - cur > timedelta(seconds=90):
            out.append(f"{max(cur, start):%H:%M:%S}-{x:%H:%M:%S}")
        cur = max(cur, y)
    if now - cur > timedelta(seconds=90):
        out.append(f"{cur:%H:%M:%S}-{now:%H:%M:%S}")
    return out
for g in ("北方", "华东", "南方"):
    allsp = list(spans.get(g, []))
    if sruns:
        allsp += [x for lane, sp in lanes.items() if lane.split(":")[0] == g for x in sp]
    gg = gaps_of(allsp)
    print(f"合并覆盖 {g}: 没人在查超过90秒 {len(gg)} 次 {' '.join(gg[:4])}")
# No issue may be written any more (each one emails the owner).
recent_issues = [i for i in gh("repos/xinlin734/toolbox/issues?state=all&sort=updated&direction=desc&per_page=30")
                 if not i.get("pull_request") and ts(i["updated_at"]) >= max(start, ts("2026-10-23T00:40:00Z"))]
print(f"新写的 issue（不应有）{len(recent_issues)} 个 {' '.join('#%d' % i['number'] for i in recent_issues[:5])}")
