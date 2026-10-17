#!/usr/bin/env python3
"""Send a Bark push to every configured iPhone; does nothing when none is set.

    push_bark.py TITLE BODY [LINK]

Recipients come from BARK_URL and BARK_URL_2. Each holds whatever the Bark
app's Copy button gives (a curl command or a https://api.day.app/<key>/...
address), several of them, or a bare key. The push rings for 30 seconds and
breaks through Focus.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

ENV_NAMES = ("BARK_URL", "BARK_URL_2")
ADDRESS_RE = re.compile(r"(https?://[^/\s'\"]+)/([^/?#\s'\"]+)")


def targets(values):
    """Unique (server, key) pairs from what the users pasted."""
    found = []
    for value in values:
        value = (value or "").strip()
        pairs = ADDRESS_RE.findall(value)
        if not pairs and value and not re.search(r"\s", value):
            pairs = [("https://api.day.app", value)]
        found += [p for p in pairs if p not in found]
    return found


def payload(key, title, body, link=""):
    data = {"device_key": key, "title": title, "body": body, "group": "stock",
            "level": "timeSensitive", "sound": "alarm", "call": "1"}
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


def main(title, body, link=""):
    recipients = targets(os.environ.get(name, "") for name in ENV_NAMES)
    if not recipients:
        print("没设置 BARK_URL，跳过 Bark")
        return
    for i, (server, key) in enumerate(recipients, 1):
        print(f"::add-mask::{key}")
        result = send(server, key, payload(key, title, body, link))
        if result == "ok":
            print(f"已推送到 Bark（第 {i} 台）")
        else:
            print(f"::warning::Bark 推送失败（第 {i} 台）：{result}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
