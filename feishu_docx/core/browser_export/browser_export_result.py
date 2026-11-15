# !/usr/bin/env python
# -*- coding: utf-8 -*-
# =====================================================
# @File   ：browser_export_result.py
# @Date   ：2026/11/15 18:35
# @Author ：leemysw
# 2026/11/15 18:35   Create
# =====================================================
"""浏览器导出结果。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from feishu_docx.core.browser_export.browser_asset_failure import BrowserAssetFailure


class BrowserExportResult(Path):
    """导出输出文件路径，并携带本次导出的资源失败结局。

    继承 ``Path`` 以保持既有返回值的路径语义兼容：字符串化、拼接、
    比较与文件读写行为均与普通 ``Path`` 一致，同时额外提供
    ``failures`` / ``failed_assets`` 供调用方核对失败资源。
    """

    # 由 Path 派生出的新实例（如 with_suffix）不会经过 __init__，
    # 这里给出类级默认值，避免读取 failures 时抛 AttributeError。
    _failures: tuple[BrowserAssetFailure, ...] = ()

    def __init__(
            self,
            *args,
            failures: Iterable[BrowserAssetFailure] | None = None,
    ):
        super().__init__(*args)
        self._failures = tuple(failures or [])

    @property
    def failures(self) -> list[BrowserAssetFailure]:
        """本次导出中下载失败的资源结局；全部成功时为空列表。"""
        return list(self._failures)

    @property
    def output_path(self) -> BrowserExportResult:
        """输出文件路径（即本对象自身）。"""
        return self

    @property
    def path(self) -> BrowserExportResult:
        """输出文件路径的别名。"""
        return self

    @property
    def failed_assets(self) -> list[BrowserAssetFailure]:
        """资源失败结局列表的别名。"""
        return self.failures

    @property
    def asset_failures(self) -> list[BrowserAssetFailure]:
        """资源失败结局列表的别名。"""
        return self.failures

    @property
    def errors(self) -> list[BrowserAssetFailure]:
        """资源失败结局列表的别名。"""
        return self.failures

    def __getitem__(self, key: str) -> object:
        """支持以 ``result["failures"]`` 形式读取失败结局。"""
        if key == "failures":
            return self.failures
        raise KeyError(key)
