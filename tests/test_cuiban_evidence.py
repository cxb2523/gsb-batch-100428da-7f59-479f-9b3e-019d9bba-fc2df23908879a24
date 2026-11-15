# -*- coding: utf-8 -*-
"""
催办链路"采集证据可信度"分支测试。

用自造的假 wx 对象与消息列表驱动，不依赖真实微信：
1) 无有效证据（搜索失败 + 历史无接龙）-> 不 @ 任何人，发人工核对提示，cuiban 返回 False；
2) 有证据且名单为空（确实全员完成）-> 仍发送"所有人已完成接龙！"；
3) 部分证据（搜索失败但历史解析出参与者）-> 走回退路径，正常催办未交者；
4) get_participated 的 meta 标志与 router status 的可信度字段。
Run:  pytest
"""
from datetime import datetime

import pytest

import config
import bot_core as bc


class FakeWx:
    """最小假微信客户端：记录所有 SendMsg 调用。"""

    def __init__(self):
        self.sent = []  # [{"msg":..., "who":..., "at":...}]
        self.current_chat = ""

    def ChatWith(self, who, *args, **kwargs):
        self.current_chat = who
        return True

    def SendMsg(self, msg, *args, **kwargs):
        self.sent.append({
            "msg": msg,
            "who": kwargs.get("who", self.current_chat),
            "at": kwargs.get("at"),
        })

    def GetAllMessage(self, *args, **kwargs):
        return []


def _today_tag():
    return datetime.now().strftime("%Y.%m.%d")


def _jielong_msg(sender, names):
    """构造一条当天接龙消息（dict 形态，与 _normalize_msgs 输出一致）。"""
    lines = [f"#接龙   {_today_tag()}日报", "注：每人姓名仅可出现1次"]
    for i, n in enumerate(names, 1):
        lines.append(f"{i}. {n}")
    return {
        "type": "friend",
        "sender": sender,
        "content": "\n".join(lines),
        "is_self": False,
    }


def _patch_common(monkeypatch, msgs, search_error="search window unavailable"):
    """让催办链路脱离真实微信：搜索失败、历史消息用传入列表、不发真实消息。"""
    def _boom(wx):
        raise RuntimeError(search_error)

    monkeypatch.setattr(bc, "_latest_jielong_via_wechat_search", _boom)
    monkeypatch.setattr(bc, "get_all_messages", lambda wx, **kwargs: list(msgs))
    monkeypatch.setattr(bc, "chat_with", lambda wx, target: True)
    monkeypatch.setattr(bc, "delay", lambda: None)


def _at_targets(wx):
    """展开所有 SendMsg 的 at 目标为扁平集合。"""
    targets = set()
    for s in wx.sent:
        at = s["at"]
        if not at:
            continue
        if isinstance(at, (list, tuple)):
            targets.update(at)
        else:
            targets.add(at)
    return targets


# ---------- 分支 1：无有效证据 ----------
def test_cuiban_no_evidence_sends_notice_and_fails(monkeypatch):
    # 搜索失败 + 历史里完全没有当天接龙 -> 采集失败
    _patch_common(monkeypatch, [])
    wx = FakeWx()

    ok = bc.cuiban(wx)

    assert ok is False
    # 绝不 @ 任何人（避免把花名册当未交者刷屏）
    assert _at_targets(wx) == set()
    # 群里收到"未能读取接龙，请人工核对"的提示
    assert any(config.CUIBAN_READ_FAIL_TEXT in s["msg"] for s in wx.sent)
    # 不应出现"所有人已完成"的回执
    assert not any("所有人已完成接龙" in s["msg"] for s in wx.sent)


