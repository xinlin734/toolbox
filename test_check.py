import contextlib
import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

import check

_spec = importlib.util.spec_from_file_location(
    "notify_delivery", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts", "notify_delivery.py"))
notify_delivery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notify_delivery)
_spec = importlib.util.spec_from_file_location(
    "push_bark", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts", "push_bark.py"))
push_bark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(push_bark)

PRODUCT = "phone-x"
A, B, C = "MX001LL/A", "MX002LL/A", "MX003LL/A"
ACCESSORY = "MX999LL/A"

BUY_PAGE = f"""
<html><script>window.BOOTSTRAP = {{"products": [
  {{"partNumber": "{A}", "dimensions": {{"capacity": "256gb", "color": "blue"}}}},
  {{"partNumber": "{B}", "dimensions": {{"capacity": "512gb", "color": "blue"}}}}
], "byPart": {{"{C}": {{"price": 1}}}}}};</script>
<script type="application/json">{{"extras": [{{"sku": "{ACCESSORY}"}}]}}</script>
</html>
"""


def store(name, number, distance, parts, closed=False):
    """parts: {part: (pickupDisplay, title)}; closed=True gives the pre-launch shape."""
    def availability(status, title):
        regular = {"storePickupProductTitle": title}
        if closed:
            regular.update(storeSelectionEnabled=False, storeSearchEnabled=False)
            quote = "Check back for availability on 1.1"
        else:
            quote = "Available<br/>Today" if status == "available" else "Unavailable for Pickup"
        return {"pickupDisplay": status, "pickupSearchQuote": quote, "messageTypes": {"regular": regular}}

    return {
        "storeName": name, "storeNumber": number, "city": name, "state": "ZZ",
        "storeDistanceWithUnit": f"{distance} mi",
        "partsAvailability": {part: availability(*v) for part, v in parts.items()},
    }


STORES = [
    store("Store B", "R2", 4.1, {A: ("unavailable", "Phone X 256GB Blue")}),
    store("Store A", "R1", 3.2, {A: ("available", "Phone X 256GB Blue"), B: ("unavailable", "Phone X 512GB Blue")}),
    store("Store C", "R3", 12.0, {ACCESSORY: ("available", "Charger")}),
]
RESPONSE = {"body": {"stores": STORES}}  # retail/pickup-message
LEGACY_RESPONSE = {"body": {"content": {"pickupMessage": {"stores": STORES}}}}  # fulfillment-messages
CLOSED_RESPONSE = {"body": {"stores": [
    store("Store A", "R1", 3.2, {A: ("default", "Phone X 256GB Blue"), B: ("default", "Phone X 512GB Blue")}, closed=True),
    store("Store B", "R2", 4.1, {A: ("default", "Phone X 256GB Blue")}, closed=True),
]}}


def delivery_response(dates):
    """dates: {part: ("Mon, Jan 4 — Free", "20270104") or None for not buyable}."""
    message = {"dudeLocated": True}
    for part, value in dates.items():
        if value is None:
            message[part] = {"regular": {"buyability": {"isBuyable": False}, "deliveryOptionMessages": []}}
        else:
            text, date = value
            message[part] = {"regular": {"buyability": {"isBuyable": True},
                                         "deliveryOptionMessages": [{"displayName": text,
                                                                     "encodedUpperDateString": date}]}}
    return {"body": {"content": {"deliveryMessage": message}}}


class FakeClient:
    """Answers per endpoint from a fixed response, an Exception, or a list consumed in order."""

    def __init__(self, by_endpoint):
        self.responses = {k: iter(v) if isinstance(v, list) else v for k, v in by_endpoint.items()}
        self.urls = []

    region = check.US

    def warm_up(self):
        pass

    def get_json(self, url):
        self.urls.append(url)
        result = self.responses.get(url.split("?")[0], {})
        if not isinstance(result, Exception) and hasattr(result, "__next__"):
            result = next(result)
        if isinstance(result, Exception):
            raise result
        return result


def quiet():
    stack = contextlib.ExitStack()
    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
    stack.enter_context(mock.patch.object(check.time, "sleep"))
    return stack


class DiscoveryTest(unittest.TestCase):
    def test_parts_from_json_and_keys(self):
        self.assertEqual(check.parts_from_page(BUY_PAGE), {A, B, C, ACCESSORY})

    def test_regex_fallback_with_escaped_slashes(self):
        page = '<div data-x="MX004LL\\u002FA MX005LL\\/A MX123ZM/A"></div>'
        self.assertEqual(check.parts_from_page(page), {"MX004LL/A", "MX005LL/A"})

    def test_failed_page_finds_nothing(self):
        class Client:
            region = check.US

            def get(self, url, accept=None):
                raise check.Blocked("HTTP 541")

        with quiet():
            self.assertEqual(check.discover_parts(Client(), PRODUCT), {})

    def test_discovery_keeps_only_the_regions_parts(self):
        class Client:
            def __init__(self, region):
                self.region = region

            def get(self, url, accept=None):
                return '<script>x = {"a": ["MX001LL/A", "MX001CH/A"]}</script>'

        with quiet():
            self.assertEqual(list(check.discover_parts(Client(check.US), PRODUCT)), ["MX001LL/A"])
            self.assertEqual(list(check.discover_parts(Client(check.REGIONS["cn"]), PRODUCT)), ["MX001CH/A"])

    def test_titles_label_parts_and_prune_other_products(self):
        parts = {A: {}, B: {}, ACCESSORY: {}}
        rows, _ = check.parse_pickup(RESPONSE)
        check.learn_titles(parts, rows, PRODUCT)
        self.assertEqual(sorted(parts), [A, B])
        self.assertEqual(check.label(A, parts), "Phone X 256GB Blue")
        self.assertEqual(check.label(C, parts), C)

    def test_matches_product(self):
        self.assertTrue(check.matches_product("Phone X 256GB Blue", "phone-x"))
        self.assertFalse(check.matches_product("Phone Y 256GB Blue", "phone-x"))


class PickupTest(unittest.TestCase):
    def test_parse_pickup_both_shapes(self):
        rows, notice = check.parse_pickup(RESPONSE)
        self.assertEqual(notice, "")
        self.assertEqual(rows, check.parse_pickup(LEGACY_RESPONSE)[0])
        a = [r for r in rows if r.store == "Store A" and r.part == A][0]
        self.assertTrue(a.available)
        self.assertEqual((a.distance, a.quote), (3.2, "Available Today"))

    def test_parse_pickup_handles_empty_and_error(self):
        self.assertEqual(check.parse_pickup({}), ([], ""))
        data = {"body": {"content": {"pickupMessage": {"errorMessage": "Try <b>later</b>"}}}}
        self.assertEqual(check.parse_pickup(data), ([], "Try later"))

    def test_pickup_url(self):
        self.assertEqual(check.pickup_url(check.PICKUP_URL, [A], "10001"),
                         check.PICKUP_URL + "?pl=true&mts.0=regular&parts.0=MX001LL%2FA&searchNearby=true&location=10001")
        self.assertIn("fae=true", check.pickup_url(check.FULFILLMENT_URL, [A], "10001"))

    def test_check_once_batches_and_sorts(self):
        client = FakeClient({check.PICKUP_URL: RESPONSE})
        with quiet():
            rows, _ = check.check_once(client, {f"MX1{i:02}LL/A": {} for i in range(10)}, "10001", batch_size=4)
        self.assertEqual(len(client.urls), 3)
        self.assertIn("parts.3=MX103LL%2FA", client.urls[0])
        self.assertEqual(rows[0].store, "Store A")  # nearest first

    def test_falls_back_to_legacy_when_no_stores_or_blocked(self):
        for first in ({"body": {"stores": []}}, check.Blocked("HTTP 541")):
            client = FakeClient({check.PICKUP_URL: first, check.FULFILLMENT_URL: LEGACY_RESPONSE})
            with quiet():
                rows, _ = check.check_once(client, {A: {}}, "10001")
            self.assertTrue(any(r.available for r in rows))

    def test_blocked_everywhere_is_an_error_not_out_of_stock(self):
        blocked = check.Blocked("HTTP 541")
        client = FakeClient({check.PICKUP_URL: blocked, check.FULFILLMENT_URL: blocked})
        with quiet(), self.assertRaises(check.Blocked):
            check.check_once(client, {A: {}, B: {}}, "10001")
        self.assertEqual(len(client.urls), 2)  # no per-part retries once blocked

    def test_all_parts_failing_is_an_error(self):
        boom = check.urllib.error.HTTPError("u", 400, "bad", None, None)
        client = FakeClient({check.PICKUP_URL: boom, check.FULFILLMENT_URL: boom})
        with quiet(), self.assertRaises(check.urllib.error.HTTPError):
            check.check_once(client, {A: {}, B: {}}, "10001")

    def test_city_filter(self):
        client = FakeClient({check.PICKUP_URL: RESPONSE})
        with quiet():
            rows, _ = check.check_once(client, {A: {}, B: {}}, "10001", city="Store B")
        self.assertEqual({r.store for r in rows}, {"Store B"})

    def test_cn_urls(self):
        cn = check.REGIONS["cn"]
        self.assertEqual(cn.pickup_url, "https://www.apple.com.cn/shop/retail/pickup-message")
        self.assertEqual(check.pickup_url(cn.pickup_url, ["MX001CH/A"], "省 市"),
                         cn.pickup_url + "?pl=true&mts.0=regular&parts.0=MX001CH%2FA&searchNearby=true"
                                         "&location=%E7%9C%81+%E5%B8%82")
        self.assertEqual(cn.buy_url(PRODUCT), "https://www.apple.com.cn/shop/buy-iphone/phone-x")

    def test_cn_warms_up_on_buy_page(self):
        for region, expected in ((check.US, "https://www.apple.com/shop/bag"),
                                 (check.REGIONS["cn"], "https://www.apple.com.cn/shop/buy-iphone/phone-x")):
            client = check.AppleClient(region, PRODUCT)
            with mock.patch.object(client, "get") as get:
                client.warm_up()
            self.assertEqual(get.call_args.args[0], expected)

    def test_closed_detection(self):
        self.assertTrue(check.pickup_closed(check.parse_pickup(CLOSED_RESPONSE)[0]))
        self.assertFalse(check.pickup_closed(check.parse_pickup(RESPONSE)[0]))
        self.assertFalse(check.pickup_closed([]))
        mixed = {"body": {"stores": CLOSED_RESPONSE["body"]["stores"] + STORES[:1]}}
        self.assertFalse(check.pickup_closed(check.parse_pickup(mixed)[0]))

    def test_hits_file_has_only_store_and_model(self):
        parts = {A: {"title": "Phone X 256GB Blue"}}
        hits = [r for r in check.parse_pickup(RESPONSE)[0] if r.available and r.part == A]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "hits.txt")
            check.write_hits(path, hits, parts)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "- Store A：Phone X 256GB Blue\n")


