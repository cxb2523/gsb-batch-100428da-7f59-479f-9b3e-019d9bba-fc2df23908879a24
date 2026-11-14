# 论文文档标识与安全入库改造说明

## 1. 背景与问题

原实现（`paperSpider/pipelines.py`）直接把详情页解析出的 `paper_source` 当作
Elasticsearch 文档主键（`paper.meta.id = item['paper_source']`），并把 item 字段
原样写入 ES：

1. `paper_source` 可能为空：`_id` 为空导致整条写入失败；
2. `paper_source` 可能重复（不同论文详情页带有相同来源链接）：后写入的论文直接
   覆盖先写入的论文；
3. `paper_cite_count`、`paper_time` 是 ES `Integer` 字段，但解析结果可能是
   `"暂无"`、`""`、`"被引 12 次"` 等字符串，空值/非法值写入整型字段会触发
   映射报错；
4. 单篇文档写入抛异常会中断整个批次的处理。

## 2. 文档标识生成规则（`paperSpider/utils/paper_id.py`）

标识完全由论文的稳定属性确定性派生（无随机因子），统一加 `paper_` 前缀，与历史
上直接以 `paper_source` 为 `_id` 的文档隔离命名空间。优先级如下：

| 优先级 | 可用身份字段 | 标识规则 | 示例 |
| --- | --- | --- | --- |
| 1 | DOI | `paper_doi_<sha1(规范化 DOI)>` | `paper_doi_f6dffbf1...` |
| 2 | 标题（+作者+年份） | `paper_title_<sha1(规范化标题 \| 作者 \| 年份)>` | `paper_title_4a7eb94b...` |
| 3 | 仅来源 URL | `paper_src_<sha1(来源 URL + 全字段指纹)>` | `paper_src_1e3900cb...` |
| 4 | 三者全空 | 返回 `None`，流水线丢弃该 item（不生成随机主键） | — |

规范化要点：

- **DOI**：剥离 `doi:`、`https://doi.org/`、`http://dx.doi.org/` 等前缀，用正则
  `10.\d{4,9}/\S+` 提取主体并统一转小写（DOI 大小写不敏感）。因此
  `doi:10.1000/X.Y` 与 `https://doi.org/10.1000/x.y` 生成同一标识；
- **标题/文本**：压缩连续空白、去首尾空白，标题参与哈希时忽略大小写；
- **作者**：兼容列表与逗号/分号分隔字符串，排序结构保持原顺序参与哈希；
- **年份**：经 `safe_int` 转换后参与，无法解析则留空；
- **全字段指纹**：标题、作者、年份、来源、DOI、摘要、关键词、下载链接按固定顺序
  拼接，用于来源重复时区分不同论文，以及冲突消歧。引用量等易变字段不参与。

### 稳定性与唯一性保证

- **同一论文多次处理 → 相同标识**：标识只依赖稳定身份字段。重复抓取时即便引用量
  变化，标识不变，流水线识别为同一文档并执行更新，不会新增；
- **不同论文 → 不同标识**：
  - 有 DOI 时 DOI 全球唯一；
  - 无 DOI 时标题键包含作者与年份，同名不同论文可区分；
  - 仅来源时全字段指纹参与哈希，来源 URL 重复也不会撞标识；
  - SHA-1 碰撞概率可忽略，且写入层还有二次防护（见下）。

## 3. 入库保护流程（`ElasticsearchPipeline`）

1. `generate_paper_id(item)` 生成标识；无身份字段则 `DropItem` 跳过；
2. `safe_int` 转换 `paper_cite_count`/`paper_time`，无法解析则**省略字段**
   （按缺失处理），文本字段空值同样省略，杜绝非法值写入；
3. 写入走 `op_type=create`（`ElasticsearchAdapter.create`），ES 协议层面保证
   不会覆盖已存在文档：
   - `_id` 不存在 → 新建；
   - `_id` 已存在且判定为**同一篇论文**（DOI 相同 / 标题相同 / 全字段指纹相同）
     → 覆盖更新同一文档（重复抓取场景）；
   - `_id` 已存在但是**另一篇论文**（历史脏数据或哈希冲突）→ 用
     `disambiguate_id` 派生 `<原标识>fb_<sha1(全字段指纹)>` 兜底标识新建，
     兜底标识同样是确定性的，重复处理仍更新同一文档；
   - 并发下 `exists` 与 `create` 之间被抢占（`ConflictError`）→ 同样走消歧重试；
4. 单篇 item 的任何异常只记录日志并跳过该篇，批次继续；
5. Redis 计数、ES `analyze`（ik 分词）故障均降级处理，不影响文档落库；
   `analyze` 不可用时本地用英文分词 + 中文整句兜底生成 `suggest`。

连接全部惰性创建且可注入，测试使用内存假 ES/假 Redis，无需真实集群。

## 4. 整型安全转换（`safe_int`）

| 输入 | 输出 |
| --- | --- |
| `12`、`"12"`、`"被引 12 次"`、`"1,234"`、`3.0` | 对应整数 |
| `None`、`""`、`"暂无"`、`"abc"`、`NaN`/`Inf`、`True/False`、`[]` | `None`（缺失，字段省略） |

## 5. 测试

```bash
cd paperSpider
python -m pytest tests
```

- `tests/test_paper_id.py`：标识规范化、稳定性、唯一性、消歧；
- `tests/test_safe_int.py`：整型转换的合法/非法输入；
- `tests/test_pipeline.py`：端到端流水线（内存 ES/Redis），覆盖来源为空/重复、
  重复抓取更新、历史脏数据不被覆盖、并发冲突、单篇失败不中断整批、
  Redis/analyze 故障降级、样例数据驱动；
- `tests/fixtures/sample_items.json`：模拟蜘蛛解析后的样例数据。

共 61 个用例，全部通过，且不依赖真实百度学术或真实 ES/Redis。