def test_cuiban_no_evidence_even_with_unrelated_messages(monkeypatch):
    # 历史里有消息，但没有任何当天接龙 -> 仍属无证据
    msgs = [
        {"type": "friend", "sender": "张三", "content": "收到", "is_self": False},
        {"type": "friend", "sender": "李四", "content": "好的", "is_self": False},
    ]
    _patch_common(monkeypatch, msgs)
    wx = FakeWx()

    ok = bc.cuiban(wx)

    assert ok is False
    assert _at_targets(wx) == set()
    assert any(config.CUIBAN_READ_FAIL_TEXT in s["msg"] for s in wx.sent)


# ---------- 分支 2：有证据但名单为空（全员已完成） ----------
def test_cuiban_all_done_still_sends_completion(monkeypatch):
    members = config.get_effective_members()
    msgs = [_jielong_msg("张三", members)]  # 接龙里包含全部应交人员
    _patch_common(monkeypatch, msgs)
    wx = FakeWx()

    ok = bc.cuiban(wx)

    assert ok is True
    assert any("所有人已完成接龙" in s["msg"] for s in wx.sent)
    assert _at_targets(wx) == set()


# ---------- 分支 3：部分证据（回退路径）-> 正常催办 ----------
def test_cuiban_partial_evidence_reminds_missing(monkeypatch):
    members = config.get_effective_members()
    done = members[:3]
    msgs = [_jielong_msg(done[0], done)]  # 搜索失败，但历史里解析出 3 人
    _patch_common(monkeypatch, msgs)
    wx = FakeWx()

    ok = bc.cuiban(wx)

    assert ok is True
    reminded = _at_targets(wx)
    expected_missing = set(members) - set(done)
    assert expected_missing, "测试前提：应存在未交者"
    assert expected_missing <= reminded
    assert not (set(done) & reminded)


# ---------- meta 标志 ----------
def test_get_participated_meta_no_evidence(monkeypatch):
    _patch_common(monkeypatch, [])
    participated, jielong_text, meta = bc.get_participated(FakeWx(), return_meta=True)

    assert participated == []
    assert meta["evidence_reliable"] is False
    assert meta["search_ok"] is False
    assert meta["fallback_used"] is True
    assert meta["history_jielong_found"] is False


def test_get_participated_meta_fallback_with_evidence(monkeypatch):
    msgs = [_jielong_msg("张三", ["张三", "李四"])]
    _patch_common(monkeypatch, msgs)
    participated, jielong_text, meta = bc.get_participated(FakeWx(), return_meta=True)

    assert meta["evidence_reliable"] is True
    assert meta["fallback_used"] is True            # 搜索失败，走了历史回退
    assert meta["history_jielong_found"] is True
    assert "张三" in participated and "李四" in participated
    assert "#接龙" in jielong_text


def test_get_participated_meta_search_ok(monkeypatch):
    today = _today_tag()
    text = f"#接龙   {today}日报\n1. 张三\n"
    monkeypatch.setattr(bc, "_latest_jielong_via_wechat_search", lambda wx: text)
    monkeypatch.setattr(bc, "get_all_messages", lambda wx, **kwargs: [])
    monkeypatch.setattr(bc, "chat_with", lambda wx, target: True)

    participated, jielong_text, meta = bc.get_participated(FakeWx(), return_meta=True)

    assert meta["evidence_reliable"] is True
    assert meta["search_ok"] is True
    assert meta["fallback_used"] is False
    assert "张三" in participated


# ---------- router status 字段 ----------
def test_router_status_exposes_reliability_field(monkeypatch):
    ocr = pytest.importorskip("openclaw_command_router")

    fake_meta = {"evidence_reliable": False, "fallback_used": True}
    monkeypatch.setattr(
        ocr, "get_participated",
        lambda wx, **kwargs: ([], "", fake_meta),
    )
    monkeypatch.setattr(ocr, "get_leave_members", lambda wx: [])

    status = ocr.get_status(FakeWx())

    assert status["participants_data_reliable"] is False
    assert status["fallback_used"] is True
    # 字段存在即可，其余既有字段不应被破坏
    assert "done" in status and "not_done" in status
