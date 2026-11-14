# -*- coding: utf-8 -*-
"""safe_int 安全整型转换测试：无法解析时按缺失（None）处理。"""
import pytest

from paperSpider.utils.paper_id import safe_int


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 0),
        (12, 12),
        ("12", 12),
        ("被引 12 次", 12),
        ("1,234", 1234),
        ("-5", -5),
        (3.0, 3),
        (3.9, 3),
        ("  2016  ", 2016),
    ],
)
def test_parsable_values(value, expected):
    assert safe_int(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "暂无",
        "abc",
        float("inf"),
        float("nan"),
        True,
        False,
        [],
        {},
    ],
)
def test_unparsable_values_become_missing(value):
    assert safe_int(value) is None
