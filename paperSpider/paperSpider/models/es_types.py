# -*- coding: utf-8 -*-
# @Time     : 2020-03-22 13:35
# @Author   : beking

from datetime import datetime
from elasticsearch_dsl import DocType, Date, Nested, Boolean, \
    analyzer, Completion, Keyword, Text, Integer, Index
from elasticsearch_dsl.analysis import CustomAnalyzer as _CustomAnalyzer
from elasticsearch_dsl.connections import connections

connections.create_connection(hosts=["localhost"])


class CustomAnalyzer(_CustomAnalyzer):
    def get_analysis_definition(self):
        return {}


ik_analyzer = CustomAnalyzer("ik_max_word", filter=["lowercase"])

# 索引名集中定义，兼容不同版本 elasticsearch-dsl 的取值方式
PAPER_INDEX = "baidu"
PAPER_DOC_TYPE = "paper"


class PaperType(DocType):
    suggest = Completion(analyzer=ik_analyzer)  # 用于自动补全

    paper_title = Text(analyzer="ik_max_word")
    paper_writer = Keyword()
    paper_time = Integer()
    paper_cite_count = Integer()
    paper_source = Keyword()
    paper_abstract = Text(analyzer="ik_max_word")
    paper_keywords = Text(analyzer="ik_max_word")
    paper_DOI = Text()
    paper_download_link = Text()

    # elasticsearch-dsl 6.x 使用 Index 内部类声明索引
    class Index:
        name = PAPER_INDEX

    # 兼容 elasticsearch-dsl 5.x 的声明方式
    class Meta:
        index = PAPER_INDEX
        doc_type = PAPER_DOC_TYPE


if __name__ == "__main__":
    PaperType.init()
