#!/usr/bin/env python3
"""Watch Apple Store in-store pickup (and US delivery dates) for one product.

    python3 check.py --product SLUG --location ZIP [--watch 60] [--delivery]
    python3 check.py --region cn --product SLUG --location "省 市" --city 市 [--watch 60]

SLUG is the last part of the store's /shop/buy-iphone/SLUG page; every model
on that page is checked. Uses the same undocumented endpoints as the Apple
Store availability dialogs. Standard library only.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class Region:
    shop: str
    part_suffix: str  # US parts end in LL/A, mainland China parts in CH/A
    language: str
    # Mainland China's pickup queries need the session a buy page sets up;
    # the US store is fine with the bag page.
    warm_on_buy_page: bool

    @property
    def part_re(self):
        return re.compile(r"M[A-Z0-9]{4}" + re.escape(self.part_suffix))

    @property
    def pickup_url(self):
        # Current pickup endpoint; the legacy one is only a fallback since Apple
        # now often answers it with HTTP 541 or an empty store list.
        return f"{self.shop}/retail/pickup-message"

    @property
    def fulfillment_url(self):
        return f"{self.shop}/fulfillment-messages"

    @property
    def delivery_url(self):
        return f"{self.shop}/delivery-message"

    def buy_url(self, product):
        return f"{self.shop}/buy-iphone/{product}"


REGIONS = {
    "us": Region("https://www.apple.com/shop", "LL/A", "en-US,en;q=0.9", False),
    "cn": Region("https://www.apple.com.cn/shop", "CH/A", "zh-CN,zh;q=0.9", True),
}
US = REGIONS["us"]
PICKUP_URL, FULFILLMENT_URL, DELIVERY_URL = US.pickup_url, US.fulfillment_url, US.delivery_url
PART_RE = re.compile(r"M[A-Z0-9]{4}(?:LL|CH)/A")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
RETRY_STATUSES = {500, 502, 503, 504}
# Apple's refusals. Retrying them right away only extends the block.
BLOCKED_STATUSES = {403, 429, 541}
MIN_WATCH_SECONDS = 30
BLOCKED_COOLDOWN_SECONDS = 300
DELIVERY_EVERY_SECONDS = 300  # ship dates move slowly; no need to ask every minute


class Blocked(Exception):
    """Apple refused the request; this says nothing about stock."""


@dataclass
class Row:
    store: str
    store_number: str
    city: str
    state: str
    distance: float | None
    part: str
    title: str
    status: str
    quote: str
    selectable: bool = True  # storeSelectionEnabled; False while Apple has pickup switched off

    @property
    def available(self):
        return self.status == "available"


def pickup_closed(rows):
    """True when Apple has pickup switched off for every store and model.

    Before launch Apple answers every store with pickupDisplay "default",
    storeSelectionEnabled false and "Check back for availability on <date>",
    regardless of stock. Anything else counts as open, so a misread errs
    toward checking more often, not less.
    """
    return bool(rows) and all(r.status == "default" and not r.selectable for r in rows)


class AppleClient:
    def __init__(self, region=US, product=None, timeout=20):
        self.region = region
        self.referer = region.buy_url(product) if product else region.shop
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, url, accept="*/*", retries=3, extra_headers=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": self.region.language,
            "Referer": self.referer,
            **(extra_headers or {}),
        }
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers=headers)
                with self.opener.open(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code in BLOCKED_STATUSES:
                    raise Blocked(f"HTTP {e.code}") from e
                if e.code not in RETRY_STATUSES or attempt == retries - 1:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == retries - 1:
                    raise
            time.sleep(3 * 2**attempt + random.random() * 2)
        raise RuntimeError("unreachable")

    def warm_up(self):
        """Apple's edge rejects pickup queries that carry no store cookies."""
        if len(self.jar):
            return
        url = self.referer if self.region.warm_on_buy_page else f"{self.region.shop}/bag"
        try:
            self.get(url, accept="text/html,application/xhtml+xml,*/*;q=0.8")
        except (Blocked, urllib.error.URLError, TimeoutError):
            pass  # The query itself decides; a failed warm-up is not an answer.

    def get_json(self, url):
        text = self.get(url, accept="application/json, text/javascript, */*; q=0.01",
                        extra_headers={"X-Requested-With": "XMLHttpRequest"})
        try:
            return json.loads(text)
        except ValueError:
            # Blocks sometimes come back as a 200 HTML page.
            raise Blocked("返回的不是 JSON") from None


