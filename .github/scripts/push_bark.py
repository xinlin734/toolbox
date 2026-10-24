#!/usr/bin/env python3
"""Send a Bark push to every configured iPhone; does nothing when none is set.

    push_bark.py TITLE BODY [LINK]

Recipients come from BARK_URL and BARK_URL_2. Each holds whatever the Bark
app's Copy button gives (a curl command or a https://api.day.app/<key>/...
address), several of them, or a bare key. Pushes are critical alerts: they
ring for 30 seconds even when the phone is muted or in Focus, once the
recipient allows Critical Alerts for Bark in iOS Settings. BARK_LEVEL=active
sends an ordinary push instead (no ringing, no Focus bypass).

BARK_QUIET sets quiet hours per recipient, in the same order, separated by
";" or newlines, e.g. "Europe/Paris 22-7; Asia/Tokyo 23-8". During quiet
hours the push still arrives, silently.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENV_NAMES = ("BARK_URL", "BARK_URL_2")
ADDRESS_RE = re.compile(r"(https?://[^/\s'\"]+)/([^/?#\s'\"]+)")
QUIET_RE = re.compile(r"\s*(\S+)\s+(\d{1,2})\s*-\s*(\d{1,2})\s*")


def targets(values):
    """Unique (server, key, source index) from what the users pasted, in order."""
    found, seen = [], set()
    for index, value in enumerate(values):
        value = (value or "").strip()
        pairs = ADDRESS_RE.findall(value)
        if not pairs and value and not re.search(r"\s", value):
            pairs = [("https://api.day.app", value)]
        for pair in pairs:
            if pair not in seen:
                seen.add(pair)
                found.append((*pair, index))
    return found


def quiet_rules(value):
    """[(tz, start_hour, end_hour) or None, ...] from BARK_QUIET."""
    rules = []
    for part in re.split(r"[;\n]", value or ""):
        m = QUIET_RE.fullmatch(part)
        rules.append((m.group(1), int(m.group(2)), int(m.group(3))) if m else None)
    return rules


def is_quiet(rule, now):
    if not rule:
        return False
    tz, start, end = rule
    try:
        hour = now.astimezone(ZoneInfo(tz)).hour
    except (ZoneInfoNotFoundError, ValueError):
        print("::warning::BARK_QUIET 里有一个时区写错了，这台照常响铃")
        return False
    return start <= hour < end if start < end else (hour >= start or hour < end)


def payload(key, title, body, link="", quiet=False, level="critical"):
    data = {"device_key": key, "title": title, "body": body, "group": "stock"}
    if quiet:
        data["level"] = "passive"
    elif level == "active":
        data["level"] = "active"
    else:
        data.update(level="critical", volume="8", sound="alarm", call="1")
    if link:
        data["url"] = link
    return data


def send(server, key, data):
    req = urllib.request.Request(f"{server}/push", data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return "ok"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code} " + e.read().decode("utf-8", "replace")[:200].replace(key, "***")
    except (urllib.error.URLError, TimeoutError) as e:
        # Never echo the URL: it contains the device key.
        return type(e).__name__


def main(title, body, link="", now=None):
    recipients = targets(os.environ.get(name, "") for name in ENV_NAMES)
    if not recipients:
        print("没设置 BARK_URL，跳过 Bark")
        return
    rules = quiet_rules(os.environ.get("BARK_QUIET", ""))
    now = now or datetime.now(timezone.utc)
    for i, (server, key, source) in enumerate(recipients, 1):
        print(f"::add-mask::{key}")
        quiet = is_quiet(rules[source] if source < len(rules) else None, now)
        # Whether a phone is in its quiet hours stays out of the public log.
        result = send(server, key, payload(key, title, body, link, quiet,
                                           os.environ.get("BARK_LEVEL", "critical")))
        if result == "ok":
            print(f"已推送到 Bark（第 {i} 台）")
        else:
            print(f"::warning::Bark 推送失败（第 {i} 台）：{result}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
