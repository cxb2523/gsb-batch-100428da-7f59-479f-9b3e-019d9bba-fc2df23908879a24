# ⚡ 今日头条（字节跳动系）协议级逆向复现与数据采集引擎 (2026年8月最新版)

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg?logo=python" alt="Python Version">
  <img src="https://img.shields.io/badge/Node.js-18.0+-green.svg?logo=node.js" alt="Node Version">
  <img src="https://img.shields.io/badge/JSVMP-Dual_Engine-orange.svg" alt="JSVMP Engine">
  <img src="https://img.shields.io/badge/0_Browser-100%25_Offline-brightgreen.svg" alt="Zero Browser">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
  <img src="https://img.shields.io/github/stars/ludapao675-debug/toutiao-news-feed-crawl?style=social" alt="GitHub Stars">
</p>

<p align="center">
  <img src="./cover.jpg" alt="ByteDance JSVMP Reverse Engineering" width="100%">
</p>

> **ByteDance Security Reverse Engineering & Protocol Reproduction Engine**  
> 🎯 涵盖今日头条 **主页推荐新闻流（Feed）** 与 **文章详情评论区（Comments）** 的纯协议全量抓取  
> 🚀 **100% 纯本地离线执行**：无需常驻 Chromium / Playwright / Selenium 浏览器，毫秒级现签现发，内存开销降低 95%+。

---

## 🌟 核心技术亮点

- ⚡ **双 JSVMP 引擎全量脱机还原**：
  - **`a_bogus` 算法**：还原 `bdms.js`（Magic: `HNOJ@?RC`）JSVMP 虚拟机，通过轻量级 Node.js 原型链沙箱触发 `XMLHttpRequest.prototype` 拦截，纯算输出 168 位签名；
  - **`_signature` 算法**：还原 `acrawler.js`（`_$jsvmprt` 工业级家族，`13 * j % 241` 指令哈希派发）JSVMP 虚拟机，15ms 快速生成 `_02B4Z...` 评论接口签名。
- 🛡️ **0 浏览器开销与轻量沙箱**：基于 Node.js `vm` 纯原生精细化补齐 `Window` / `Document` / `Navigator` / `Screen` / `Canvas 2D` / `WebGL` / `EventTarget` 原型链，彻底摆脱笨重的无头浏览器渲染。
- 🔄 **Token 种子离线流转**：解析 `mssdk.bytedance.com` 与 `localStorage.xmst` 之间的关联映射，实现 `msToken` 离线跨天复用与动态注入。
- 🚀 **开箱即用高可用**：提供统一 Python 抽象层与 CLI 命令行工具，支持单篇/批量文章评论拉取及主页瀑布流抓取。

---

## 🏗️ 字节跳动 2026 最新风控与加密全景架构

```
                                  ┌────────────────────────────────────────────────────────┐
                                  │               今日头条客户端 / 协议发起端                │
                                  └──────────────────────────┬─────────────────────────────┘
                                                             │
                             ┌───────────────────────────────┴───────────────────────────────┐
                             │                                                               │
                    【主页推荐新闻流 Feed】                                          【文章详情评论区 Comments】
                             │                                                               │
                             ▼                                                               ▼
             GET /api/pc/list/feed                                           GET /article/v4/tab_comments/
                             │                                                               │
        ┌────────────────────┴────────────────────┐                                          │
        │                                         │                                          │
        ▼                                         ▼                                          ▼
 核心参数: msToken                         核心参数: a_bogus                           核心参数: _signature
 (128位 种子令牌)                          (168位 动态行为签名)                         (47位 动态哈希签名)
        │                                         │                                          │
        ├─────────────────────────────────────────┤                                          │
        │                                         │                                          │
        ▼                                         ▼                                          ▼
 来源: mssdk 响应头 x-ms-token            算法: bdms.js (HNOJ@?RC JSVMP)              算法: acrawler.js (_$jsvmprt JSVMP)
 存储: localStorage.xmst                  机制: 劫持 XHR.prototype.send()             机制: 13*j%241 哈希派发 + 十六进制取指
 注入: bdms init_me[24].inner             特性: 现签现用、防重放检验                  特性: Query 参数字符串哈希与时间指纹
        │                                         │                                          │
        └────────────────────┬────────────────────┘                                          │
                             │                                                               │
                             ▼                                                               ▼
        ┌─────────────────────────────────────────┐                 ┌─────────────────────────────────────────┐
        │      Node.js 离线沙箱 (bdms Engine)     │                 │   Node.js 离线沙箱 (acrawler Engine)    │
        │     (cases/toutiao_news_feed_crawl)     │                 │        (cases/toutiao_acrawler)         │
        └────────────────────┬────────────────────┘                 └────────────────────┬────────────────────┘
                             │                                                           │
                             └───────────────────────────────┬───────────────────────────┘
                                                             │
                                                             ▼
                                              ┌─────────────────────────────┐
                                              │  直连 HTTP GET (毫秒级响应)  │
                                              └──────────────┬──────────────┘
                                                             │
                                                             ▼
                                              ┌─────────────────────────────┐
                                              │  HTTP 200 返回结构化 JSON   │
                                              └─────────────────────────────┘
```

