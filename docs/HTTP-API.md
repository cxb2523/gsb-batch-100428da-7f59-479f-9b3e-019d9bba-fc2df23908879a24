# HTTP API

`python/douyin_http.py` 把 dy-cli 与私信驱动包装成本地 HTTP 服务。默认只监听
`127.0.0.1:7709`，**不要**直接暴露到公网。

它面向的是"上层 agent / 别的程序"，DSH 插件本身并不依赖它 —— 两者共享同一批
Python 脚本和同一份 `data/`。

## 启动

```bash
# Linux / macOS
.venv/bin/pip install -r python/requirements-http.txt
./scripts/run-http.sh

# Windows
.venv\Scripts\pip install -r python\requirements-http.txt
.\scripts\run-http.ps1

# 或者直接
python python/douyin_http.py
```

常驻可以用 `deploy/douyin-api.service`（systemd 模板）。

## 约定

- 请求与响应都是 JSON。
- 成功：HTTP 200，body 形如 `{"ok": true, ...}`。
- 失败：HTTP 200 但 `{"ok": false, "error": ...}`（**例外**：私信接口的错误是
  `{"ok": false, "error": {"code": "...", "message": "..."}}`）。
- 只读接口不产生副作用。

## 接口

### `GET /health`

依赖自检。

```bash
curl -s http://127.0.0.1:7709/health | jq .
```

```json
{
  "ok": true,
  "python": "/opt/dsh-douyin-whale/.venv/bin/python",
  "dy": true,
  "ytdlp": true,
  "ffmpeg": true,
  "im": true,
  "im_send_allowed": false,
  "im_allow_to": [],
  "data_dir": "/opt/dsh-douyin-whale/data",
  "cookie_present": true
}
```

### `GET /status`

`dy-cli status` 的原始输出（登录态、cookie 路径等）。

### `POST /trending`

抖音热榜。

```bash
curl -s -X POST http://127.0.0.1:7709/trending | jq .
```

### `POST /search`

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `keyword` | string | 必填 | 搜索词 |
| `count` | int | 10 | 1–20 |
| `sort` | string | `综合` | `综合` / `最多点赞` / `最新发布` |
| `offset` | int | 0 | 可选。第二页起传上一页的 `next_offset` |
| `logid` | string | 空 | 可选。第二页起必须传上一页响应里的 `logid` |

```bash
curl -s -X POST http://127.0.0.1:7709/search \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"猫","count":5}' | jq .
```

**翻页必须带 `logid`。** 抖音搜索结果靠上一页的 `extra.logid`（请求参数里叫
`search_id`）串起来；只把 `offset` 往后挪、不带 `logid`，第二页通常直接返回空
列表。所以本接口固定回传翻页所需的三件套：

```json
{
  "ok": true,
  "keyword": "猫",
  "outcome": "completed",
  "offset": 0,
  "next_offset": 5,
  "logid": "1234567890...",
  "has_more": true,
  "items": 5,
  "raw_count": 5,
  "requested": 5,
  "chained_with_logid": false,
  "data": { "data": [{ "aweme_info": { "aweme_id": "..." } }] }
}
```

下一屏就是 `{"keyword":"猫","count":5,"offset":5,"logid":"<上一页的 logid>"}`。

`outcome` 是这一页的「翻页结局」：

| outcome | 含义 | 调用方动作 |
|---|---|---|
| `completed` | 正常拿到条目 | 需要更多就带 `logid` 翻下一页 |
| `no_more` | 上游明确说没有更多（`has_more=false`） | 停 |
| `empty_page` | 空列表，但上游**没有**说没有更多（多半是丢了 `logid`） | 停，别当「已采完」 |
| `challenge_required` | 命中了人机验证（`verify_check`） | 停；需要人在专用窗口里手动过验证 |
| `rate_limited` | 429 / 频繁 | 停，隔一段时间再来 |
| `not_authenticated` | 未登录 / 登录失效 | 停，先 `POST /status` 看登录 |
| `parse_failed` | 上游返回体解析失败 | 停 |
| `backend_unavailable` | 子进程失败 / 超时 / 空响应 | 停 |
| `invalid_args` | 参数不合法 | 改参数重试 |

遇到 `rate_limited` / `not_authenticated` / `parse_failed` / `backend_unavailable` /
`invalid_args` 时 HTTP body 是 `{"ok": false, ...}`，但**已经拿到的条目仍放在
`data.data` 里**，`next_offset` / `logid` 也会一并返回，调用方不必因为中止而丢掉数据。

不传 `offset`/`logid` 时行为与旧版完全一致（走 `dy search` 首屏），
传了任一个就走 `python/dy_query.py`（同一套 dy-cli 签名逻辑，只多参数管道）。

### `POST /detail`

视频详情。`id` 可以是 `aweme_id`，也可以是整段分享链接。

```bash
curl -s -X POST http://127.0.0.1:7709/detail \
  -H 'Content-Type: application/json' \
  -d '{"id":"7xxxxxxxxxxxxxxxxxx"}' | jq .
```

### `POST /profile`

用户主页。`user` 可以是 `sec_uid`、抖音号或昵称。

### `POST /download`

下载无水印视频到 `DOUYIN_MEDIA_DIR/downloads`，返回本地路径。

```bash
curl -s -X POST http://127.0.0.1:7709/download \
  -H 'Content-Type: application/json' \
  -d '{"aweme_id":"7xxxxxxxxxxxxxxxxxx"}' | jq .
```

返回：

```json
{
  "ok": true,
  "aweme_id": "7xxxxxxxxxxxxxxxxxx",
  "path": "/opt/dsh-douyin-whale/data/media/downloads/7xxx.mp4",
  "cached": false,
  "via": "yt-dlp",
  "desc": "视频文案",
  "author": "作者",
  "duration": 32.4,
  "size": 4812345
}
```

