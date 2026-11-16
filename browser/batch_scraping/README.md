# AgentInBrowser 批处理采集层

在 AgentInBrowser HTTP 接口（`/execute` + find/click/send_keys/...）之上封装的一层
**可复用批处理能力**。给定一组有序步骤，逐条结算：成功、失败、跳过各归其位；
单条失败不中断整批；索引失效时自动重新查找并重试一次；窗口切换/关闭时正确处理
元素缓存失效；最终输出可核对的结算报告，并保留所有已成功采集的文本。

本目录完全自包含，**不依赖真实站点、不需要 Chrome**：内置一个按 AgentInBrowser
线协议实现的本地 mock 服务器和自造页面，所有失败场景均可控、可重复。

## 目录结构

```
batch_scraping/
├── aib_batch/                 # 可复用库（仅用 Python 标准库）
│   ├── client.py              #   AgentInBrowser REST 客户端
│   ├── runner.py              #   批处理执行器：重试、跳过、结算
│   └── report.py              #   文本报告 + JSON 导出
├── mock_server/               # 本地 mock AgentInBrowser 服务器（wsgi，stdlib）
│   ├── dom.py                 #   极简 HTML 解析 + CSS 选择器 + 元素代际管理
│   ├── browser.py             #   浏览器状态机：窗口、元素缓存、stale 注入
│   ├── app.py                 #   /status /init /execute /quit /shutdown /inject
│   └── serve.py               #   独立启动入口
├── sites/pages/               # 自造页面（index/detail/list/popup/failure）
├── tests/
│   ├── server_harness.py      # 随机端口的进程内 HTTP 测试夹具
│   └── test_batch_runner.py   # 15 个端到端测试（真实 HTTP 回路）
├── demo.py                    # 混合场景演示（恢复/硬失败/跳过/弹窗）
└── demo_report.json           # demo 运行后产出的机器可读结算结果
```

## 快速开始

```bash
# 全部用 Python 标准库，支持安全隔离模式 -I
python -I tests/test_batch_runner.py   # 运行 15 个测试
python -I demo.py                      # 运行端到端演示
python -I mock_server/serve.py --port 5050   # 单独启动 mock 服务器
```

对**真实** AgentInBrowser 服务器使用时，只需把 `AIBClient(base_url)` 指向
`http://127.0.0.1:5000`，步骤 DSL 与结算逻辑完全一致：

```python
from aib_batch import AIBClient, BatchRunner, format_report

client = AIBClient("http://127.0.0.1:5000")
client.init("https://example.com")
runner = BatchRunner(client, max_retries=1)
report = runner.run_batch([
    {"op": "find", "selector": "button.open", "name": "find-open"},
    {"op": "click", "index": 0, "name": "open"},
    {"op": "get_text", "store": "body_text"},
])
print(format_report(report))
```

## 步骤 DSL

| op | 参数 | 说明 |
|----|------|------|
| `find` | `selector`（CSS），可选 `store` | 查找元素，刷新服务器元素缓存 |
| `inputs` / `buttons` | 可选 `store` | 内置查找器，同样刷新缓存 |
| `click` | `index`，可选 `name` | 按最近一次查找结果的索引点击 |
| `send_keys` | `index`, `text`（支持 `${var}`） | 先 clear 再输入 |
| `get_html` | `index`，可选 `store` | 取元素 innerHTML |
| `get_text` | 可选 `store` | 取整页文本 |
| `page_info` / `sleep` | `seconds` | 页面信息 / 等待 |
| `execute_script` | `script`，可选 `store` | 执行 JS |
| `switch_window` | `index`（`-1` = 最新窗口） | 切换窗口，**清空已记忆的查找器** |
| `close_window` | — | 关闭当前窗口并回到主窗口，**清空查找器** |

通用字段：`name`（命名，便于依赖引用与报告核对）、`depends_on`（显式依赖，
可传 name 或序号，可传列表）、`store`（把结果存入结果上下文，供后续 `${var}` 引用）。