---

## 🔬 深度算法逆向剖析

### 1. `a_bogus`：JSVMP 虚拟机与 XHR 劫持链

在 2026 年最新的头条前端架构中，`a_bogus` 并非暴露在 `window.ye(url)` 或 `window.sign(url)` 等可直接调用的全局函数中，而是与前端网络通信底层强耦合：

1. **胶水层注入**：页面引入 `sdk-glue.js` 包装原生 `XMLHttpRequest`；
2. **虚拟机初始化**：`bdms.js` 执行 `bdms.init({ aid: 24, pageId: 6457, paths: { include: [...] } })`，完成对 `XMLHttpRequest.prototype.open` 与 `XMLHttpRequest.prototype.send` 的深度劫持；
3. **运行时参数装配**：
   - 虚拟机启动时从 `localStorage.getItem('xmst')` 读取 `msToken` 并写入内部状态槽 `me[24].inner`；
   - 在 XHR 执行 `send()` 的微任务阶段，读取当前请求的 Method、Path、Query、Canvas/WebGL 指纹及鼠标/触摸事件队列；
   - 执行字节码（Header 特征 `HNOJ@?RC`，Hex: `48 4e 4f 4a 40 3f 52 43`），产出 168 位的 Base64URL 格式 `a_bogus` 并动态追加到 URL 尾部。
4. **离线复现策略**：
   通过 [browser_shim.js](browser_shim.js) 构造高保真原型链环境，在 Node.js `vm.runInContext` 中加载 [raw/bdms.js](raw/bdms.js)，模拟触发 `new XMLHttpRequest().send()`，成功实现脱机计算。

### 2. `_signature`：`_$jsvmprt` 工业级 JSVMP 家族

今日头条的评论接口及部分老版 Feed 接口依赖 `_signature`：

1. **特征指纹**：
   - 运行时标记：`_$jsvmprt`（JSVM protect runtime）；
   - 哈希分发循环：`13 * j % 241`（字节跳动专属指令派发指纹）；
   - 字节码形态：两字符一组的十六进制字符串（如 `parseInt(""+b[O]+b[O+1], 16)`）；
   - 常量池解密：`String.fromCharCode(r ^ i.p[P])`。
2. **纯算复现**：
   通过 [replay_real.js](replay_real.js) 提供确定性低频+高频噪声 Canvas 垫片，驱动真实 `byted_acrawler.sign(url)`，在 15ms 内为目标 URL 附加 `_02B4...` 签名。

---

## 📁 仓库文件结构

```
.
├── README.md                      # 完整技术架构文档与使用说明
├── manifest.json                  # 案例元数据与签名清单
├── requirements.txt               # Python 依赖清单
├── package.json                   # Node.js 配置
├── repro.py                       # 🚀 主页推荐新闻流一键复现与抓取入口
├── fetch_article_comments.py      # 💬 任意文章评论区一键复现与抓取入口
├── sign_abogus.py                 # Python 对 Node a_bogus 沙箱的调用抽象
├── replay_bdms_offline.js         # Node.js a_bogus 离线签名核心
├── xhr_real_network.js            # Node 真实网络与 XHR 适配层
├── shim_abogus_harden.js          # 环境硬化垫片 (WebGL/Canvas/Audio/UA-CH)
├── shim_event_target.js           # EventTarget 与鼠标轨迹模拟
├── mstoken_store.js               # msToken 缓存与轮换存储器（缓存文件运行时自动生成）
├── mstoken_cache.json             # ⚠️ 运行产物：msToken 缓存，gitignore 忽略，本地自动生成
├── browser_fingerprint.json       # ⚠️ 运行产物：浏览器指纹快照，gitignore 忽略，本地自动生成
├── refresh_browser_fingerprint.js # 连接真实浏览器自动采集指纹并写入 browser_fingerprint.json
├── refresh_env_snapshot.js        # 连接真实浏览器自动采集环境快照（含 msToken/xmst）
├── raw/
│   └── bdms.js                    # 字节跳动 JSVMP 原始算法文件
└── sample_data.json               # 实测捕获的结构化新闻数据样本
```

---

## ⚡ 快速上手

### 1. 环境准备

- **Python**: 3.8+ （无需安装额外重型包，仅依赖标准库）
- **Node.js**: >= 18.0.0