# ---------- part numbers ----------

def _walk_parts(obj, found):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if PART_RE.fullmatch(key):
                found.add(key)
            _walk_parts(value, found)
    elif isinstance(obj, list):
        for value in obj:
            _walk_parts(value, found)
    elif isinstance(obj, str) and PART_RE.fullmatch(obj):
        found.add(obj)


def _json_blobs(html):
    decoder = json.JSONDecoder()
    for body in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S | re.I):
        body = body.strip()
        starts = [0] if body[:1] in "{[" else []
        starts += [m.end() - 1 for m in re.finditer(r"=\s*\{", body)]
        for start in starts:
            try:
                yield decoder.raw_decode(body, start)[0]
            except ValueError:
                continue


def parts_from_page(html):
    found = set()
    for blob in _json_blobs(html):
        _walk_parts(blob, found)
    if not found:
        found.update(PART_RE.findall(html.replace("\\u002F", "/").replace("\\/", "/")))
    return found


def discover_parts(client, product):
    try:
        found = parts_from_page(client.get(client.region.buy_url(product), accept="text/html"))
    except (Blocked, urllib.error.URLError, TimeoutError) as e:
        print(f"⚠️  打开购买页失败：{e}", file=sys.stderr)
        found = set()
    found = {part for part in found if part.endswith(client.region.part_suffix)}
    print(f"共找到 {len(found)} 个型号")
    return {part: {} for part in sorted(found)}


def matches_product(title, product):
    """'iPhone X 256GB Blue' matches slug 'iphone-x'; page extras such as accessories don't."""
    title = title.lower()
    return all(word in title for word in product.lower().split("-"))


def learn_titles(parts, rows, product):
    """Label parts with Apple's product titles and drop parts that aren't this product."""
    for r in rows:
        if r.title and r.part in parts:
            parts[r.part]["title"] = r.title
    for part in [p for p, meta in parts.items() if meta.get("title") and not matches_product(meta["title"], product)]:
        del parts[part]


def label(part, parts):
    return (parts.get(part) or {}).get("title") or part


# ---------- pickup ----------

def _distance(store):
    value = store.get("storedistance") or store.get("storeDistance")
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"[\d.]+", str(store.get("storeDistanceWithUnit") or value or ""))
    return float(m.group()) if m else None