## 结算语义（对应需求逐条实现）

1. **逐条结算，失败不中断。** 每个步骤记录 `success` / `failed` / `skipped`、
   尝试次数、耗时、错误信息与归类原因；任何异常都被捕获，后续独立步骤继续执行。
2. **依赖失败 → 跳过，而不是误报成功。** 依赖有三个来源：
   - 索引类步骤（click/send_keys/get_html）隐式依赖当前窗口最近一次查找；
   - `${var}` 占位符隐式依赖产出该变量的步骤；
   - 显式 `depends_on`。
   任一依赖未成功，本步骤标记 `skipped` 并记录 `skipped_due_to` 的序号。
3. **索引失效只重试一次。** 索引类步骤收到可恢复错误（stale、index、缓存为空、
   element not found 等）且当前窗口存在可重放的查找器时：重新执行该查找器一次，
   再重试操作一次。重试仍失败（包括重查找返回 0 个元素）才记为硬失败，
   报告里 `retried: true` 并保留两段错误信息。
4. **窗口切换单独处理。** `switch_window` / `close_window` 后立即丢弃记忆中的
   查找器，强制后续索引步骤重新查找；`close_window` 由服务器焦点回到主窗口，
   剩余步骤在主窗口继续。
5. **结果不丢失。** 所有 `store` 成功采集的文本/HTML 进入 `results`，
   与步骤级记录一起保留，个别失败不会丢弃整批数据。

## 结算报告

`run_batch()` 返回的结构：

```json
{
  "total": 12,
  "summary": {"success": 10, "failed": 1, "skipped": 1},
  "failure_reasons": [
    {"reason": "element-not-found", "count": 1, "seqs": [9], "sample_error": "..."}
  ],
  "results": {"detail_text": "...", "first_record": "<article ...>...</article>"},
  "steps": [
    {"seq": 7, "name": "record-html", "op": "get_html", "status": "success",
     "attempts": 2, "retried": true, "reason": null, "error": null,
     "skipped_due_to": [], "data": {...}, "captured": "...", "duration_ms": 30}
  ]
}
```

`format_report()` 渲染人工核对用的纯文本（计数、逐条状态、失败原因摘要、
采集数据预览）；`write_json_report()` 导出机器可读 JSON。

## Mock 服务器如何还原真实陷阱

- 每个窗口独立持有 DOM 与元素缓存；`find/inputs/buttons` 每次都**替换**缓存，
  与真实服务一致。
- DOM 变动（按钮的 `data-action` 触发导航、删除/新增节点、注入刷新）会递增
  *generation*，旧缓存代际不匹配即返回 `stale element reference`，模拟真实
  Selenium 的陈旧索引。
- `POST /inject {"kind": "stale"}` 是测试钩子：让下一条索引命令先遇到一次
  页面刷新，用于确定性地验证“重查找 + 一次重试”的恢复路径。
- 弹窗由页面按钮 `data-action="open-popup"` 打开，`switch_window -1` 切入，
  `close_window` 关闭后焦点回到第一个窗口。

## 验证覆盖（tests/test_batch_runner.py，15 项）

- 完整 happy path：表单输入 → 点击导航 → 取文本 → 查找记录
- 单条失败后独立步骤照常成功，前后采集文本均保留
- 依赖链失败 → 后续步骤 skipped（含显式 depends_on 与隐式索引依赖）
- 未定义 `${var}` 记为 missing-variable，依赖步骤跳过
- 注入 stale：一次重查找 + 一次重试后成功（attempts=2, retried=true）
- 连续 stale / 重查找返回 0 个元素：硬失败且原因分类正确
- 可变列表页真实 DOM 删除：第二次点击靠自动重查找恢复
- 无缓存索引越界：干净失败、不触发无意义重试
- 切窗口后旧缓存失效必须重新查找；关窗后回主窗口继续执行
- 弹窗内失败不影响关窗后主窗口的数据采集
- 报告计数、原因摘要、数据保留、JSON 往返序列化
