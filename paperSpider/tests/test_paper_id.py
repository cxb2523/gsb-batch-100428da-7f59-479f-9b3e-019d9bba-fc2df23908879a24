# -*- coding: utf-8 -*-
"""文档标识生成规则的单元测试：稳定性、唯一性、冲突消歧。"""
import pytest

from paperSpider.utils.paper_id import (
    build_identity,
    disambiguate_id,
    generate_paper_id,
    normalize_doi,
    normalize_text,
)
from conftest import make_item


class TestNormalize:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, ""),
            ("", ""),
            ("  hello   world  ", "hello world"),
            ("深度学习\n 综述", "深度学习 综述"),
            (["a", " b ", "c"], "a b c"),
        ],
    )
    def test_normalize_text(self, raw, expected):
        assert normalize_text(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("10.1000/abc", "10.1000/abc"),
            ("doi:10.1000/Abc", "10.1000/abc"),
            ("https://doi.org/10.1109/CVPR.2016.90", "10.1109/cvpr.2016.90"),
            ("http://dx.doi.org/10.48550/arXiv.1502.03167", "10.48550/arxiv.1502.03167"),
            ("没有doi", ""),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalize_doi(self, raw, expected):
        assert normalize_doi(raw) == expected


class TestPaperIdStability:
    """同一论文多次处理必须得到相同标识。"""

    def test_same_doi_varying_format_same_id(self):
        a = make_item(paper_title="A", paper_DOI="doi:10.1000/X.Y")
        b = make_item(paper_title="A", paper_DOI="https://doi.org/10.1000/x.y")
        c = make_item(paper_title="A", paper_DOI="10.1000/X.Y")
        ids = {generate_paper_id(a), generate_paper_id(b), generate_paper_id(c)}
        assert len(ids) == 1
        assert ids.pop().startswith("paper_doi_")

    def test_repeated_processing_same_id(self):
        item = make_item(
            paper_title="Attention Is All You Need",
            paper_writer=["Vaswani"],
            paper_time="2017",
        )
        first = generate_paper_id(item)
        # 模拟重复抓取：引用量发生变化，但身份字段不变
        again = make_item(
            paper_title="Attention Is All You Need",
            paper_writer=["Vaswani"],
            paper_time="2017",
            paper_cite_count="99999",
        )
        assert first == generate_paper_id(again)

    def test_whitespace_and_case_insensitive_title_same_id(self):
        a = make_item(paper_title="  Deep   Learning ", paper_writer=["A B"], paper_time="2020")
        b = make_item(paper_title="deep learning", paper_writer=["A B"], paper_time="2020")
        assert generate_paper_id(a) == generate_paper_id(b)

    def test_source_only_is_stable(self):
        a = make_item(paper_source="https://arxiv.org/abs/1502.03167", paper_abstract="x")
        b = make_item(paper_source="https://arxiv.org/abs/1502.03167", paper_abstract="x")
        assert generate_paper_id(a) == generate_paper_id(b)
        assert generate_paper_id(a).startswith("paper_src_")


class TestPaperIdUniqueness:
    """不同论文不得得到相同标识。"""

    def test_different_doi_different_id(self):
        # 有 DOI 时 DOI 是最高优先级身份依据
        a = make_item(paper_title="Same Title", paper_DOI="10.1000/a")
        b = make_item(paper_title="Same Title", paper_DOI="10.1000/b")
        assert generate_paper_id(a) != generate_paper_id(b)

    def test_same_title_with_and_without_doi_different_id(self):
        # 同标题：一条带 DOI、一条不带，标识也必须不同（身份依据不同）
        with_doi = make_item(paper_title="Same Title", paper_DOI="10.1000/a")
        without_doi = make_item(paper_title="Same Title")
        assert generate_paper_id(with_doi) != generate_paper_id(without_doi)
        assert generate_paper_id(with_doi).startswith("paper_doi_")
        assert generate_paper_id(without_doi).startswith("paper_title_")

    def test_same_title_different_year_different_id(self):
        a = make_item(paper_title="Survey", paper_writer=["X"], paper_time="2019")
        b = make_item(paper_title="Survey", paper_writer=["X"], paper_time="2021")
        assert generate_paper_id(a) != generate_paper_id(b)

    def test_same_title_different_authors_different_id(self):
        a = make_item(paper_title="Survey", paper_writer=["Alice"], paper_time="2020")
        b = make_item(paper_title="Survey", paper_writer=["Bob"], paper_time="2020")
        assert generate_paper_id(a) != generate_paper_id(b)

    def test_same_source_different_papers_different_id(self):
        # 来源 URL 重复（原 bug 场景）：没有标题/DOI，靠完整指纹区分不同论文
        a = make_item(paper_source="https://common.url/x", paper_abstract="paper A content")
        b = make_item(paper_source="https://common.url/x", paper_abstract="paper B content")
        assert generate_paper_id(a) != generate_paper_id(b)

    def test_generated_ids_are_namespaced_away_from_legacy_source_ids(self):
        # 历史数据直接用 source URL 当 _id，新标识带前缀，不会撞名
        source = "http://openaccess.thecvf.com/paper.pdf"
        item = make_item(paper_source=source)
        new_id = generate_paper_id(item)
        assert new_id != source
        assert new_id.startswith("paper_")

    def test_no_identity_returns_none(self):
        item = make_item(paper_abstract="只有摘要，没有任何身份字段")
        assert generate_paper_id(item) is None
        assert build_identity(item)[0] == "unknown"


class TestDisambiguate:
    def test_disambiguate_id_is_deterministic(self):
        item = make_item(paper_title="T", paper_source="u")
        base = generate_paper_id(item)
        d1 = disambiguate_id(base, item)
        d2 = disambiguate_id(base, item)
        assert d1 == d2  # 重复处理同一冲突，兜底标识依旧稳定
        assert d1 != base
        assert d1.startswith(base + "fb_")

    def test_different_papers_get_different_disambiguated_ids(self):
        base = "paper_title_0123456789abcdef"
        # 同标题（主标识相同）但摘要不同的两篇论文，指纹不同 => 兜底标识不同
        a = make_item(paper_title="T", paper_abstract="A")
        b = make_item(paper_title="T", paper_abstract="B")
        assert disambiguate_id(base, a) != disambiguate_id(base, b)
