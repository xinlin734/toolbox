# iPhone Duo 抢购项目交接文档（给 Codex）

更新时间：2026-10-23 16:50 UTC（北京时间 10/24 00:50）
项目负责人：bob（GitHub `xinlin734`），用中文沟通。

本项目有两部分：

1. **云端库存检查器**（GitHub Actions，仓库 `github.com/xinlin734/toolbox`，已稳定运行）：盯美国（ZIP 91016 附近）和中国大陆全部 49 家 Apple Store 的 iPhone Duo 自提库存，有货推 Bark。
2. **国内自动下单**（bob 的 Mac mini 上 `~/apple-cn-order/`，开发中，**目前只演练、不真下单**）：云端一看到国内有货，通过 ntfy 通知 Mac，Mac 上的 Playwright + Chrome 自动走网页结账。

> 本文不含任何密钥、Bark 地址、ntfy 地址、取货人姓名电话等个人信息，只写存放位置。

---

## 0. bob 的硬性要求（必须遵守）

1. **国内只要门店自提（pickup），绝不要送货/快递。** 不要再建议寄送当备选，这是原则。
2. **目标只有一个：拿下任意一个 Duo 自提订单。** 任何颜色、任何容量、全国任何门店/城市都行。
3. **不要 GitHub issue / 评论 / @mention / 邮件提醒。** 之前每小时 ~100 封邮件把 bob 惹火了。提醒只走 Bark 推送。状态用 commit status 和 run annotation 记录（它们不发邮件）。
4. **被 Apple 拦截必须自动恢复**（换新 runner 接着查），不能只记日志。
5. **主动监控、主动汇报。** 每次改动后都要持续确认检查器真的在正常工作，不只是看第一轮；发现问题先自己修，再简短告诉 bob（影响 + 怎么修的）。bob 曾因拦截没人报告而不满。
6. **国内自动下单目前只演练（dry-run），不点「立即下单」。** bob 说：先跑通、把时间压到极致、多门店多次演练，再考虑真下单。真下单要 bob 明确说（例如回「上真的」）。bob 已表示真单可以退款、允许程序自动填取货人并提交，但当前指令是先演练。
7. 不做反机器人规避（换 IP、代理、指纹伪装），不破解 Apple Store App 的加密通信，不装破解 IPA。
8. bob 在 project chat 问状态时，直接给结果，不要只甩一个链接。

---

## 1. 云端库存检查器（toolbox 仓库）

### 1.1 仓库结构

| 文件 | 作用 |
|---|---|
| `check.py` | 核心检查脚本，只用标准库。调 Apple 未公开接口 `/shop/retail/pickup-message`（备用 `/shop/fulfillment-messages`），美国另查 `/shop/delivery-message` 寄送日期。支持 `--region us/cn`、多地点 `;` 分隔、`--watch`、`--pace/--phase`（多 runner 按墙上时钟错开）、`--hot-interval`、`--stop-on-block`、`--hits-file`、`--link-file`、`--on-change` 等。 |
| `test_check.py` | 单元测试（约 50 个）。改 `check.py` 后跑 `python3 -m pytest test_check.py` 或 `python3 test_check.py`。 |
| `.github/workflows/check-us.yml` | 美国检查，`*/5` cron 兜底 + 自我接力链。 |
| `.github/workflows/check-cn.yml` | 国内检查，3 组矩阵（北方/华东/南方），覆盖全国 49 店。 |
| `.github/workflows/surge.yml` | 有货后的「快查」：每个地点每 5 秒查一次，持续到 30 分钟没再见货。 |
| `.github/scripts/run_check.sh` | 包一层 `check.py`：从 Secret 读产品/地点，mask 掉，公开日志只留计数，写 `code=` 和 `rounds=` 注释。 |
| `.github/scripts/notify_stock.sh` | 有货时：①先把 `hits.txt.json` POST 到 `MAC_TRIGGER`（ntfy）②Bark 推送（同店同型号 30 分钟内只推一次）③启动 surge。 |
| `.github/scripts/push_bark.py` | Bark 推送到 `BARK_URL` / `BARK_URL_2` 两台手机，critical 级别响铃；`BARK_QUIET` 设静音时段。 |
| `.github/scripts/notify_delivery.py` | 美国寄送日期提前时推 Bark（状态存在 Actions cache）。 |
| `.github/scripts/state.sh` | 共享状态，存成仓库第一个 commit（`96f27b2…`）上的 commit status：`seen/<region>`、`seen/<region>-<group>`、`pushed/<hash>`。不发邮件。 |
| `.github/scripts/surge.sh` | surge 的 want/start/running/count/看门狗。 |
| `.github/scripts/surge_lanes.sh` | 生成 surge 的 lane 列表（组 × 相位）。 |

