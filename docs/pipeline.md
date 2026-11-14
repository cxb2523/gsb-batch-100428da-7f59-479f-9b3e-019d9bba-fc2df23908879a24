# 每日画布流水线

从多个上游源采集条目，去重、排序后渲染为静态页面（HTML）与接口数据（JSON）。

## 运行

```bash
python -m dailycanvas --config config/sources.json --output output/
# 指定期号（默认当天）：
python -m dailycanvas --config config/sources.json --output output/ --edition 2026-11-14
```

产物：`output/index.html`（页面，含期号、条目标题与来源）和 `output/daily.json`（同样的条目列表 + 本期元信息，含每个源的采集结局）。

## 源清单（config/sources.json）

```json
{
  "max_items": 50,
  "timeout_seconds": 10,
  "retries": 2,
  "sources": [
    {"name": "alpha", "url": "https://example.com/feed.json", "weight": 1.0, "enabled": true}
  ]
}
```

- `url` 支持 `http(s)://`、`file://`，以及相对配置文件目录的本地路径（便于样例与测试）。
- 上游载荷格式：`{"items": [{"id", "title", "url", "published"}, ...]}`，`published` 为 ISO 日期或日期时间。

## 行为约定

- **采集**：并发抓取；每个源记录状态、成功条数、失败原因、耗时、尝试次数（1 次首发 + `retries` 次重试）。缺字段/日期非法的脏条目只丢弃并计数，不拖垮该源。
- **降级**：单源失败只剔除该源，其余源照常合成；全部源失败（或没有任何有效条目）时以非零码退出并写明原因，不发布空刊。
- **合成**：按条目 `id` 去重，保留 `published` 较新一条；按“新鲜度降序、来源权重降序、id 升序”排序；条数上限 `max_items`。
- **确定性**：采集结局按源名排序、排序键含 id tie-break，同一天重复运行产物字节一致，与并发完成顺序无关。

## 测试

```bash
python -m unittest discover -s tests -v
```

全部使用本地临时文件源，不访问外网。覆盖：正常出刊、脏条目丢弃、单源失败降级、非法 JSON 降级、全源失败报错、无启用源报错、跨源重复去重留新、同日重跑确定性、条数上限。