def _plain(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def parse_pickup(data):
    """Turn a pickup response into Rows; also return Apple's notice, if any.

    retail/pickup-message puts stores at body.stores; fulfillment-messages
    nests them under body.content.pickupMessage.
    """
    body = (data or {}).get("body") or {}
    pickup = (body.get("content") or {}).get("pickupMessage") or body
    rows = []
    for store in pickup.get("stores") or []:
        for part, info in (store.get("partsAvailability") or {}).items():
            messages = info.get("messageTypes") or {}
            message = messages.get("regular") or messages.get("compact") or {}
            rows.append(Row(
                store=store.get("storeName") or "",
                store_number=store.get("storeNumber") or "",
                city=store.get("city") or "",
                state=store.get("state") or "",
                distance=_distance(store),
                part=part,
                title=message.get("storePickupProductTitle") or "",
                status=(info.get("pickupDisplay") or "").lower(),
                quote=_plain(info.get("pickupSearchQuote") or message.get("storePickupQuote")),
                selectable=message.get("storeSelectionEnabled") is not False,
            ))
    notice = _plain(pickup.get("errorMessage") or pickup.get("pickupLocatorErrorMessage"))
    return rows, notice


def pickup_url(base, parts, zip_code):
    params = [("pl", "true"), ("mts.0", "regular")]
    if base.endswith("/fulfillment-messages"):
        params = [("fae", "true")] + params + [("mts.1", "compact")]
    params += [(f"parts.{i}", part) for i, part in enumerate(parts)]
    params += [("searchNearby", "true"), ("location", zip_code)]
    return f"{base}?{urllib.parse.urlencode(params)}"


def query_pickup(client, parts, zip_code):
    """Ask the current endpoint, falling back to the legacy one if it fails or finds no stores."""
    result, error = None, None
    for base in (client.region.pickup_url, client.region.fulfillment_url):
        try:
            result = parse_pickup(client.get_json(pickup_url(base, parts, zip_code)))
        except (Blocked, urllib.error.HTTPError) as e:
            error = error or e
            continue
        if result[0]:
            return result
    if result is not None:
        return result
    raise error


def check_once(client, parts, zip_code, batch_size=8, city=None):
    """Raises Blocked if Apple refuses the queries; that is not the same as no stock.

    city keeps only stores in that city: a nearby search can reach the next one.
    """
    client.warm_up()
    part_list = sorted(parts)
    rows, notices, failed = [], set(), []
    for i in range(0, len(part_list), batch_size):
        batch = part_list[i:i + batch_size]
        try:
            batch_rows, notice = query_pickup(client, batch, zip_code)
        except urllib.error.HTTPError:
            if len(batch) == 1:
                raise
            # A multi-part query was rejected outright; try one part at a time.
            batch_rows, notice = [], ""
            for part in batch:
                try:
                    r, n = query_pickup(client, [part], zip_code)
                except urllib.error.HTTPError as e:
                    print(f"⚠️  {part} 查询失败，跳过：{e}", file=sys.stderr)
                    failed.append(e)
                    continue
                batch_rows += r
                notice = notice or n
        rows += batch_rows
        if notice:
            notices.add(notice)
        time.sleep(0.5 + random.random())

    if failed and not rows:
        raise failed[-1]
    if city:
        nearby = {r.store_number for r in rows}
        rows = [r for r in rows if r.city.strip() == city]
        if nearby and not rows:
            print(f"⚠️  附近 {len(nearby)} 家店都不在{city}", file=sys.stderr)
    rows.sort(key=lambda r: (r.distance if r.distance is not None else 1e9, r.store, r.part))
    return rows, notices


def print_report(rows, notices, parts, show_all=False):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [r for r in rows if r.part in parts]
    hits = [r for r in rows if r.available]
    stores = {r.store_number for r in rows}
    print(f"\n[{stamp}] {len(stores)} 家店 × {len(parts)} 个型号 → 可自提 {len(hits)} 个")
    for notice in notices:
        print(f"  Apple 提示：{notice}")
    by_store = {}
    for r in rows:
        by_store.setdefault(r.store_number, []).append(r)
    for store_rows in by_store.values():
        avail = [r for r in store_rows if r.available]
        if not avail and not show_all:
            continue
        r0 = store_rows[0]
        dist = f" · {r0.distance:g} mi" if r0.distance is not None else ""
        print(f"{'✅' if avail else '❌'} {r0.store} ({r0.city}, {r0.state}{dist})  有货 {len(avail)}/{len(store_rows)}")
        for r in store_rows if show_all else avail:
            print(f"   {'  ✔' if r.available else '   '} {label(r.part, parts):<40} {r.part:<10} {r.quote}")
    if not hits and not show_all:
        print("  暂时没有任何门店可自提。")
    return hits


def write_hits(path, hits, parts):
    """Only store and model: this text ends up in a public issue."""
    with open(path, "w", encoding="utf-8") as f:
        for r in hits:
            f.write(f"- {r.store}：{label(r.part, parts)}\n")


# ---------- delivery ----------

def delivery_url(parts, zip_code, region=US):
    # postalCode, not location: the latter is ignored in favour of IP geolocation.
    params = [(f"parts.{i}", part) for i, part in enumerate(parts)]
    params += [("mts.0", "regular"), ("postalCode", zip_code)]
    return f"{region.delivery_url}?{urllib.parse.urlencode(params)}"


def parse_delivery(data):
    """{part: {"date": "YYYYMMDD" or None, "text": "Mon, Jan 25 — Free"}} from delivery-message."""
    messages = (((data or {}).get("body") or {}).get("content") or {}).get("deliveryMessage") or {}
    result = {}
    for part, info in messages.items():
        if not PART_RE.fullmatch(part) or not isinstance(info, dict):
            continue
        regular = info.get("regular") or {}
        options = [o for o in regular.get("deliveryOptionMessages") or [] if isinstance(o, dict)]
        dated = sorted((o for o in options if o.get("encodedUpperDateString")),
                       key=lambda o: o["encodedUpperDateString"])
        best = (dated or options or [{}])[0]
        buyable = (regular.get("buyability") or {}).get("isBuyable") is not False
        result[part] = {
            "date": best.get("encodedUpperDateString") if buyable else None,
            "text": _plain(best.get("displayName")) or ("" if buyable else "暂时买不了"),
        }
    return result


def check_delivery(client, parts, zip_code, batch_size=8):
    part_list = sorted(parts)
    result = {}
    for i in range(0, len(part_list), batch_size):
        url = delivery_url(part_list[i:i + batch_size], zip_code, client.region)
        result.update(parse_delivery(client.get_json(url)))
    return {part: d for part, d in result.items() if part in parts}


def print_delivery(delivery, parts):
    texts = {d["text"] or "没有日期" for d in delivery.values()}
    if len(texts) == 1 and len(delivery) > 1:
        print(f"寄送最早：{len(delivery)} 个型号都是 {texts.pop()}")
        return
    print("寄送最早：")
    for part in sorted(delivery, key=lambda p: label(p, parts)):
        print(f"   {label(part, parts):<40} {delivery[part]['text'] or '没有日期'}")


def write_delivery(path, delivery, parts):
    data = {part: {**d, "label": label(part, parts)} for part, d in delivery.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)


def watch_delivery(client, parts, args, previous):
    """One delivery round; returns what to compare the next round against."""
    try:
        delivery = check_delivery(client, parts, args.location, args.batch_size)
    except (Blocked, urllib.error.URLError, TimeoutError) as e:
        print(f"[{datetime.now():%H:%M:%S}] 寄送日期没查到：{e}（下次再试）", file=sys.stderr)
        return previous
    print_delivery(delivery, parts)
    sys.stdout.flush()
    if args.delivery_file:
        write_delivery(args.delivery_file, delivery, parts)
    if args.on_delivery_change and delivery and delivery != previous:
        subprocess.run(args.on_delivery_change, shell=True, check=False)
    return delivery


# ---------- CLI ----------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="查 Apple Store 附近门店的自提库存和寄送日期（一个产品的全部型号）")
    p.add_argument("--region", choices=sorted(REGIONS), default="us", help="us（默认）或 cn（中国大陆）")
    p.add_argument("--product", required=True, help="购买页地址 /shop/buy-iphone/ 后面那段")
    p.add_argument("--location", required=True, help="美国填邮编；中国大陆填“省 市”，例如“浙江 杭州”")
    p.add_argument("--city", help="只看这个城市的门店（附近搜索可能会带出别的城市）")
    p.add_argument("--parts", help="手动指定型号，逗号分隔（跳过自动抓取）")
    p.add_argument("--all", action="store_true", help="显示每家店每个型号的状态（包括没货的）")
    p.add_argument("--list-parts", action="store_true", help="只列出找到的型号，不查库存")
    p.add_argument("--watch", type=int, metavar="SECONDS",
                   help=f"持续监控，每隔 N 秒查一次（最少 {MIN_WATCH_SECONDS} 秒）")
    p.add_argument("--duration", type=int, metavar="SECONDS", help="--watch 模式下跑这么多秒后自动退出")
    p.add_argument("--stop-on-block", action="store_true",
                   help="--watch 模式下被 Apple 拦截就退出（返回 2），而不是原地等")
    p.add_argument("--closed-interval", type=int, metavar="SECONDS",
                   help="--watch 模式下如果 Apple 还没开放自提（所有门店都显示“稍后再来”），改成每隔这么多秒查一次")
    p.add_argument("--hits-file", metavar="PATH", help="每次查完把有货的门店和型号写进这个文件")
    p.add_argument("--on-change", metavar="CMD", help="--watch 模式下，第一次查完以及有货情况变化时执行这条命令")
    p.add_argument("--delivery", action="store_true", help="同时查寄送最早日期，只支持美国（--watch 模式下每 5 分钟查一次）")
    p.add_argument("--delivery-file", metavar="PATH", help="把寄送日期写进这个 JSON 文件")
    p.add_argument("--on-delivery-change", metavar="CMD", help="--watch 模式下，第一次查到以及寄送日期变化时执行这条命令")
    p.add_argument("--batch-size", type=int, default=8, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.delivery and args.region != "us":
        print("--delivery 只支持美国", file=sys.stderr)
        return 2
    client = AppleClient(REGIONS[args.region], args.product)
    if args.parts:
        parts = {part: {} for part in filter(None, (s.strip().upper() for s in args.parts.split(",")))}
    else:
        parts = discover_parts(client, args.product)
    if not parts:
        print("没找到任何型号。可以用 --parts 手动指定。", file=sys.stderr)
        return 2
    if args.list_parts:
        for part in parts:
            print(part)
        return 0

    if not args.watch:
        try:
            rows, notices = check_once(client, parts, args.location, args.batch_size, args.city)
        except Blocked as e:
            print(f"被 Apple 拦截了（{e}）：这不代表没货。过 10 分钟以上再试，或换个网络。", file=sys.stderr)
            return 2
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"查询失败：{e}", file=sys.stderr)
            return 2
        learn_titles(parts, rows, args.product)
        hits = print_report(rows, notices, parts, args.all)
        if args.hits_file:
            write_hits(args.hits_file, hits, parts)
        if args.delivery:
            watch_delivery(client, parts, args, None)
        return 0 if hits else 1

    interval = max(args.watch, MIN_WATCH_SECONDS)
    until = f"，{args.duration} 秒后退出" if args.duration is not None else "，Ctrl+C 退出"
    print(f"开始监控，每 {interval} 秒左右查一次{until}。", flush=True)
    started = time.monotonic()
    seen = None  # None until the first successful round, so --on-change always fires once
    cooldown = 0
    last_delivery, delivery_checked_at = None, None
    try:
        while True:
            pause = interval
            try:
                rows, notices = check_once(client, parts, args.location, args.batch_size, args.city)
                learn_titles(parts, rows, args.product)
                hits = print_report(rows, notices, parts, args.all)
                sys.stdout.flush()
                current = {(r.store_number, r.part) for r in hits}
                if args.hits_file:
                    write_hits(args.hits_file, hits, parts)
                if args.on_change and current != seen:
                    subprocess.run(args.on_change, shell=True, check=False)
                seen = current
                cooldown = 0
                if args.delivery and (delivery_checked_at is None
                                      or time.monotonic() - delivery_checked_at >= DELIVERY_EVERY_SECONDS):
                    delivery_checked_at = time.monotonic()
                    last_delivery = watch_delivery(client, parts, args, last_delivery)
                if args.closed_interval and pickup_closed([r for r in rows if r.part in parts]):
                    pause = max(interval, args.closed_interval)
                    print(f"Apple 还没开放自提（{rows[0].quote}），{pause} 秒后再查。", flush=True)
            except Blocked as e:
                if args.stop_on_block:
                    print(f"[{datetime.now():%H:%M:%S}] 被 Apple 拦截（{e}），不代表没货；退出", file=sys.stderr)
                    return 2
                # Back off 5 → 10 → 20 → 30 min while Apple keeps refusing.
                cooldown = min(cooldown * 2 or BLOCKED_COOLDOWN_SECONDS, 1800)
                pause = max(interval, cooldown)
                print(f"[{datetime.now():%H:%M:%S}] 被 Apple 拦截（{e}），不代表没货；"
                      f"{pause // 60} 分钟后再试", file=sys.stderr)
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"[{datetime.now():%H:%M:%S}] 查询失败：{e}（下次继续）", file=sys.stderr)
            if args.duration is not None and time.monotonic() - started + pause > args.duration:
                return 0
            time.sleep(pause + random.uniform(0, 3))
    except KeyboardInterrupt:
        print("\n已停止。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
