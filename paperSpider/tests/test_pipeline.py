# -*- coding: utf-8 -*-
"""ElasticsearchPipeline 端到端测试（内存假 ES/Redis，无真实集群）。

覆盖验收点：
1. paper_source 为空/重复时论文仍逐篇落库且互不覆盖；
2. 重复抓取同一论文更新同一文档而非新增；
3. 整型字段非法值按缺失处理，不产生非法写入；
4. 单篇坏文档/外部故障不中断整批；
5. analyze/Redis 故障下降级运行。
"""
import pytest
from scrapy.exceptions import DropItem

from paperSpider.utils.paper_id import generate_paper_id
from conftest import make_item


class TestBasicWrite:
    def test_item_with_empty_source_is_stored(self, pipeline_factory):
        pipeline, es, redis_cli = pipeline_factory()
        item = make_item(paper_title="Attention Is All You Need", paper_source="")
        pipeline.process_item(item, spider=None)

        doc_id = generate_paper_id(item)
        assert doc_id in es.docs
        assert es.docs[doc_id]["paper_title"] == "Attention Is All You Need"
        # 空 source 不应写入字段
        assert "paper_source" not in es.docs[doc_id]
        assert redis_cli.counters.get("baidu_count") == 1

    def test_duplicate_source_does_not_overwrite_other_paper(self, pipeline_factory):
        pipeline, es, _ = pipeline_factory()
        common_source = "https://example.com/shared-source-url"
        paper_a = make_item(paper_title="Paper A", paper_source=common_source,
                            paper_DOI="10.1000/a", paper_abstract="alpha")
        paper_b = make_item(paper_title="Paper B", paper_source=common_source,
                            paper_DOI="10.1000/b", paper_abstract="beta")

        pipeline.process_item(paper_a, None)
        pipeline.process_item(paper_b, None)

        stored = list(es.docs.values())
        assert len(stored) == 2  # 两篇都落库，没有互相覆盖
        titles = {d["paper_title"] for d in stored}
        assert titles == {"Paper A", "Paper B"}
        assert es.create_calls >= 2

    def test_integer_fields_safe_conversion(self, pipeline_factory):
        pipeline, es, _ = pipeline_factory()
        item = make_item(
            paper_title="T",
            paper_cite_count="被引 189,521 次",
            paper_time="2016",
        )
        pipeline.process_item(item, None)
        doc = es.docs[generate_paper_id(item)]
        assert doc["paper_cite_count"] == 189521
        assert doc["paper_time"] == 2016
        assert isinstance(doc["paper_cite_count"], int)

    def test_invalid_integer_fields_are_omitted_not_written(self, pipeline_factory):
        pipeline, es, _ = pipeline_factory()
        item = make_item(
            paper_title="T",
            paper_cite_count="暂无",
            paper_time="",
        )
        pipeline.process_item(item, None)
        doc = es.docs[generate_paper_id(item)]
        assert "paper_cite_count" not in doc
        assert "paper_time" not in doc

    def test_empty_text_fields_omitted(self, pipeline_factory):
        pipeline, es, _ = pipeline_factory()
        item = make_item(paper_title="Only Title")
        pipeline.process_item(item, None)
        doc = es.docs[generate_paper_id(item)]
        assert doc["paper_title"] == "Only Title"
        for empty_field in ("paper_abstract", "paper_DOI", "paper_source"):
            assert empty_field not in doc

    def test_unidentifiable_item_dropped_not_crash(self, pipeline_factory):
        pipeline, es, redis_cli = pipeline_factory()
        item = make_item(paper_abstract="没有标题/DOI/来源")
        # process_item 内部捕获 DropItem 并记录，不应抛出，也不应写库
        pipeline.process_item(item, None)
        assert es.docs == {}
        assert redis_cli.counters == {}