class DeliveryTest(unittest.TestCase):
    def test_delivery_url_uses_postal_code(self):
        self.assertEqual(check.delivery_url([A, B], "10001"),
                         check.DELIVERY_URL + "?parts.0=MX001LL%2FA&parts.1=MX002LL%2FA&mts.0=regular&postalCode=10001")

    def test_parse_delivery(self):
        data = delivery_response({A: ("Mon, Jan 4 — Free", "20270104"), B: None})
        self.assertEqual(check.parse_delivery(data), {
            A: {"date": "20270104", "text": "Mon, Jan 4 — Free"},
            B: {"date": None, "text": "暂时买不了"},
        })
        self.assertEqual(check.parse_delivery({}), {})

    def test_parse_delivery_takes_earliest_option(self):
        data = delivery_response({A: ("Fri, Jan 8 — Free", "20270108")})
        data["body"]["content"]["deliveryMessage"][A]["regular"]["deliveryOptionMessages"].append(
            {"displayName": "Tue, Jan 5 — $9", "encodedUpperDateString": "20270105"})
        self.assertEqual(check.parse_delivery(data)[A]["date"], "20270105")


class ClientTest(unittest.TestCase):
    def test_541_raises_blocked_without_retry(self):
        client = check.AppleClient()
        err = check.urllib.error.HTTPError("u", 541, "Unknown", None, None)
        with mock.patch.object(client.opener, "open", side_effect=err) as opened, \
                mock.patch.object(check.time, "sleep"), self.assertRaises(check.Blocked):
            client.get("https://www.apple.com/x")
        self.assertEqual(opened.call_count, 1)

    def test_html_instead_of_json_is_blocked(self):
        client = check.AppleClient()
        with mock.patch.object(client, "get", return_value="<html>nope</html>"), self.assertRaises(check.Blocked):
            client.get_json("https://www.apple.com/x")