```bash
git clone https://github.com/ludapao675-debug/toutiao-news-feed-crawl.git
cd toutiao-news-feed-crawl
```

### 2. 抓取今日头条主页推送新闻

```bash
# 抓取主页推荐新闻（默认输出前 10 条并保存至 sample_data.json）
python repro.py

# 指定展示条数
python repro.py --limit 5
```

**实时输出示例：**
```text
================ 实时抓取结果 (共 12 条) ================

[1] 标题: 诱骗中国演员王星等人至境外 对部分女性实施奸淫和强迫卖淫 跨境人口贩卖集团17人被公诉
    来源: 三湘都市报 | 时间: 2026-08-20 14:38:29 | 阅读: 217174 | 评论: 0
    链接: https://www.toutiao.com/article/7675709769629401615/

[2] 标题: 花53000元回收购物卡，成交数小时后56张卡全部被消费沦为空卡！当事人已报警，作案手法曝光
    来源: 齐鲁壹点 | 时间: 2026-08-20 14:38:28 | 阅读: 427 | 评论: 0
    链接: https://www.toutiao.com/article/7675951910888735247/

[3] 标题: 许家印被判处无期徒刑 恒大集团、恒大地产等案一审宣判
    来源: 四川观察 | 时间: 2026-08-20 14:38:20 | 阅读: 4595309 | 评论: 1842
    链接: https://www.toutiao.com/article/7675959400783184384/
=================================================================
```

### 3. 抓取指定文章全部评论

```bash
# 抓取指定文章的评论（传入文章 group_id / item_id）
python fetch_article_comments.py 7673783789474071075
```

**实时输出示例：**
```text
开始抓取文章 [7673783789474071075] 的评论列表...
成功获取到 19 条热门评论：

1. [小秦一家亲] (海南) 2026-08-14 19:08:33
   内容: 既然这么多人喜欢开电动车，为什么还要禁燃油车？交给人民自己选择不好了吗？为啥还用政策禁燃售燃油车？[灵光一闪]
   👍 点赞: 3 | 💬 回复: 4
------------------------------------------------------------
2. [林十森] (广东) 2026-08-14 15:42:35
   内容: 主要是用电安全，自燃的10秒逃生，上海理想商务车视频，想到有点怕
   👍 点赞: 3 | 💬 回复: 2
```

---

## 🐍 Python 代码集成

### 集成到现有爬虫项目中：

```python
from repro import fetch_toutiao_homepage_news
from fetch_article_comments import fetch_comments

# 1. 纯本地拉取主页 Feed 推荐流
news_list = fetch_toutiao_homepage_news()
for news in news_list:
    print(f"新闻: {news['title']} (URL: {news['url']})")
    
    # 2. 纯本地拉取该新闻评论区
    if news.get("group_id"):
        comments = fetch_comments(group_id=news["group_id"], offset=0, count=20)
        print(f"  └─ 成功抓取 {len(comments)} 条评论")
```

---

## ⚠️ 风控防封与对抗经验（必读）

1. **时效性防重放（现签现发）**：
   - 字节跳动对 `a_bogus` 部署了严格的服务端时间窗口与单次消费校验；
   - **禁止** 预先批量生成一堆带签名的 URL 存入数据库再慢慢消费；
   - **必须** 采用“现签现用”架构，Node 算出签名后在 1~2 秒内发起 HTTP 请求。
2. **msToken 种子维护**：
   - `msToken` 记录在 `mstoken_cache.json` 中，单次有效周期一般在 24 小时以上；
   - **仓库不携带任何真实 Token/指纹**：`mstoken_cache.json`、`browser_fingerprint.json`、`live_canvas*` 等
     运行产物已加入 `.gitignore`。首次运行需先连接真实浏览器自动采集：
     ```bash
     node refresh_browser_fingerprint.js   # 采集浏览器指纹 → browser_fingerprint.json
     node refresh_env_snapshot.js          # 采集环境快照与 msToken → mstoken_cache.json
     ```
     或在字节系页面（toutiao.com）DevTools → Network 抓包 `x-ms-token` 响应头，手动写入 `mstoken_cache.json`；
   - 若出现服务端连续返回 HTTP 200 空 Body，说明 Token 种子已轮换，重新执行上述采集即可自愈。
3. **高频请求控制**：
   - 建议在并发请求间加入 0.5s ~ 1.5s 的随机延时，避免单 IP 请求频次过高触发滑动验证码。

---

## 📄 License & 免责声明

- 本项目代码仅用于 **网络安全研究、反爬虫技术对抗学习与学术探讨**。
- 请勿将本项目用于任何侵犯版权、恶意爬虫、商业牟利或违反相关法律法规的场景。
- 遵循 [MIT License](LICENSE)。
