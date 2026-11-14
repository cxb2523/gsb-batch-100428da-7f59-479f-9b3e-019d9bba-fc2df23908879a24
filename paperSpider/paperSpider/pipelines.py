# -*- coding: utf-8 -*-

# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html
import logging
import re

import redis
from elasticsearch.exceptions import ConflictError, NotFoundError
from scrapy.exceptions import DropItem

from paperSpider.models.es_types import PaperType, PAPER_INDEX, PAPER_DOC_TYPE
from paperSpider.utils.paper_id import (
    build_identity,
    disambiguate_id,
    generate_paper_id,
    normalize_text,
    safe_int,
)

logger = logging.getLogger(__name__)

# 整型字段：无法解析时按缺失处理（不写入该字段），而不是塞非法值导致索引报错
_INTEGER_FIELDS = ("paper_cite_count", "paper_time")
# 文本/列表字段：仅在有有效值时写入
_TEXT_FIELDS = (
    "paper_title",
    "paper_writer",
    "paper_abstract",
    "paper_keywords",
    "paper_DOI",
    "paper_source",
    "paper_download_link",
)
# 同一来源标识冲突时的最大消歧次数（正常一次即可，上限仅用于防御）
_MAX_DISAMBIGUATE = 3


def _get_es_connection():
    """惰性创建 ES 连接，避免 import 阶段即绑定连接、提升可测试性。"""
    from elasticsearch_dsl.connections import connections
    return connections.get_connection(PaperType._index._using)


def gen_suggests(es_client, index, info_tuple):
    """根据字符串生成搜索建议数组。

    ES analyze 接口不可用（离线/测试环境、ik 分词器缺失等）时退化为本地
    分词兜底，保证建议字段不会因为外部依赖故障而阻断整条写入链路。
    """
    used_words = set()
    suggests = []
    for text, weight in info_tuple:
        new_words = set()
        text = normalize_text(text)
        if text:
            try:
                words = es_client.indices.analyze(
                    index=index,
                    analyzer="ik_smart",
                    params={'filter': ["lowercase"]},
                    body=text,
                )
                new_words = {r["token"] for r in words["tokens"] if len(r["token"]) > 1}
            except Exception as exc:  #  analyze 失败不应影响文档落库
                logger.warning("ES analyze 失败，使用本地分词兜底: %s", exc)
                # 本地兜底：英文/数字单词 + 长度大于 1 的连续片段
                new_words = {w.lower() for w in re.findall(r"[A-Za-z0-9]{2,}", text)}
                # 含中文等非 ASCII 文本时，整体作为一个建议词保留
                if any(ord(ch) > 127 for ch in text):
                    new_words.add(text)
            new_words = {w for w in new_words if w} - used_words

        if new_words:
            suggests.append({"input": sorted(new_words), "weight": weight})
            used_words.update(new_words)

    return suggests


class ElasticsearchAdapter(object):
    """ES 读写操作的薄封装，集中索引名/类型名并便于在测试中替换为内存实现。"""

    def __init__(self, es_client, index=PAPER_INDEX, doc_type=PAPER_DOC_TYPE):
        self.es = es_client
        self.index = index
        self.doc_type = doc_type

    def exists(self, doc_id):
        return bool(self.es.exists(index=self.index, doc_type=self.doc_type, id=doc_id))

    def get(self, doc_id):
        try:
            return self.es.get(index=self.index, doc_type=self.doc_type, id=doc_id)
        except NotFoundError:
            return None

    def create(self, doc_id, body):
        """op_type=create：仅当 _id 不存在时写入，存在则抛 ConflictError，
        从协议层面保证不会覆盖已有论文。"""
        return self.es.create(
            index=self.index, doc_type=self.doc_type, id=doc_id, body=body
        )

    def index_doc(self, doc_id, body):
        """整文档覆盖写，仅用于“已确认是同一篇论文”的更新场景。

        方法名避开 ``self.index``（索引名字符串）属性，防止命名冲突。
        """
        return self.es.index(
            index=self.index, doc_type=self.doc_type, id=doc_id, body=body
        )


