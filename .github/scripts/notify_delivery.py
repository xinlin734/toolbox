#!/usr/bin/env python3
"""Notify when a model's delivery date beats the earliest date seen so far.

    notify_delivery.py DELIVERY_JSON STATE_JSON

DELIVERY_JSON is written by check.py --delivery-file. STATE_JSON keeps the
earliest date seen per model; the workflow carries it between runs in the
Actions cache so it never appears in the public repo. The first run only
records a baseline. Each improvement opens an issue that @-mentions the owner
and is closed right away: the notification is the point, not the issue.
Needs gh plus GH_TOKEN and OWNER in the environment.
"""

import json
import os
import subprocess
import sys

TITLE = "寄送日期提前了"


def short(date):
    return f"{int(date[4:6])}/{int(date[6:8])}" if date else "没有日期"


def plan(current, best):
    """Return (new_best, message or None); best is None when there is no baseline yet."""
    if best is None:
        return {part: d["date"] for part, d in current.items() if d["date"]}, None
    best = dict(best)
    improved = []
    for part, d in sorted(current.items(), key=lambda kv: kv[1]["label"]):
        if d["date"] and (part not in best or d["date"] < best[part]):
            improved.append(f"- {d['label']}：之前最早 {short(best.get(part))} → **{d['text']}**")
            best[part] = d["date"]
    return best, "\n".join(improved) or None


def main(delivery_path, state_path):
    with open(delivery_path, encoding="utf-8") as f:
        current = json.load(f)
    if not current:
        return
    try:
        with open(state_path, encoding="utf-8") as f:
            best = json.load(f)
    except (OSError, ValueError):
        best = None
    best, message = plan(current, best)
    if message:
        url = subprocess.run(["gh", "issue", "create", "--title", TITLE,
                              "--body", f"@{os.environ['OWNER']}\n\n{message}"],
                             check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["gh", "issue", "close", url], check=True, capture_output=True)
    os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(best, f, sort_keys=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