class MainTest(unittest.TestCase):
    """main() with a fake client and a fake clock: every sleep advances time by its length."""

    def run_main(self, by_endpoint, argv):
        clock = [0.0]
        client = FakeClient(by_endpoint)
        out = io.StringIO()
        with mock.patch.object(check, "AppleClient", lambda *a, **k: client), \
                mock.patch.object(check.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s)), \
                mock.patch.object(check.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(check.subprocess, "run") as run, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = check.main(["--product", PRODUCT, "--location", "10001", "--parts", f"{A},{B}"] + argv)
        return code, run, out.getvalue(), clock[0]

    def test_single_check(self):
        code, _, out, _ = self.run_main({check.PICKUP_URL: RESPONSE}, [])
        self.assertEqual(code, 0)
        self.assertIn("可自提 1 个", out)

    def test_on_change_fires_first_round_and_on_changes_only(self):
        empty = {"body": {"stores": [store("Store A", "R1", 3.2, {A: ("unavailable", "Phone X 256GB Blue")})]}}
        code, run, _, _ = self.run_main(
            {check.PICKUP_URL: [empty, empty, RESPONSE, RESPONSE, empty]},
            ["--watch", "60", "--duration", "290", "--on-change", "notify"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 3)  # rounds 1, 3 and 5

    def test_stop_on_block(self):
        blocked = check.Blocked("HTTP 541")
        code, run, _, _ = self.run_main({check.PICKUP_URL: blocked, check.FULFILLMENT_URL: blocked},
                                     ["--watch", "60", "--duration", "600", "--stop-on-block", "--on-change", "x"])
        self.assertEqual(code, 2)
        run.assert_not_called()

    def test_closed_interval_slows_down_while_closed(self):
        pickups = [CLOSED_RESPONSE] * 4
        code, run, out, elapsed = self.run_main(
            {check.PICKUP_URL: pickups,
             check.DELIVERY_URL: delivery_response({A: ("Mon, Jan 4 — Free", "20270104"),
                                                    B: ("Mon, Jan 4 — Free", "20270104")})},
            ["--watch", "60", "--duration", "1200", "--closed-interval", "300", "--delivery", "--on-delivery-change", "d"])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("还没开放自提"), 4)  # rounds at 0, 5, 10 and 15 minutes
        self.assertIn("2 个型号都是 Mon, Jan 4 — Free", out)
        run.assert_called_once()  # delivery unchanged after the first answer

    def test_open_pickup_checks_every_minute(self):
        code, _, out, _ = self.run_main({check.PICKUP_URL: RESPONSE},
                                        ["--watch", "60", "--duration", "300", "--closed-interval", "300"])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("可自提 1 个"), 5)

    def test_delivery_is_us_only(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = check.main(["--region", "cn", "--product", PRODUCT, "--location", "省 市", "--delivery"])
        self.assertEqual(code, 2)

    def test_delivery_every_five_minutes_and_hook_on_change(self):
        jan = delivery_response({A: ("Mon, Jan 4 — Free", "20270104")})
        nov = delivery_response({A: ("Fri, Nov 6 — Free", "20261106")})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "delivery.json")
            code, run, _, _ = self.run_main(
                {check.PICKUP_URL: RESPONSE, check.DELIVERY_URL: [jan, jan, nov]},
                ["--watch", "60", "--duration", "700", "--delivery", "--delivery-file", path,
                 "--on-delivery-change", "d"])
            with open(path, encoding="utf-8") as f:
                written = json.load(f)
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 2)  # first answer, then the change to Nov 6
        self.assertEqual(written[A], {"date": "20261106", "text": "Fri, Nov 6 — Free", "label": "Phone X 256GB Blue"})