class PaperspiderPipeline(object):
    def process_item(self, item, spider):
        return item


class ElasticsearchPipeline(object):
    """把论文 item 安全写入 Elasticsearch。

    关键保证：
    1. 文档 ``_id`` 由 :func:`generate_paper_id` 基于 DOI/标题/来源等稳定属性
       派生，不再直接使用可能为空或重复的 ``paper_source``；
    2. 新文档一律走 ``op_type=create``，冲突时先判断是否同一篇论文：
       同一论文 => 更新同一文档；不同论文 => 派生消歧标识后新建，绝不覆盖；
    3. 整型字段经 :func:`safe_int` 转换，无法解析按缺失处理；
    4. 单篇文档的任何异常只跳过该篇并记录日志，不会中断整批处理；
    5. ES/Redis 连接均可注入，本地可用内存假实现驱动整条流水线。
    """

    def __init__(self, es_adapter=None, redis_client=None, counter_key="baidu_count"):
        self._adapter = es_adapter
        self._redis = redis_client
        self.counter_key = counter_key

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            redis_client=None,
            counter_key=crawler.settings.get("PAPER_REDIS_COUNTER_KEY", "baidu_count"),
        )

    @property
    def adapter(self):
        if self._adapter is None:
            self._adapter = ElasticsearchAdapter(_get_es_connection())
        return self._adapter

    @property
    def redis_client(self):
        if self._redis is None:
            self._redis = redis.StrictRedis(
                host="127.0.0.1", port=6379, db=0, socket_connect_timeout=2
            )
        return self._redis

    def process_item(self, item, spider):
        try:
            doc_id = generate_paper_id(item)
            if not doc_id:
                # 标题/DOI/来源全部缺失，无法构造稳定标识：跳过而不是生成随机主键
                raise DropItem("缺少标题、DOI、来源，无法生成稳定文档标识: %r" % dict(item))

            body = self._build_document(item)
            self._write_with_protection(doc_id, item, body)
            self._incr_counter()
        except DropItem as exc:
            # 预期内的丢弃：记录原因即可，不计为写入错误
            logger.warning("丢弃论文: %s", exc)
        except Exception as exc:
            # 单篇失败只影响自身，保证整批不中断
            logger.error("论文写入失败，已跳过: %s | item=%r", exc, dict(item), exc_info=True)
        return item

    def _build_document(self, item):
        """把 item 转为 ES 文档体，空值/非法值一律省略而不是写入空串或非法整型。"""
        body = {}
        for field in _TEXT_FIELDS:
            value = item.get(field)
            if isinstance(value, (list, tuple)):
                value = [normalize_text(v) for v in value]
                value = [v for v in value if v]
            else:
                value = normalize_text(value)
            if value:  # 空列表/空字符串不写入
                body[field] = value

        for field in _INTEGER_FIELDS:
            number = safe_int(item.get(field))
            if number is not None:  # 无法解析 => 缺失（省略字段）
                body[field] = number

        keywords = item.get("paper_keywords") or []
        if isinstance(keywords, (list, tuple)):
            keyword_text = " ".join(normalize_text(k) for k in keywords)
        else:
            keyword_text = normalize_text(keywords)
        body["suggest"] = gen_suggests(
            self.adapter.es,
            self.adapter.index,
            (
                (body.get("paper_title", ""), 10),
                (keyword_text, 5),
                (body.get("paper_abstract", ""), 2),
            ),
        )
        return body

    def _write_with_protection(self, doc_id, item, body):
        """create 优先、冲突消歧的受保护写入。"""
        candidate_id = doc_id
        for attempt in range(_MAX_DISAMBIGUATE + 1):
            try:
                if not self.adapter.exists(candidate_id):
                    self.adapter.create(candidate_id, body)
                    logger.info("新建论文文档 _id=%s", candidate_id)
                    return candidate_id

                existing = self.adapter.get(candidate_id)
                if self._is_same_paper(item, existing):
                    # 重复抓取同一篇论文 => 更新同一文档，而不是新增
                    self.adapter.index_doc(candidate_id, body)
                    logger.info("更新已存在论文文档 _id=%s", candidate_id)
                    return candidate_id

                # _id 已被另一篇论文占用（历史脏数据/哈希冲突）：派生消歧标识
                candidate_id = disambiguate_id(doc_id, item)
                logger.warning(
                    "文档标识 %s 已被其他论文占用，消歧为 %s（第 %d 次）",
                    doc_id, candidate_id, attempt + 1,
                )
            except ConflictError:
                # 并发场景下 exists 与 create 之间被其他进程抢先写入
                candidate_id = disambiguate_id(doc_id, item)
                logger.warning("create 冲突，消歧重试 _id=%s", candidate_id)

        raise RuntimeError("文档标识消歧次数超过上限，放弃写入: %s" % doc_id)

    @staticmethod
    def _is_same_paper(item, existing_doc):
        """判断已入库文档与当前 item 是否为同一篇论文。

        判定规则（命中任一即可）：
        1. 双方都有 DOI 且规范化后相等；
        2. 双方都有标题且规范化（忽略大小写）后相等；
        3. 双方身份指纹完全一致（标题/作者/年份/来源/DOI 组合相同）。
        引用量等易变字段不参与判定，因此引用量上涨仍视为同一篇论文的更新。
        """
        if not existing_doc:
            return False
        source = existing_doc.get("_source", {}) if isinstance(existing_doc, dict) else {}

        item_kind, item_key, item_fp = build_identity(item)
        ex_kind, ex_key, ex_fp = build_identity(source)

        if item_kind == "doi" and ex_kind == "doi" and item_key == ex_key:
            return True
        if item_kind == "title" and ex_kind == "title":
            # title 身份键含作者/年份辅助信息；标题相同即视为同一篇
            item_title = normalize_text(item.get("paper_title")).lower()
            ex_title = normalize_text(source.get("paper_title")).lower()
            if item_title and item_title == ex_title:
                return True
        return item_fp == ex_fp

    def _incr_counter(self):
        try:
            self.redis_client.incr(self.counter_key)
        except Exception as exc:  # Redis 故障不影响 ES 落库结果
            logger.warning("Redis 计数自增失败: %s", exc)