仓库是 **public**，所以产品名和 ZIP 藏在 Secret 里，公开日志只打计数。

### 1.2 Secrets（只说位置，值在 toolbox → Settings → Secrets and variables → Actions）

| 名称 | 内容 |
|---|---|
| `TARGET` | 美国：`<产品 slug> <ZIP>` |
| `TARGET_CN` | 国内：`<产品 slug> <省> <市>`（国内实际用的是 workflow 里公开的地点列表，Secret 只提供产品） |
| `BARK_URL` | bob 本人手机（洛杉矶，静音 20–6 点） |
| `BARK_URL_2` | 上海那台手机（静音 23–9 点） |
| `MAC_TRIGGER` | ntfy topic URL，云端国内有货时 POST 给 Mac。**topic 名相当于密码，不要写进仓库或公开日志。** |

### 1.3 美国 check-us.yml

- 每轮 run 盯 4 分钟（`WATCH_MINUTES`），快结束前 `next` job 用 `workflow_dispatch chained=true` 启动下一轮（带 `prev_run`，等上一轮结束再查，保证同时只有一台在查）。`*/5` cron 只是链断了的兜底；`gate` job 发现已有 run 在跑就让开。
- 每分钟查一次自提；店有货时该店每 30 秒查；寄送日期每 5 分钟。`MINUTE_FROM=2026-10-23` 之前 Apple 未开放自提时每 5 分钟查一次。
- 被拦（HTTP 403/429/541 或返回非 JSON）→ `--stop-on-block` 退出码 2 → 立即 dispatch `rescue=true` 换新 runner 接着查剩下的时间，并写 `::warning title=blocked::` 注释。
- 手动输入：`test_alert`（发测试推送）、`order_test`（下单演练，例如 `iphone-18-pro Pro Max`，只推 `BARK_URL`）。

### 1.4 国内 check-cn.yml

- 13 个附近搜索覆盖全国 49 家店（2026-10-20 测绘），分 3 组在 3 个 runner 上跑：
  - 北方：`天津 天津; 辽宁 沈阳; 山东 烟台; 河南 郑州; 湖北 武汉`
  - 华东：`浙江 宁波; 江苏 南京; 福建 厦门; 浙江 温州`
  - 南方：`广东 广州; 重庆 重庆; 湖南 长沙; 云南 昆明; 广西壮族自治区 南宁`
- **坑：** 一个 runner 每分钟查 13 次，3 分钟内就被 HTTP 541；每 runner ≤5 次/分钟。即使 5 次/分钟，约 6 分钟后也会被拒，所以每轮只跑 4 分钟就换新 runner。
- **坑：** 南宁必须写 `广西壮族自治区 南宁`，写 `广西 南宁` 查不到。
- **坑：** 国内的 pickup 查询需要先访问购买页拿 cookie（`Region.warm_on_buy_page=True`）。
- 被拦的组单独 dispatch `rescue_group=<组>` 换新 runner。
- surge 在跑时，check-cn **只给本组的** surge 让路（PR #156；之前给所有组让路，导致南方 2026-10-23 02:29–03:41 UTC 无人在查）。
- `after_surge=true`：surge 结束交回常规检查时用，不再让路。

### 1.5 快查 surge.yml

