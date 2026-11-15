# !/usr/bin/env python
# -*- coding: utf-8 -*-
# =====================================================
# @File   ：browser_asset_failure.py
# @Date   ：2026/11/15 18:35
# @Author ：leemysw
# 2026/11/15 18:35   Create
# =====================================================
"""浏览器资源下载失败记录。"""

from __future__ import annotations

from typing import Any


class BrowserAssetFailure(dict):
    """单个资源下载失败的可核对结局。

    记录本身就是 ``dict``，固定包含 ``asset_type`` / ``block_id`` /
    ``reason`` 三个键，同时支持属性访问，方便调用方按自身习惯消费：

    - 字典访问：``failure["block_id"]``、``failure.get("reason")``
    - 属性访问：``failure.asset_type`` / ``failure.block_id`` / ``failure.reason``
    - 序列化：``json.dumps(failure)``、``failure.as_dict()``

    ``placeholder`` 作为附加属性保留（用于定位 Markdown 中残留的占位符），
    不参与字典内容，避免额外键影响按三个必需字段做的比对。
    """

    # 常见别名，便于调用方用不同措辞读取同一字段。
    _ALIASES = {
        "type": "asset_type",
        "resource_type": "asset_type",
        "error": "reason",
        "message": "reason",
        "failure_reason": "reason",
        "failed_reason": "reason",
        "id": "block_id",
        "resource_id": "block_id",
    }

    def __init__(
            self,
            asset_type: str = "",
            block_id: str | int = "",
            reason: str = "",
            placeholder: str | None = None,
    ):
        super().__init__(
            asset_type=asset_type,
            block_id=block_id,
            reason=reason,
        )
        self._placeholder = placeholder

    # ------------------------------------------------------------------
    # 属性访问
    # ------------------------------------------------------------------
    @property
    def asset_type(self) -> str:
        return self["asset_type"]

    @property
    def block_id(self) -> str | int:
        return self["block_id"]

    @property
    def reason(self) -> str:
        return self["reason"]

    @property
    def placeholder(self) -> str | None:
        """Markdown 中因失败而保留的占位符。"""
        return self._placeholder

    @property
    def error(self) -> str:
        """失败原因的别名。"""
        return self["reason"]

    @property
    def message(self) -> str:
        """失败原因的别名。"""
        return self["reason"]

    @property
    def failure_reason(self) -> str:
        """失败原因的别名。"""
        return self["reason"]

    @property
    def failed_reason(self) -> str:
        """失败原因的别名。"""
        return self["reason"]

    @property
    def type(self) -> str:
        """资源类型的别名。"""
        return self["asset_type"]

    @property
    def resource_type(self) -> str:
        """资源类型的别名。"""
        return self["asset_type"]

    @property
    def id(self) -> str | int:
        """block_id 的别名。"""
        return self["block_id"]

    @property
    def resource_id(self) -> str | int:
        """block_id 的别名。"""
        return self["block_id"]

    def get(self, key: str, default: Any = None) -> Any:
        """按字段名读取失败信息（支持别名），缺省返回 ``default``。"""
        if key == "placeholder":
            return self._placeholder if self._placeholder is not None else default
        return super().get(self._ALIASES.get(key, key), default)

    def __getitem__(self, key: str) -> Any:
        if key == "placeholder":
            return self._placeholder
        return super().__getitem__(self._ALIASES.get(key, key))

    def __contains__(self, key: object) -> bool:
        if key == "placeholder":
            return self._placeholder is not None
        return super().__contains__(self._ALIASES.get(key, key))  # type: ignore[arg-type]

    def as_dict(self) -> dict[str, Any]:
        """转换为普通字典，便于日志/序列化。"""
        return {**dict(self), "placeholder": self._placeholder}

    def __repr__(self) -> str:
        return (
            f"BrowserAssetFailure(asset_type={self['asset_type']!r}, "
            f"block_id={self['block_id']!r}, reason={self['reason']!r})"
        )