# class MysqlPipeline(object):
#     def __init__(self):
#         self.conn = MySQLdb.connect('127.0.0.1', 'root', '123456', 'paper_spider_data', charset='utf8',
#                                     use_unicode=True)
#         self.cursor = self.conn.cursor()
#
#     def process_item(self, item, spider):
#         insert_sql = """
#             insert into paper_info(paper_title,paper_writer,paper_time,paper_cite_count,paper_source,paper_abstract,paper_keywords,paper_DOI,paper_download_link)
#             values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
#         """
#         params = list()
#         params.append(item.get('paper_title', ''))
#         params.append(item.get('paper_writer', ''))
#         params.append(item.get('paper_time', ''))
#         params.append(item.get('paper_cite_count', ''))
#         params.append(item.get('paper_source', ''))
#         params.append(item.get('paper_abstract', ''))
#         params.append(item.get('paper_keywords', ''))
#         params.append(item.get('paper_DOI', ''))
#         params.append(item.get('paper_download_link', ''))
#         self.cursor.execute(insert_sql, tuple(params))
#         self.conn.commit()
#
#         return item
#
#
# class JsonExporterPipeline(object):
#     def __init__(self):
#         self.file = open('paper_export.json', 'wb')
#         self.exporter = JsonItemExporter(self.file, encoding='utf-8', ensure_ascii=False)
#         self.exporter.start_exporting()
#
#     def process_item(self, item, spider):
#         self.exporter.export_item(item)
#         return item
#
#     def spider_closed(self):
#         self.exporter.finish_exporting()
#         self.file.close()