> 下载走的是"移动端分享页解析直链 + yt-dlp generic 提取"的路线，绕开了
> yt-dlp 自带 Douyin 提取器所需的 `a_bogus` 签名。细节见源码顶部注释。

### `POST /frames`

下载并均匀抽帧，只返回帧文件路径，**不**代替你调用视觉模型。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `aweme_id` | string | 必填 | 视频 ID 或分享链接 |
| `n` | int | 8 | 4–12 帧 |

```bash
curl -s -X POST http://127.0.0.1:7709/frames \
  -H 'Content-Type: application/json' \
  -d '{"aweme_id":"7xxxxxxxxxxxxxxxxxx","n":8}' | jq .
```

```json
{
  "ok": true,
  "aweme_id": "7xxx",
  "path": ".../7xxx.mp4",
  "frames": [".../7xxx_0.jpg", ".../7xxx_1.jpg"],
  "desc": "视频文案",
  "duration": 32.4
}
```

拿到帧路径后，把它们喂给支持图片输入的多模态模型即可（DSH 里就是
`vision_ask` 之类的能力）。

## 私信 / 群聊

> 所有私信操作默认 **headless**（无窗口弹出），由 HTTP API 的 `DOUYIN_HEADLESS=1` 控制。
> 手动调试时在环境里设 `DOUYIN_HEADLESS=0` 可以看到浏览器操作过程。

### `POST /im/status`

检查登录态、风控页、私信页搜索框。会真的启动一次浏览器，约 10–30 秒。

### `POST /im/conversations`

会话列表，`kind` 为 `user` 或 `group`。

```bash
curl -s -X POST http://127.0.0.1:7709/im/conversations \
  -H 'Content-Type: application/json' -d '{"limit":20}' | jq .
```

### `POST /im/groups`

只列群聊。

### `POST /im/send`

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `to` | string | 必填 | 对方昵称（需完全一致） |
| `text` | string | 必填 | ≤ 500 字 |
| `send` | bool | `false` | `true` 才尝试真实发送 |

### `POST /im/group-send`

同上，`group` 为目标群名；若打开的会话不像群聊，会直接 `NOT_GROUP` 中止。

### dry-run 语义（重要）

真实发送需要**同时**满足：

1. 请求体 `send: true`；
2. 服务进程 `DOUYIN_IM_ALLOW_SEND=1`；
3. （可选）若设置了 `DOUYIN_IM_ALLOW_TO`，目标必须在白名单里。

任何一道不满足：

- 前两道不满足时，文本会被填进输入框、截图、然后清空，**不会发出**；
- 第三道不满足时，直接返回 `TARGET_NOT_ALLOWED`，连输入框都不填。

响应里的 `data.dry_run` 会明确告诉你这次到底跑在什么模式：

```json
{
  "ok": true,
  "data": {
    "target": "某某",
    "resolved_title": "某某",
    "text": "你好",
    "dry_run": true,
    "send_requested": true,
    "send_allowed_by_server": false,
    "cleared": true,
    "screenshot": ".../im-shots/20261023-120000_某某_dryrun.png"
  },
  "warning": "服务端未开启真实发送（DOUYIN_IM_ALLOW_SEND != 1）……"
}
```

## 错误码

| code | 含义 | 处理 |
|---|---|---|
| `NEED_LOGIN` | 没有 cookie 或已失效 | 重新导出 storage_state |
| `RISK_CONTROL` | 页面弹安全验证 | 人工处理，不要绕过 |
| `PAGE_NOT_READY` | 私信页没渲染出来 | 重试或跑 `tools/probe_im.py` 对照选择器 |
| `NOT_FOUND` | 搜不到目标 | 检查昵称是否完全一致 |
| `CHAT_NOT_OPEN` | 点了但右侧没开 | 对方可能限制私信 |
| `NO_COMPOSER` | 找不到输入框 | 前端改版 |
| `COMPOSE_FAILED` | Draft.js 没接受输入 | 重试 |
| `SEND_FAILED` / `SEND_UNCONFIRMED` | 发送失败 / 没确认到气泡 | **不要自动重试**，人工核实 |
| `NOT_GROUP` | 目标不是群 | 检查群名 |
| `SEND_BLOCKED` | 总闸未开 | 需要 `DOUYIN_IM_ALLOW_SEND=1` |
| `TARGET_NOT_ALLOWED` | 不在白名单 | 修改 `DOUYIN_IM_ALLOW_TO` |
| `BUSY` | 已有任务在跑 | 稍后再试（全局单实例） |
| `TIMEOUT` | 超过 `DOUYIN_IM_TIMEOUT` | 页面慢或被风控 |
| `NO_BROWSER` | 找不到浏览器 | 装 Chromium 或设 `DOUYIN_BROWSER` |

## 环境变量

见 [`.env.example`](../.env.example)。与 HTTP 服务相关的：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DOUYIN_HTTP_HOST` | `127.0.0.1` | 监听地址 |
| `DOUYIN_HTTP_PORT` | `7709` | 监听端口 |
| `DOUYIN_DATA_DIR` | `<repo>/data` | 数据目录 |
| `DOUYIN_MEDIA_DIR` | `<DATA>/media` | 成片 / 帧缓存 |
| `DOUYIN_CACHE_LIMIT_MB` | `800` | 缓存上限 |
| `DOUYIN_IM_TIMEOUT` | `180` | 单次私信操作超时 |
| `DOUYIN_IM_LOCK_WAIT` | `5` | 排队等锁上限 |
| `DOUYIN_IM_ALLOW_SEND` | 未设置 | `1` 才允许真实发送 |
| `DOUYIN_IM_ALLOW_TO` | 未设置 | 逗号分隔的发送白名单 |