class NotifyDeliveryTest(unittest.TestCase):
    CURRENT = {
        A: {"date": "20270125", "text": "Mon, Jan 25 — Free", "label": "Phone X 256GB Blue"},
        B: {"date": "20270125", "text": "Mon, Jan 25 — Free", "label": "Phone X 512GB Blue"},
    }

    def with_date(self, part, date, text):
        current = {p: dict(d) for p, d in self.CURRENT.items()}
        current[part].update(date=date, text=text)
        return current

    def test_first_run_records_baseline_quietly(self):
        best, message = notify_delivery.plan(self.CURRENT, None)
        self.assertIsNone(message)
        self.assertEqual(best, {A: "20270125", B: "20270125"})

    def test_earlier_date_notifies(self):
        best, _ = notify_delivery.plan(self.CURRENT, None)
        best, message = notify_delivery.plan(self.with_date(B, "20261106", "Fri, Nov 6 — Free"), best)
        self.assertEqual(message, "- Phone X 512GB Blue：之前最早 1/25 → **Fri, Nov 6 — Free**")
        self.assertEqual(best[B], "20261106")

    def test_slip_and_recovery_stay_quiet(self):
        best, _ = notify_delivery.plan(self.CURRENT, None)
        best, message = notify_delivery.plan(self.with_date(A, "20270205", "Fri, Feb 5 — Free"), best)
        self.assertIsNone(message)
        _, message = notify_delivery.plan(self.CURRENT, best)
        self.assertIsNone(message)

    def test_unbuyable_is_not_an_improvement(self):
        best, _ = notify_delivery.plan(self.CURRENT, None)
        _, message = notify_delivery.plan(self.with_date(A, None, "暂时买不了"), best)
        self.assertIsNone(message)

    def test_main_writes_state_and_opens_issue_only_on_improvement(self):
        with tempfile.TemporaryDirectory() as d:
            current, state = os.path.join(d, "delivery.json"), os.path.join(d, "state", "best.json")
            with open(current, "w", encoding="utf-8") as f:
                json.dump(self.CURRENT, f)
            with mock.patch.object(notify_delivery.subprocess, "run") as run, \
                    mock.patch.dict(os.environ, {"OWNER": "someone"}):
                notify_delivery.main(current, state)  # baseline
                run.assert_not_called()
                with open(current, "w", encoding="utf-8") as f:
                    json.dump(self.with_date(A, "20261106", "Fri, Nov 6 — Free"), f)
                run.return_value.stdout = "https://github.com/o/r/issues/1\n"
                notify_delivery.main(current, state)
            with open(state, encoding="utf-8") as f:
                self.assertEqual(json.load(f)[A], "20261106")
        self.assertEqual(run.call_args_list[0].args[0][:3], ["gh", "issue", "create"])
        self.assertEqual(run.call_args_list[1].args[0], ["gh", "issue", "close", "https://github.com/o/r/issues/1"])



