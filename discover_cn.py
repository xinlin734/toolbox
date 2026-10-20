"""One-off: which mainland China locations reach which Apple Stores."""
import json, time, random
import check

CITIES = ("北京 北京;上海 上海;天津 天津;重庆 重庆;广东 广州;广东 深圳;广东 佛山;广东 东莞;广东 珠海;"
          "浙江 杭州;浙江 宁波;浙江 温州;江苏 南京;江苏 苏州;江苏 无锡;江苏 常州;江苏 南通;山东 济南;山东 青岛;"
          "山东 烟台;辽宁 大连;辽宁 沈阳;吉林 长春;黑龙江 哈尔滨;河南 郑州;湖北 武汉;湖南 长沙;四川 成都;云南 昆明;"
          "广西 南宁;福建 厦门;福建 福州;安徽 合肥;江西 南昌;陕西 西安;贵州 贵阳;山西 太原;河北 石家庄;海南 海口;"
          "内蒙古 呼和浩特;新疆 乌鲁木齐;甘肃 兰州").split(";")

client = check.AppleClient(check.REGIONS["cn"], "iphone-17")
parts = sorted(check.discover_parts(client, "iphone-17"))[:1]
client.warm_up()
stores, reach = {}, {}
for loc in CITIES:
    try:
        rows, _ = check.query_pickup(client, parts, loc)
    except Exception as e:
        print(f"{loc}: ERROR {e}", flush=True)
        time.sleep(10)
        continue
    nums = sorted({r.store_number for r in rows})
    for r in rows:
        stores[r.store_number] = [r.store, r.city]
    reach[loc] = nums
    print(f"{loc}: {len(nums)} stores, cities {sorted({r.city for r in rows})}", flush=True)
    time.sleep(3 + random.random() * 2)
print("TOTAL", len(stores))
print("STORES", json.dumps(stores, ensure_ascii=False))
print("REACH", json.dumps(reach, ensure_ascii=False))
