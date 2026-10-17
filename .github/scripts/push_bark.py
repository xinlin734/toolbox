#!/usr/bin/env python3
"""Send a Bark push to the owner's iPhone; does nothing when BARK_URL is unset.

    push_bark.py TITLE BODY [LINK]

BARK_URL is whatever the Bark app's Copy button gives (a curl command or a
https://api.day.app/<key>/... address), or just the key. The push rings for 30 seconds and breaks through Focus.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request


def target(value):
    """(server, key) from what the user pasted."""
    value = value.strip()
    m = re.search(r"(https?://[^/\s'\"]+)/([^/?#\s'\"]+)", value)
    return (m.group(1), m.group(2)) if m else ("https://api.day.app", value)


def payload(key, title, body, link=""):
    data = {"device_key": key, "title": title, "body": body, "group": "stock",
            "level": "timeSensitive", "sound": "alarm", "call": "1"}
    if link:
        data["url"] = link
    return data


def main(title, body, link=""):
    raw = os.environ.get("BARK_URL", "").strip()
    if not raw:
        print("没设置 BARK_URL，跳过 Bark")
        return
    server, key = target(raw)
    print(f"::add-mask::{key}")
    req = urllib.request.Request(f"{server}/push", data=json.dumps(payload(key, title, body, link)).encode(),
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        print("已推送到 Bark")
    except urllib.error.HTTPError as e:
        reason = e.read().decode("utf-8", "replace")[:200].replace(key, "***")
        print(f"::warning::Bark 推送失败：HTTP {e.code} {reason}")
    except (urllib.error.URLError, TimeoutError) as e:
        # Never echo the URL: it contains the device key.
        print(f"::warning::Bark 推送失败：{type(e).__name__}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