- 由 `notify_stock.sh` 在有货时启动；check-cn/check-us 的 gate 里 `surge.sh <region>` 当看门狗：30 分钟内见过货但 surge 没在跑就重启。手动开窗口：dispatch surge.yml，填 `region` 和 `keep_until`（epoch 秒）。
- `PACE=15, STEP=5`：每组 3 条 lane（相位 0/5/10），每条每 15 秒查一轮，合起来每个地点每 5 秒查一次。国内 3 组 × 3 = 9 条 lane；美国 3 条。
- 国内每条 lane 的 runner 只活 `CN_SECONDS=60` 秒（约 20 次查询，低于 Apple 约 30 次开始拒绝的阈值），美国 240 秒。提前 `LEAD_SECONDS=30` 启动下一个 runner。
- 每条 lane 是独立的接力链，交接时间错开，因为 GitHub 同时最多约 20 个 job（和常规检查共享）。
- 每次交接只用一次 API 调用（repo token 每小时 1000 次）。
- **交回常规检查的规则（PR #157/#158/#159，踩了三次坑）：** lane 在 30 分钟没见货后结束；如果它是 **phase 0** 或者 **本组唯一还在跑的 lane**，就 dispatch `check-cn rescue_group=<组> after_surge=true`。
  - 只看数量会失败：三条同时结束时每条都看到别的还在跑，谁都不交。
  - 只看 phase 0 会失败：后来才见货的 lane 独自多跑，结束时没人交。
- **PR #160：** 组内 lane 共享 `seen/<region>-<group>` 状态，一条见货三条都继续，否则会掉到 15 秒一次。被拦时，如果交接已经开始（`handing`/`handed` 文件）就不再 rescue，否则会出现两条重复链。
- **不要手动单独重启某一条非 phase 0 的 lane**：它单独跑时永远不会交回。

### 1.6 推送与状态

- 只推 Bark，两台手机，critical 级别（静音时段改 passive）。推送带下单链接 `https://www.apple.com.cn/shop/product/<型号>`（PR #11），点开就选好了型号/颜色/容量；门店无法通过 URL 预选。
- 去重：`state.sh pushed 30` 读出 30 分钟内推过的行。
- 2026-10-23 ~00:33 UTC 起完全不再开 issue / 评论。**永远不要再引入 GitHub issue 当提醒。** 旧的 issue #13–#152 是有货历史记录，可以读来分析规律。

### 1.7 监控和自检

- **每小时自检**：claude.ai 上的定时任务「国内+美国检查每小时自检」（每小时第 33 分钟），在线程「监控运行情况与国内全国版」里汇报。它运行 `monitor/cn_coverage.py 60`（仓库里有一份副本，原件在 Claude 项目共享目录）（读 GitHub Actions API，需要 gh 认证）。健康标准：
  - 北方/华东/南方三行「合并覆盖」都是 0 次超过 90 秒无人查（这是最主要的指标，合并了常规检查和快查）；
  - 快查 0 失败、0 查询失败、lane 断档不超过 15 秒，每分钟 surge run 不超过约 9 个（否则是失控，要取消并修）；
  - 「逐台核对」0 查询失败；美国两次检查开始间隔不超过 6 分钟；没有失败的 run；
  - 每次被拦（`被拦（运行注释）`）都有 rescue run；
  - 组 30 分钟没货后快查要停，常规检查要接上；
  - 「新写的 issue」必须为 0。
- Codex 接手后如果不能用这个定时任务，需要自己找办法每小时跑一次同样的检查（例如本地 cron 跑 `python3 monitor/cn_coverage.py 60`）。
- 每个 run 的 `::notice title=rounds::rounds=N failed=N code=N` 注释可通过 API 读取，不用下载日志。
- 统计（10/23 05:30–15:20 UTC）：快查约 2200 次 0 失败；常规约 290 次 0 失败；被拦 11 次全部自动接上，没有超过 90 秒的空档。

### 1.8 国内补货规律

- 10/22 之前几乎没货（只有 10/21 04:26 UTC 一次）。
- 10/23：北京时间 05:58 开始零星出现，07:24–08:34 几乎连续跳货，一小时 100 多次，每次只持续 5–60 秒，很多店每几秒翻转一次（深圳万象城、广州天环广场、珠江新城、北京几家）。型号是 Duo 256/512GB 星光白色/夜空色（如 MK2M4CH/A、MK2P4CH/A、MK2Q4CH/A）。
- 推测是每天门店放库存 + 未付款订单退回。**要验证 10/24 早上是否重复。**
- 结论：窗口在北京时间约 06:00–08:40（UTC 22:00–00:40）。

### 1.9 开发流程和权限

