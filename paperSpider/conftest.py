# -*- coding: utf-8 -*-
"""pytest 公共夹具：保证以包方式导入 paperSpider，并提供内存版 ES/Redis 假实现。

验收不依赖真实百度学术或真实 ES/Redis 集群：
- :class:`FakeElasticsearch` 用 dict 模拟 ES 的 exists/get/create/index 行为，
  并完整复现 create 冲突（ConflictError）与文档不存在（NotFoundError）语义；
- :class:`FakeRedis` 模拟 incr 计数，可切换为故障模式验证降级。
"""
import json
import os

import pytest
from elasticsearch.exceptions import ConflictError, NotFoundError

from paperSpider.pipelines import ElasticsearchAdapter, ElasticsearchPipeline
from paperSpider.items import PaperspiderItem

_HERE = os.path.dirname(os.path.abspath(__file__))


class FakeIndices(object):
    def __init__(self, fail_analyze=False):
        self.fail_analyze = fail_analyze

    def analyze(self, **kwargs):
        if self.fail_analyze:
            raise RuntimeError("simulated analyze outage (ik analyzer unavailable)")
        # 极简分词：按空白切词，模拟 ES analyze 返回结构
        tokens = kwargs.get("body", "").split()
        return {"tokens": [{"token": t} for t in tokens]}


class FakeElasticsearch(object):
    """内存版 ES 客户端，仅实现流水线用到的接口。"""

    def __init__(self, fail_analyze=False, exploding_ids=None):
        self.docs = {}  # _id -> _source(dict)
        self.indices = FakeIndices(fail_analyze=fail_analyze)
        # 配置为“一写就抛异常”的 _id，用于验证单篇失败不影响整批
        self.exploding_ids = set(exploding_ids or [])
        self.create_calls = 0
        self.index_calls = 0

    def exists(self, index, doc_type, id):
        return id in self.docs

    def get(self, index, doc_type, id):
        if id not in self.docs:
            raise NotFoundError(404, "not_found", {"_id": id})
        return {"_index": index, "_type": doc_type, "_id": id, "_source": self.docs[id]}

    def create(self, index, doc_type, id, body):
        self.create_calls += 1
        if id in self.exploding_ids:
            raise RuntimeError("simulated es write outage")
        if id in self.docs:
            raise ConflictError(409, "document_already_exists", {"_id": id})
        self.docs[id] = dict(body)
        return {"result": "created", "_id": id}

    def index(self, index, doc_type, id, body):
        self.index_calls += 1
        if id in self.exploding_ids:
            raise RuntimeError("simulated es write outage")
        self.docs[id] = dict(body)
        return {"result": "updated", "_id": id}


class FakeRedis(object):
    def __init__(self, broken=False):
        self.counters = {}
        self.broken = broken

    def incr(self, key):
        if self.broken:
            raise RuntimeError("simulated redis outage")
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]


def make_item(**kwargs):
    """构造带默认空值的 PaperspiderItem，模拟蜘蛛解析后的产物。"""
    defaults = {
        "paper_title": "",
        "paper_writer": [],
        "paper_time": "",
        "paper_cite_count": 0,
        "paper_source": "",
        "paper_abstract": "",
        "paper_keywords": [],
        "paper_DOI": "",
        "paper_download_link": [],
    }
    defaults.update(kwargs)
    return PaperspiderItem(**defaults)


def make_pipeline(fail_analyze=False, exploding_ids=None, redis_broken=False):
    es = FakeElasticsearch(fail_analyze=fail_analyze, exploding_ids=exploding_ids)
    redis_cli = FakeRedis(broken=redis_broken)
    pipeline = ElasticsearchPipeline(
        es_adapter=ElasticsearchAdapter(es), redis_client=redis_cli
    )
    return pipeline, es, redis_cli


@pytest.fixture
def pipeline_factory():
    return make_pipeline


@pytest.fixture
def sample_items():
    """从样例数据文件加载“解析后”的论文条目。"""
    path = os.path.join(_HERE, "tests", "fixtures", "sample_items.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
