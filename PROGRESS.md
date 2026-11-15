# 番茄小说 CDP 下载器 — 项目进度与交接文档

> 本文件用于跨 session 交接。新会话请先读完本文件再继续。
> 最后更新：全阶段完成（0~5）。全书验证通过（《十日终焉》1496章 329万字全抓，0缺章0残留PUA）。CLI(--novel/--attach) + FastAPI 网页服务均已实现并冒烟测试通过。项目可交付。
> Web 服务启动：`cd src && uvicorn service:app --host 127.0.0.1 --port 8000`，浏览器开 http://127.0.0.1:8000

## 一、项目目标

新建独立项目，用 **CDP 远程控制用户自有 Chrome** 的方式批量抓取番茄小说正文。
相比旧项目（`../fanqienovel-downloader`）的 requests 直抓方式更稳：复用真实登录态、
真实指纹、热 cookie，风控命中率低；撞验证码可在真窗口手动过，脚本等待续爬。

项目根目录：`fanqie-cdp-downloader`

## 二、为什么做这个（旧项目现状实测结论）

对旧项目 `fanqienovel-downloader` 逐端点实测（2026-07-09）：

| 环节 | 现状 |
|------|------|
| 章节目录 `/page/{id}` | ✅ 还活着，能拿 1496 章 + 正确标题 |
| 正文页 `/reader/{id}` | ❌ 废了。HTML 仅 6KB，无 `muye-reader-content`，旧 xpath 抓 0 段，页面含 `verify`/`captcha`，返回的是风控验证页；正文改前端 JS 渲染 |
| 正文 API `/api/reader/full` | ❌ 废了。返回 200 但 body 长度为 0 |

结论：旧项目正文抓取管线已死，只有**本地解密逻辑**（基于 charset.json 的
PUA 字体反混淆）还有价值。故新项目改用浏览器渲染后从 DOM 抠正文。

## 三、已确定的关键决策

- **浏览器**：Chrome（用户定）
- **CDP 端口**：**9333**（用户明确指定，非默认 9222）
- **连接方式**：Playwright `connect_over_cdp("http://localhost:9333")`，
  不启动自带浏览器，接管用户用专用 profile 起的实例
- **输入方式**（三种）：① 小说ID/URL 列表 ② 指定章节范围 ③ 接管当前已打开的番茄标签页
- **输出格式**：先做 TXT（单文件 + 分章两种），JSON 中间态作内部存储
- **项目位置**：Desktop 同级新目录（已建）
- **反爬策略**：默认串行单标签 + 随机延迟抖动；并发多标签留作可选，不默认开
- **profile 隔离**：专用 `data/chrome-profile`，不干扰日常 Chrome

## 四、GATE 结论（已通过 2026-07-09）

探针实测「十日终焉」第1章（reader/7173216089122439711）：

- **正文渲染方式 = 老规矩**：PUA 码位 + 自定义字体，DOM innerText 即密文。
- **charset.json 完全可复用**：2912 个 PUA 码位，还原率 **100%**，0 残留（best_mode=0）。
  旧项目死的只是抓取管线，本地解密资产完好，`decoder.py` 可原样用。
- **抽取选择器（重要，阶段1按此写）**：用 **`div.muye-reader-content.noselect`**
  （1 节点 / 67 个 `<p>` / 2475 字，纯正文，不含标题与「本章字数」元信息）。
  ❌ 不要用探针按字数选出的 `div[class*="reader-content"]`——它匹配到嵌套双层
  容器，innerText 被数两遍（5004≈2×2475），会导致正文重复。
- 章节标题从 `catalog.fetch_catalog` 的目录取，不从正文节点取。

### 验证码检测误报修复（同日）
`browser.is_verify_page` 原用 `page.content()`（整份 HTML 含内联 JS）子串匹配，
`verify`/`captcha`/`验证码` 必然命中打包 JS 与「获取验证码」按钮 → 正常阅读页误报。
已改为：只看 `inner_text('body')` 的强信号词（去掉单独「验证码」）+ 检测验证码 SDK
容器是否**真实可见**。实测对正常阅读页返回 False。

## 五、进度面板

| 阶段 | 内容 | 状态 |
|------|------|------|
| 阶段0 | 脚手架 + Chrome启动脚本(9333) + charset迁移 + 探针 probe.py | ✅ 完成 |
| GATE | 运行探针验证正文选择器 + charset 是否可复用 | ✅ **通过**(charset 100%) |
| 阶段1 | CDP连接 + 目录 + 单章「取→解」端到端跑通 | ✅ 完成 |
| 阶段2 | 批量(列表/范围) + 断点续传 + 延迟抖动 + 验证码暂停 + TXT输出 | ✅ 完成 |
| 全书验证 | 完整抓取「十日终焉」1496章并核对 | ✅ **通过** |
| 阶段3 | 接管当前页签模式(--attach) | ✅ 完成 |
| 阶段4 | README + 配置 + 收尾 | ✅ 完成 |
| 阶段5 | FastAPI 服务 + 网页界面(下载/进度/停止/成品下载) | ✅ 完成 |