class PushBarkTest(unittest.TestCase):
    def test_targets_from_app_url_curl_command_or_bare_key(self):
        api = "https://api.day.app"
        self.assertEqual(push_bark.targets(["https://api.day.app/AbC123/这里改成你自己的推送内容"]), [(api, "AbC123")])
        self.assertEqual(push_bark.targets(["curl -X GET https://api.day.app/AbC123/title/body?group=example&ttl=600"]),
                         [(api, "AbC123")])
        self.assertEqual(push_bark.targets(["curl 'https://bark.example.com/K9'"]), [("https://bark.example.com", "K9")])
        self.assertEqual(push_bark.targets([" AbC123 "]), [(api, "AbC123")])
        self.assertEqual(push_bark.targets(["", None, "not a key"]), [])

    def test_two_recipients_deduplicated(self):
        api = "https://api.day.app"
        self.assertEqual(push_bark.targets(["https://api.day.app/ME/x", "curl -X GET https://api.day.app/HER/t/b"]),
                         [(api, "ME"), (api, "HER")])
        self.assertEqual(push_bark.targets(["https://api.day.app/ME/x\nhttps://api.day.app/HER/x", "ME"]),
                         [(api, "ME"), (api, "HER")])

    def test_payload_rings_and_links(self):
        data = push_bark.payload("K", "有货", "- Store A：Phone X", "https://shop/x")
        self.assertEqual((data["device_key"], data["call"], data["level"], data["url"]),
                         ("K", "1", "timeSensitive", "https://shop/x"))
        self.assertNotIn("url", push_bark.payload("K", "t", "b"))

    def test_unset_is_a_no_op(self):
        with mock.patch.dict(os.environ, {"BARK_URL": "", "BARK_URL_2": ""}), \
                mock.patch.object(push_bark.urllib.request, "urlopen") as urlopen, \
                contextlib.redirect_stdout(io.StringIO()):
            push_bark.main("t", "b")
        urlopen.assert_not_called()

    def test_sends_to_each_and_never_prints_keys(self):
        out = io.StringIO()
        env = {"BARK_URL": "https://api.day.app/MYKEY/x", "BARK_URL_2": "curl -X GET https://api.day.app/HERKEY/t/b"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(push_bark.urllib.request, "urlopen") as urlopen, contextlib.redirect_stdout(out):
            push_bark.main("有货", "- Store A：Phone X", "https://shop/x")
            self.assertEqual([json.loads(c.args[0].data)["device_key"] for c in urlopen.call_args_list],
                             ["MYKEY", "HERKEY"])
            self.assertEqual(urlopen.call_args.args[0].full_url, "https://api.day.app/push")
            urlopen.side_effect = [mock.MagicMock(), push_bark.urllib.error.URLError("https://api.day.app/HERKEY down")]
            push_bark.main("有货", "b")
        printed = re.sub(r"::add-mask::\S+", "", out.getvalue())
        self.assertIn("Bark 推送失败（第 2 台）：URLError", printed)
        self.assertNotIn("MYKEY", printed)
        self.assertNotIn("HERKEY", printed)


if __name__ == "__main__":
    unittest.main()
