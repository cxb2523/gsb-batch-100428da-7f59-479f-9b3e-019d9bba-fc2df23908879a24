# xzqh-crawler

从民政部[「全国行政区划信息查询平台」](https://dmfw.mca.gov.cn/XzqhVersionPublish.html)接口抓取中国 1-4 级行政区划代码和地址基础数据，并写入 SQLite。

可直接用作行政区划库、地址库基础表，适合省市区街道级联地址选择、地址补全、地址标准化前置字典和地理数据清洗。

当前实现是纯异步批处理工具：

- HTTP：`aiohttp`
- 数据库：`aiosqlite`
- 并发模型：`asyncio.Queue + 固定 worker + 单 writer`
- 终端输出：普通日志 + 最终摘要

## 运行要求

- Python 3.12+
- 推荐使用 [uv](https://github.com/astral-sh/uv)

## 安装

```bash
git clone https://github.com/ByteColtX/xzqh-crawler.git
cd xzqh-crawler
uv sync
```

## 快速开始

直接抓取并写入默认数据库 `./data/xzqh.db`：

```bash
uv run xzqh crawl
```

指定数据库路径：

```bash
uv run xzqh crawl --database ./data/custom.db
```

只补抓失败或未完成任务：

```bash
uv run xzqh resume --database ./data/xzqh.db
```

调大并发和超时：

```bash
uv run xzqh crawl --database ./data/xzqh.db --concurrency 4 --timeout 60 --request-interval 0.25
```

查看帮助：

```bash
uv run xzqh --help
```

## 命令行参数

常用参数只有这几个：

- `xzqh crawl`：完整抓取 1-4 级数据
- `xzqh resume`：重试 `pending/failed` 任务
- `xzqh status`：查看数据和任务统计
- `--database PATH`：SQLite 文件路径，默认 `./data/xzqh.db`
- `-c, --concurrency N`：最大并发抓取数，默认 6
- `-t, --timeout SECONDS`：单请求超时时间，默认 45
- `--retry-attempts N`：单请求最大尝试次数，默认 5
- `--retry-base-delay SECONDS`：指数退避基础延迟，默认 2
- `--retry-max-delay SECONDS`：最大退避延迟，默认 60
- `--request-interval SECONDS`：请求之间的最小间隔，默认 0.2
- `--lease-seconds SECONDS`：任务租约时长，默认 600（用于中断续跑，见下）
- `--log-level LEVEL`：`DEBUG/INFO/WARNING/ERROR`
- `--log-file PATH`：写入日志文件
- `--format text|json`：选择最终结果格式
- `-q, --quiet`：不输出控制台日志
- `-v, --verbose`：启用 DEBUG 日志

日志写入 stderr，最终摘要或 JSON 写入 stdout，便于管道处理。遇到大量 502/503 时，优先降低 `--concurrency`、增大 `--request-interval`，然后使用 `xzqh resume`。

## 数据表

- `divisions`
  - `code, name, level, kind, parent_code, source_version, fetched_at`
- `crawl_runs`
  - `id, mode, source_version, status, started_at, finished_at, stats_json`
- `crawl_tasks`
  - `run_id, parent_code, target_level, state, attempts, last_error, started_at, lease_until, updated_at`
- `division_rejections`
  - `run_id, raw_code, raw_payload, reason, created_at`

抓取运行、任务状态和被拒绝的脏数据分别记录，支持断点续跑、失败补抓和问题审计。

## 任务状态与断点续跑

`crawl_tasks.state` 的语义：

- `pending`：等待抓取
- `running`：已被某个执行者认领，`lease_until` 记录租约到期时间
- `succeeded`：请求成功。**上游返回 200 但 `data` 为空也记为成功**——这表示该父节点下确实没有乡级数据，不会写入任何乡级记录，也不会写入 `division_rejections`
- `failed`：请求真的失败（超时、重试耗尽的 5xx、非法 JSON 等），错误信息保留在 `last_error`

`resume` 只重试 `pending` / `failed` 任务；`succeeded` 任务（包括空结果）不会再被请求。任务开始抓取时从 `pending` 置为 `running` 并写入 `lease_until`。进程中途退出后，再次 `resume` 时：

- `running` 且租约已过期的任务会被重新纳入抓取；
- `running` 且租约仍有效的任务视为仍被其他执行者持有，不会被重复请求；
- 上一次运行的 `succeeded` 任务会继承到新运行中，便于计数。

运行结束时若仍有 `pending` / `running` 任务，该次运行记为失败，需再次 `resume` 收尾。租约时长用 `--lease-seconds` 调整。

## 数据快照与结构兼容性

可从 [GitHub Releases](https://github.com/ByteColtX/xzqh-crawler/releases) 下载压缩的 SQLite 数据快照及 SHA256 校验文件。

2026-10-04 起的快照采用上文列出的新结构：`divisions.type` 改为 `kind`，移除 `parent_name` 和 `name_path`，新增 `source_version`；`crawl_jobs` 改为按运行记录任务的 `crawl_runs` 和 `crawl_tasks`，另增 `division_rejections`。

当前程序不自动迁移旧版数据库。使用 2026-10-01 或更早的快照时，请保留旧文件，并将新版快照解压到新路径，或指定新路径重新抓取。依赖旧字段的查询需相应调整。

## 数据说明

统计用区划代码通常由 1～12 位数字构成，各位含义如下：

- 第 1～2 位：省级代码
- 第 3～4 位：地级代码
- 第 5～6 位：县级代码
- 第 7～9 位：乡级代码
- 第 10～12 位：村级代码

示例：

- 省级数据（L1）：广东（`44`）
- 地市级数据（L2）：广州市（`4401`）
- 区县级数据（L3）：越秀区（`440104`）
- 乡镇级数据（L4）：白云街道（`440104020`）
- 村 / 社区（L5）：本项目暂无

当前工具抓取并落库的范围是 L1-L4，不包含 L5。

## 数据约束

- 仅保存符合层级长度规则且 `code` 为纯数字的行政区划记录
- 被拒绝的记录会写入 `division_rejections`，保留代码和拒绝原因

这是为了规避上游偶发脏数据，同时保留问题审计信息。

## 测试

运行全部测试：

```bash
uv run --extra dev python -m pytest
```

真实网络集成测试默认关闭，只有显式设置环境变量后才执行：

```bash
XZQH_INTEGRATION=1 uv run --extra dev python -m pytest -m integration
```