class TestDuplicateCrawlUpdatesSameDoc:
    def test_recrawl_same_paper_updates_instead_of_insert(self, pipeline_factory):
        pipeline, es, _ = pipeline_factory()
        first = make_item(
            paper_title="Deep Residual Learning",
            paper_writer=["Kaiming He"],
            paper_time="2016",
            paper_cite_count="100",
            paper_DOI="10.1109/CVPR.2016.90",
        )
        pipeline.process_item(first, None)
        doc_id = generate_paper_id(first)
        assert len(es.docs) == 1
        assert es.docs[doc_id]["paper_cite_count"] == 100

        # 重复抓取：同一篇论文，引用量上涨
        recrawl = make_item(
            paper_title="Deep Residual Learning",
            paper_writer=["Kaiming He"],
            paper_time="2016",
            paper_cite_count="250",
            paper_DOI="10.1109/CVPR.2016.90",
        )
        assert generate_paper_id(recrawl) == doc_id
        pipeline.process_item(recrawl, None)

        assert len(es.docs) == 1  # 没有新增文档
        assert es.docs[doc_id]["paper_cite_count"] == 250  # 内容被更新
        assert es.index_calls == 1  # 走的是更新路径
        assert es.create_calls == 1


class TestLegacyAndCollisionProtection:
    def test_legacy_source_id_owned_by_other_paper_not_overwritten(self, pipeline_factory):
        pipeline, es, _ = pipeline_factory()
        item = make_item(paper_title="New Paper", paper_source="https://example.com/legacy.pdf",
                         paper_abstract="different content")
        incoming_id = generate_paper_id(item)
        # 模拟历史脏数据：新论文将要使用的派生 _id 已被另一篇论文占用
        es.docs[incoming_id] = {"paper_title": "Legacy Other Paper", "paper_abstract": "old"}

        pipeline.process_item(item, None)

        # 历史文档原样保留，没有被新论文覆盖
        assert es.docs[incoming_id]["paper_title"] == "Legacy Other Paper"
        # 新论文以消歧标识另存为独立文档
        remaining = {k: v for k, v in es.docs.items() if k != incoming_id}
        assert len(remaining) == 1
        assert list(remaining.values())[0]["paper_title"] == "New Paper"

    def test_legacy_id_owned_by_same_paper_updates(self, pipeline_factory):
        pipeline, es, _ = pipeline_factory()
        item = make_item(paper_title="Same Paper", paper_source="https://example.com/legacy.pdf",
                         paper_cite_count="42")
        incoming_id = generate_paper_id(item)
        # 同一篇论文的历史文档（引用量为旧值）占用了该 _id
        es.docs[incoming_id] = {
            "paper_title": "Same Paper",
            "paper_source": "https://example.com/legacy.pdf",
            "paper_cite_count": 10,
        }
        pipeline.process_item(item, None)

        # 同一篇论文：更新历史文档本身，不新增
        assert len(es.docs) == 1
        assert es.docs[incoming_id]["paper_cite_count"] == 42


class TestConcurrentAndCompatibility:
    def test_create_conflict_race_is_disambiguated(self, pipeline_factory):
        """exists() 与 create() 之间被其他进程抢先写入 => ConflictError 路径。"""
        from elasticsearch.exceptions import ConflictError
        pipeline, es, _ = pipeline_factory()
        item = make_item(paper_title="Racy Paper", paper_abstract="mine")

        original_create = es.create

        def racing_create(index, doc_type, id, body):
            # 模拟并发：第一次 create 前另一进程已占用该 _id
            if not getattr(racing_create, "fired", False):
                racing_create.fired = True
                es.docs[id] = {"paper_title": "Other Process Paper", "paper_abstract": "theirs"}
                raise ConflictError(409, "version_conflict", {"_id": id})
            return original_create(index, doc_type, id, body)

        es.create = racing_create
        pipeline.process_item(item, None)

        # 两篇论文都在，新论文走消歧标识，没有覆盖并发写入的文档
        titles = {d["paper_title"] for d in es.docs.values()}
        assert titles == {"Other Process Paper", "Racy Paper"}

    def test_plain_dict_items_are_supported(self, pipeline_factory):
        """验收允许用普通 dict 驱动流水线（不依赖 scrapy.Item）。"""
        pipeline, es, _ = pipeline_factory()
        item = {
            "paper_title": "Dict Driven Paper",
            "paper_writer": ["Alice"],
            "paper_time": "2020",
            "paper_cite_count": "被引 7 次",
            "paper_source": "",
            "paper_abstract": "",
            "paper_keywords": [],
            "paper_DOI": "10.1234/dict.2020",
            "paper_download_link": [],
        }
        pipeline.process_item(item, None)
        doc_id = generate_paper_id(item)
        assert doc_id in es.docs
        assert es.docs[doc_id]["paper_cite_count"] == 7
        assert es.docs[doc_id]["paper_time"] == 2020

    def test_from_crawler_builds_pipeline_without_live_connections(self):
        from paperSpider.pipelines import ElasticsearchPipeline

        class FakeSettings:
            def get(self, key, default=None):
                return default

        class FakeCrawler:
            settings = FakeSettings()

        pipe = ElasticsearchPipeline.from_crawler(FakeCrawler())
        assert pipe.counter_key == "baidu_count"
        assert pipe._adapter is None  # 连接惰性创建，构造期不触网