- Claude 直接 push 到 toolbox `main` 被自动模式拦截，所以改动都走 PR，由 bob 在手机上合并（GitHub App → toolbox → Pull requests → #N → Merge），或在自检中自己合并（bob 授权过自检修复后合并）。
- 不要在 public 仓库上跑会读 Secret 的探测 workflow。
- 临时分支 `claude/cn-control-test` 删不掉（代理禁删 ref），可以忽略。
- `xinlin734/hello-world` 已废弃（PR #8 删除了 workflow，issue #6 过时），**不要用它判断检查器是否在跑**。

### 1.10 相关 PR 速查

| PR | 内容 |
|---|---|
| #6 | 国内全国版（49 店、3 组、≤5 次/分钟） |
| #11 | 推送链接直达型号页 |
| #25、#41 | 有货后快查 surge.yml |
| #156 | check-cn 只给本组 surge 让路 |
| #157、#158、#159 | surge 交回常规检查的空档修复 |
| #160 | 组内 lane 共享见货状态；交接开始后不再 rescue |
| #161 | `write_hits` 同时写 `HITS.json`，`notify_stock.sh` 最先 POST 给 `MAC_TRIGGER`。**重构时这两处都要保留。** |

---

## 2. 国内自动下单（bob 的 Mac mini）

### 2.1 现状一句话

链路已经通了，从云端有货到 Mac 收到通知不到 1 秒，Mac 自动走到最终确认页「准备下单了吗？」，停在「立即下单」前。整套约 **44–47 秒**，其中我们自己的操作约 3.4 秒，剩下约 41 秒是 Apple 网页结账服务器：选店、继续、取货人、付款 4 步，每步固定约 10 秒，必须一步等一步，合并或并发会被拒。**这是网页版的下限。** 窗口只有 5–60 秒，所以还偏慢。

### 2.2 架构

```
GitHub Actions（check-cn / surge）
   └─ 有货 → notify_stock.sh 第一件事 POST hits.json 到 MAC_TRIGGER（ntfy topic）
                 JSON 例：[{"part":"MK2P4CH/A","store_number":"R359","store":"南京东路","city":"上海","state":"上海"}]
Mac mini（launchd 常驻）
   └─ 流式订阅 <ntfy topic>/json（每行一个事件，正文在 "message" 字段，忽略 keepalive/open）
       └─ 取第一个命中 → Playwright 用专用 Chrome profile 在已登录页面里结账 → 停在确认页（dry-run）
```

- **Mac 不自己查库存。** 早期在 Mac 上连续查几个城市后，请求从 3 秒变成每次约 52 秒（疑似限速），会拖慢下单那条线路。所以检测全部交给云端。
- 选择命中：任何 Duo 颜色/容量/门店都可以，取第一个。

### 2.3 Mac 上的文件（`~/apple-cn-order/`）

| 路径 | 说明 |
|---|---|
| `order.py` | 主脚本，Python + Playwright，`--dry-run` 默认开启（停在「立即下单」前） |
| `profile/` | 专用 Chrome profile（`launch_persistent_context`，有界面），已登录 bob 的中国区 Apple ID，与 bob 平时的 Chrome 分开 |
| `config.json` | 取货人姓名、手机号、邮箱、身份证后 4 位。**只存本地，绝不进仓库或日志。** |
| `.venv/` | Python 虚拟环境 |
| `com.bob.apple-cn-order.listen.plist` | launchd 任务，已由 bob 安装到 `~/Library/LaunchAgents/` 并 `launchctl bootstrap`，开机自启监听 ntfy |
| `listen.log` | 监听日志 |
| `scratch/drills.sh` | 多门店轮流演练脚本（广州珠江新城、深圳万象城、北京三里屯、杭州西湖、成都太古里、上海环贸），北京时间 05:00 自动停 |
| `scratch/review.png` | 演练到确认页的截图 |

Mac 上的 Claude Code 权限：bob 在 `~/.claude/settings.json` 的 `permissions.allow` 里加了 `Bash(~/apple-cn-order/.venv/bin/python:*)`、`Edit(~/apple-cn-order/**)`、`Write(~/apple-cn-order/**)`。具体代码细节以 Mac 上的文件为准，本文没有逐行核对。

### 2.4 结账流程和已做的优化