## 六、已建文件清单（阶段0）

```
fanqie-cdp-downloader/
├── PROGRESS.md            # 本文件
├── requirements.txt       # playwright / requests / lxml / tqdm
├── launch_chrome.ps1      # 专用profile + 端口9333 启动 Chrome
├── launch_chrome.bat      # 同上(bat版)
├── data/                  # 运行时数据(chrome-profile 等)
├── output/                # 输出目录(探针样本、TXT)
└── src/
    ├── config.py          # 端口9333、路径、PUA码位、DELAY_RANGE(章间延迟)
    ├── decoder.py         # decode_content / decode_best / charset_health(还原率自检)
    ├── browser.py         # connect / find_fanqie_pages / get_or_open_page /
    │                      #   is_verify_page / wait_manual_verify(交互) / wait_verify_cleared(无人值守轮询)
    ├── catalog.py         # parse_novel_id / fetch_catalog(requests抓目录)
    ├── extractor.py       # 阶段1：extract_chapter(goto取解) / next_chapter(SPA快切,未采用)
    ├── storage.py         # 阶段2：JSON中间态(断点续传) + 单文件/分章 TXT 导出
    ├── downloader.py      # 阶段2：编排 连接→目录→逐章循环→导出。CLI: --novel/--start/--end/--split
    ├── probe.py           # 阶段0探针GATE
    └── charset.json       # 从旧项目迁移的字体映射表
```

## 全书验证结论（2026-07-09）

完整抓取《十日终焉》(novel 7143038691944959011)：
- **1496 章全抓到，index 1~1496，缺章 0，空章 0**。
- 正文合计 **329 万字**，单文件 TXT 9.4 MB，分章 TXT 1496 个文件。
- 抽查首/中/尾 15 章残留 PUA = 0，解密干净，结尾正文通顺。
- 全程 0 失败、0 验证码（登录态真实浏览器 + 极限档延迟 DELAY_RANGE=(0.0,0.3)）。

运行方式（专用 Chrome 已登录并开着调试端口 9333 的前提下）：
```
cd src
python downloader.py --novel 7143038691944959011 --split
# 可选 --start N --end M 指定章节范围；断点续传自动跳过已抓章。
```

### 提速探索结论（重要，别重蹈）
整页 `goto` 单章 ~0.8s 反而比 SPA「下一章」快切 ~1.7s 更快（Chrome 缓存框架/字体，
重复 goto 不重载；快切要额外轮询等 URL/正文变化）。速度瓶颈在 config.DELAY_RANGE，
不在导航。extractor 里 next_chapter/chapter_id_from_url 是那次探索的遗留，downloader 未用。

## 七、已验证（离线，不依赖浏览器）

- ✅ 全部模块语法 / import 通过
- ✅ `catalog.fetch_catalog('7143038691944959011')` → 标题「十日终焉」、1496 章、
  章节名「第1章 空屋」等，UTF-8 编码正确
  （注意：Windows 控制台直接 print 中文会显示乱码，是显示问题，落地到文件正确）
- ✅ `decoder.charset_health()` 逻辑正确（对构造的 PUA 密文还原率 100%）

## 八、下一步操作（GATE，需用户的登录浏览器）

1. 双击 `launch_chrome.bat`（或 PowerShell 跑 `launch_chrome.ps1`），
   在弹出的专用 Chrome 里登录番茄，**别关这个窗口**。
2. 装依赖（若未装）：`pip install -r requirements.txt`
3. 跑探针，三选一：
   ```
   cd fanqie-cdp-downloader\src
   python probe.py --novel 7143038691944959011
   # 或手动翻到某章后： python probe.py --attach
   # 或指定章节：       python probe.py --chapter <reader链接或ID>
   ```
4. 探针输出会给出命中的正文选择器、charset 还原率、及三选一结论。
   把输出贴回会话，据此决定阶段1抽取层写法。

## 九、给接手会话的提醒

- 在拿到 GATE 探针结论前，**不要**写阶段1的抽取层，避免建在错误假设上。
- 若探针结论是「charset 过期」或「canvas/乱序 span」，需先和用户商定新解密/抽取方案。
- 旧项目路径 `../fanqienovel-downloader`，charset.json 与解密逻辑源自其 `src/main.py`
  的 `_decode_content`（约 1126 行）。
- 文件写入有每次 ≤50 行限制，需分块 Write/Edit。