class TestBatchResilience:
    def test_one_bad_document_does_not_stop_batch(self, pipeline_factory, sample_items):
        # 让第一篇写入时抛异常，模拟 ES 单文档故障
        first_id = generate_paper_id(make_item(**sample_items[0]))
        pipeline, es, redis_cli = pipeline_factory(exploding_ids={first_id})

        from paperSpider.items import PaperspiderItem
        for raw in sample_items:
            pipeline.process_item(PaperspiderItem(**raw), None)

        # 第一篇失败，其余合法论文仍全部落库（第 4 条无身份字段被跳过）
        stored_titles = {d.get("paper_title") for d in es.docs.values()}
        assert "Deep Residual Learning for Image Recognition" not in stored_titles
        assert "Attention Is All You Need" in stored_titles
        assert "Batch Normalization: Accelerating Deep Network Training by Reducing Internal Covariate Shift" in stored_titles
        # 成功的文档计数仍递增
        assert redis_cli.counters.get("baidu_count") == 2

    def test_redis_outage_does_not_block_es_write(self, pipeline_factory):
        pipeline, es, redis_cli = pipeline_factory(redis_broken=True)
        item = make_item(paper_title="T", paper_DOI="10.1000/x")
        pipeline.process_item(item, None)  # 不抛异常
        assert generate_paper_id(item) in es.docs
        assert redis_cli.counters == {}

    def test_analyze_outage_uses_local_fallback(self, pipeline_factory):
        pipeline, es, _ = pipeline_factory(fail_analyze=True)
        item = make_item(
            paper_title="Deep Learning Survey 深度学习综述",
            paper_keywords=["neural network"],
            paper_abstract="abstract text",
        )
        pipeline.process_item(item, None)
        doc = es.docs[generate_paper_id(item)]
        # 建议字段仍被填充（本地兜底分词），文档正常落库
        assert isinstance(doc["suggest"], list) and doc["suggest"]
        all_inputs = []
        for part in doc["suggest"]:
            all_inputs.extend(part["input"])
        assert "deep" in all_inputs  # 英文单词被本地切出
        assert "Deep Learning Survey 深度学习综述" in all_inputs  # 中文整句保留


class TestSampleDataDriven:
    def test_full_sample_pipeline(self, pipeline_factory, sample_items):
        pipeline, es, _ = pipeline_factory()
        from paperSpider.items import PaperspiderItem
        seen_ids = set()
        for raw in sample_items:
            item = PaperspiderItem(**raw)
            doc_id = generate_paper_id(item)
            if doc_id:
                seen_ids.add(doc_id)
            pipeline.process_item(item, None)

        # 3 条可识别论文全部有独立文档，且标识互不相同
        assert len(seen_ids) == 3
        assert len(es.docs) == 3
        # 样例 1：字符串引用量/年份被安全转成整型
        residual = next(d for d in es.docs.values()
                        if d["paper_title"].startswith("Deep Residual"))
        assert residual["paper_cite_count"] == 189521
        assert residual["paper_time"] == 2016
        # 样例 2：来源为空也能落库，非法引用量/缺失年份被省略
        attention = next(d for d in es.docs.values()
                         if d["paper_title"] == "Attention Is All You Need")
        assert "paper_source" not in attention
        assert "paper_cite_count" not in attention