1. 查库存（仅调试时用）：在已登录页面里 `fetch('/shop/retail/pickup-message?pl=true&mts.0=regular&parts.0=<PART>&searchNearby=true&location=<省 市>')`，看 `body.stores[].partsAvailability[PART].pickupDisplay == "available"`，约 0.3 秒；Duo 8 个型号一次请求约 0.7 秒。
2. 商品页 `https://www.apple.com.cn/shop/product/<PART>` → 加入购物袋（自动选「不折抵、不加 AppleCare+」），约 0.5–1.5 秒。
3. 购物袋 → 结账 → 选取货 → 选店 → 时段 → 取货人（从 config.json 自动填）→ 付款（支付宝）→ 确认页。
4. 已做的提速：
   - **跳过省/市/区逐级选择**：网页每选一级都要等一次服务器（bob 手动也一样慢，不是限速），改为直接发最终的搜门店/选店请求（用页面自己的 checkoutx 接口，在 bob 自己的会话里）。选店从 11 秒降到 0.6 秒。
   - 时段一出现就选，不再固定等待、不等 networkidle。
   - 地区选择不再靠鼠标点（结账页顶部标题栏曾挡住省份选择框）。
   - 购物袋里只放要抢的那一台。
5. 安全护栏：购物袋里不止目标一台、有商品显示不可取货、或者结账跳到送货地址页，程序立即停下（防止误下送货单）。

### 2.5 实测耗时

| 版本 | 门店 | 到确认页 | 备注 |
|---|---|---|---|
| 点界面 | 上海南京东路 | 62 秒 | 首次跑通 |
| 点界面 | 广州珠江新城 | 84.7 秒 | 选地区 41 秒 |
| 点界面 | 深圳万象城 | 56.5 秒 | 每步约 10 秒 |
| 点界面 | 北京三里屯 | 74.8 秒 | 选地区 31 秒 |
| 点界面 | 杭州西湖 | 失败 | 上一单的结账页没清干净 |
| 直接请求 | 深圳 | **44.5 秒** | 自己的操作 3.4 秒 |
| 直接请求 | 上海 | **47.4 秒** | |

下午早些时候 Apple 每步只要 2–3 秒，如果恢复，整套可到 12–15 秒，代码不用改。登录后没遇到验证码或机器人拦截。

### 2.6 下一步（未完成）

1. **10/24 北京时间 06:00–08:40 窗口**：真实云端有货也只演练并计时，记录每一步 Apple 服务器耗时。北京时间 05:00 停掉测试演练，让 IP 休息。已有一个一次性提醒 21:30 UTC 检查准备情况。
2. **iPhone 镜像方案**（正在等 bob 回答）：网页结账到下限了，App 更快。Mac 上装不了 Apple Store App（App Store 里没有 Mac 版，不用破解 IPA）。思路是用 macOS 15+ 的「iPhone 镜像」让程序点手机上的 App，估计每点一下多 0.3–0.5 秒，6–8 下，大约 10–15 秒到确认页。需要 bob 确认：①手机上的 Apple Store App 能否用中国区账号在中国门店下自提单；②Mac mini 是否有「iPhone 镜像」（macOS 15+）；③iPhone 能否一直放在 Mac 旁边、插电、锁屏。都可以的话做一个镜像版，和网页版实测比较。
3. 时间压下来且稳定后，把结果整理给 bob，由 bob 决定是否真下单。真下单流程：点「立即下单」→ Bark 叫 bob 用支付宝付款（国内 Apple Pay 不带取货人信息，付款必须 bob 本人确认）。
4. 正式开抢前确认购物袋是空的（之前演练加过 iPhone 18 Pro，以及一台寄送的 Duo 512GB 星光白色，已经清空过，开抢前再检查）。

### 2.7 已讨论过、不做的事

- 不破解或伪装 Apple Store App 的私有接口（会被识别，订单被取消、账号被封）。
- 不做代理/换 IP/指纹伪装。
- Mac 上不要 1–2 秒高频查库存（会限速自己下单用的 IP 和账号）。
- 不建议送货，送货不考虑。

---

## 3. 交接给 Codex 的注意事项

- 和 bob 用中文沟通，简短，先说结论。
- 需要 bob 操作时，给出准确的点击路径或一行命令。
- 每次改检查器后都要观察几轮真实运行（尤其是 surge 交接、被拦恢复），用 `cn_coverage.py` 验证「合并覆盖」没有空档。
- 改 `check.py` 先跑 `test_check.py`。
- 任何时候都不要把 Secret、Bark key、ntfy topic、取货人信息写进 public 仓库、日志或 issue。
