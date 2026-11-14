#!/usr/bin/env python3
"""Offline verification for the failure-outcome protocol and its HTTP mapping.

Covers the check.applescript -> server.py contract:
  * OK: success path (unchanged protocol, unchanged /chat response fields)
  * ERR: + META line parsing (outcome / elapsed / polls)
  * outcome -> HTTP status + retryable mapping (timeout & empty_result are
    retryable; selector/injection/script failures are not)
  * identical failure semantics across /chat, /v1/chat/completions, /v1/messages
  * /health never touches the Safari lock

Runs with a fake osascript (stubbed server.subprocess) — no Safari, no macOS.

Usage: python3 test_outcome_mapping.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import types

import server
from server import (
    AnthropicMessage,
    AnthropicMessagesRequest,
    ChatCompletionRequest,
    ChatRequest,
    CollectionError,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def fake_osascript(stdout="", stderr="", returncode=0, raises=None):
    """Swap server.subprocess for a stub whose run() returns fake osascript output."""
    real = server.subprocess

    def _run(*args, **kwargs):
        if raises is not None:
            raise raises
        return FakeProc(stdout=stdout, stderr=stderr, returncode=returncode)

    server.subprocess = types.SimpleNamespace(
        run=_run, TimeoutExpired=subprocess.TimeoutExpired
    )
    return real


def restore_osascript(real):
    server.subprocess = real


def expect_collection_error(fn, outcome, status, retryable):
    try:
        fn()
    except CollectionError as exc:
        assert exc.outcome == outcome, f"outcome {exc.outcome!r} != {outcome!r}"
        assert exc.status_code == status, f"status {exc.status_code} != {status}"
        assert exc.retryable is retryable, f"retryable {exc.retryable} != {retryable}"
        return exc
    raise AssertionError(f"expected CollectionError({outcome})")


# --------------------------------------------------------------------------- #
# 1. Success path
# --------------------------------------------------------------------------- #

def test_success_ok_protocol_unchanged():
    real = fake_osascript(stdout="OK:\n你好，世界\n第二行\n")
    try:
        text = server._run_applescript("hi", "in", "out", "")
    finally:
        restore_osascript(real)
    assert text == "你好，世界\n第二行"
    print("[ok] success_ok_protocol_unchanged")


def test_success_chat_response_fields_compatible():
    real = fake_osascript(stdout="OK:\n答案正文")
    try:
        resp = server.chat(
            ChatRequest(prompt="问", input_selector="i", result_selector="r")
        )
    finally:
        restore_osascript(real)
    # Existing fields must all still be there with the same types.
    assert resp["response"] == "答案正文"
    assert isinstance(resp["elapsed_seconds"], float)
    assert resp["selectors"] == {"input": "i", "result": "r", "button": None}
    print("[ok] success_chat_response_fields_compatible")


def test_success_body_with_meta_like_text_not_stripped():
    # The OK: body is opaque text; a META-looking line inside it is content.
    real = fake_osascript(stdout="OK:\n正文\nMETA: outcome=timeout elapsed=1 polls=1")
    try:
        text = server._run_applescript("hi", "in", "out", "")
    finally:
        restore_osascript(real)
    assert text == "正文\nMETA: outcome=timeout elapsed=1 polls=1"
    print("[ok] success_body_with_meta_like_text_not_stripped")


# --------------------------------------------------------------------------- #
# 2. Timeout (retryable)
# --------------------------------------------------------------------------- #

def test_timeout_outcome_retryable_504():
    out = (
        "ERR:\n轮询 60 次后页面仍无内容（结果节点存在但文本为空，可能仍在生成）。\n"
        "META: outcome=timeout elapsed=183 polls=60\n"
    )
    real = fake_osascript(stdout=out)
    try:
        exc = expect_collection_error(
            lambda: server._run_applescript("hi", "in", "out", ""),
            "timeout", 504, True,
        )
    finally:
        restore_osascript(real)
    assert exc.elapsed == 183.0
    assert exc.polls == 60
    assert "轮询 60 次" in exc.reason
    print("[ok] timeout_outcome_retryable_504")


def test_subprocess_timeout_maps_to_retryable_timeout():
    real = fake_osascript(
        raises=subprocess.TimeoutExpired(cmd="osascript", timeout=server.OSASCRIPT_TIMEOUT)
    )
    try:
        exc = expect_collection_error(
            lambda: server._run_applescript("hi", "in", "out", ""),
            "timeout", 504, True,
        )
    finally:
        restore_osascript(real)
    assert "timed out" in exc.reason
    print("[ok] subprocess_timeout_maps_to_retryable_timeout")


# --------------------------------------------------------------------------- #
# 3. Empty result (retryable)
# --------------------------------------------------------------------------- #

def test_empty_result_outcome_retryable_504():
    out = (
        "ERR:\n轮询 60 次后未匹配到任何结果节点（页面尚未渲染或结果选择器失效）。\n"
        "META: outcome=empty_result elapsed=180 polls=60\n"
    )
    real = fake_osascript(stdout=out)
    try:
        exc = expect_collection_error(
            lambda: server._run_applescript("hi", "in", "out", ""),
            "empty_result", 504, True,
        )
    finally:
        restore_osascript(real)
    assert exc.polls == 60
    print("[ok] empty_result_outcome_retryable_504")


def test_ok_with_empty_body_is_empty_result():
    real = fake_osascript(stdout="OK:\n")
    try:
        expect_collection_error(
            lambda: server._run_applescript("hi", "in", "out", ""),
            "empty_result", 504, True,
        )
    finally:
        restore_osascript(real)
    print("[ok] ok_with_empty_body_is_empty_result")


# --------------------------------------------------------------------------- #
# 4. Selector missing (NOT retryable)
# --------------------------------------------------------------------------- #

def test_selector_missing_not_retryable_502():
    out = (
        "ERR:\n注入失败: ERROR_INPUT_NOT_FOUND\n"
        "META: outcome=selector_missing elapsed=1 polls=0\n"
    )
    real = fake_osascript(stdout=out)
    try:
        exc = expect_collection_error(
            lambda: server._run_applescript("hi", "in", "out", ""),
            "selector_missing", 502, False,
        )
    finally:
        restore_osascript(real)
    assert "ERROR_INPUT_NOT_FOUND" in exc.reason
    assert exc.polls == 0
    print("[ok] selector_missing_not_retryable_502")


def test_injection_failed_not_retryable_502():
    out = "ERR:\n注入失败: 不允许执行 JavaScript。\nMETA: outcome=injection_failed elapsed=2 polls=0\n"
    real = fake_osascript(stdout=out)
    try:
        expect_collection_error(
            lambda: server._run_applescript("hi", "in", "out", ""),
            "injection_failed", 502, False,
        )
    finally:
        restore_osascript(real)
    print("[ok] injection_failed_not_retryable_502")


# --------------------------------------------------------------------------- #
# 5. Robustness / legacy output
# --------------------------------------------------------------------------- #

def test_legacy_err_without_meta_is_unknown_502():
    real = fake_osascript(stdout="ERR:\n超时或抓取失败。")
    try:
        exc = expect_collection_error(
            lambda: server._run_applescript("hi", "in", "out", ""),
            "unknown", 502, False,
        )
    finally:
        restore_osascript(real)
    assert exc.reason == "超时或抓取失败。"
    assert exc.elapsed is None and exc.polls is None
    print("[ok] legacy_err_without_meta_is_unknown_502")


def test_nonzero_returncode_is_script_error():
    real = fake_osascript(stderr="osascript: can't open script", returncode=1)
    try:
        expect_collection_error(
            lambda: server._run_applescript("hi", "in", "out", ""),
            "script_error", 502, False,
        )
    finally:
        restore_osascript(real)
    print("[ok] nonzero_returncode_is_script_error")


def test_multiline_reason_preserved_and_junk_meta_tolerated():
    out = "ERR:\n第一行原因\n第二行原因\nMETA: outcome=timeout elapsed=abc polls=\n"
    real = fake_osascript(stdout=out)
    try:
        exc = expect_collection_error(
            lambda: server._run_applescript("hi", "in", "out", ""),
            "timeout", 504, True,
        )
    finally:
        restore_osascript(real)
    assert exc.reason == "第一行原因\n第二行原因"
    assert exc.elapsed is None and exc.polls is None  # junk numbers -> None, not a crash
    print("[ok] multiline_reason_preserved_and_junk_meta_tolerated")


# --------------------------------------------------------------------------- #
# 6. Consistent HTTP mapping across the three entries
# --------------------------------------------------------------------------- #

def test_all_entries_share_same_failure_semantics():
    boom = CollectionError("timeout", "页面还在生成", elapsed=183.0, polls=60)
    real_run = server._run_applescript
    real_lazy = server.LAZY_TOOLS
    real_in, real_out = server.DEFAULT_INPUT_SELECTOR, server.DEFAULT_RESULT_SELECTOR
    server._run_applescript = lambda *a, **k: (_ for _ in ()).throw(boom)
    server.LAZY_TOOLS = False
    server.DEFAULT_INPUT_SELECTOR, server.DEFAULT_RESULT_SELECTOR = "i", "r"
    try:
        e1 = expect_collection_error(
            lambda: server.chat(ChatRequest(prompt="hi")), "timeout", 504, True
        )
        e2 = expect_collection_error(
            lambda: server.chat_completions(
                ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}])
            ),
            "timeout", 504, True,
        )
        e3 = expect_collection_error(
            lambda: server.anthropic_messages(
                AnthropicMessagesRequest(
                    messages=[AnthropicMessage(role="user", content="hi")]
                )
            ),
            "timeout", 504, True,
        )
    finally:
        server._run_applescript = real_run
        server.LAZY_TOOLS = real_lazy
        server.DEFAULT_INPUT_SELECTOR, server.DEFAULT_RESULT_SELECTOR = real_in, real_out
    # Same failure object surfaced through all three entries.
    assert e1 is e2 is e3 is boom
    print("[ok] all_entries_share_same_failure_semantics")


def test_error_response_body_shape():
    exc = CollectionError("empty_result", "页面暂时无内容", elapsed=12.0, polls=4)
    resp = asyncio.run(server._collection_error_handler(None, exc))
    assert resp.status_code == 504
    body = json.loads(bytes(resp.body))
    # `detail` stays a plain string (as with HTTPException) for old clients...
    assert body["detail"] == "页面暂时无内容"
    # ...while the structured classification rides alongside it.
    assert body["error"] == {
        "type": "collection_error",
        "outcome": "empty_result",
        "retryable": True,
        "elapsed_seconds": 12.0,
        "polls": 4,
    }

    exc2 = CollectionError("selector_missing", "注入失败: ERROR_INPUT_NOT_FOUND")
    resp2 = asyncio.run(server._collection_error_handler(None, exc2))
    body2 = json.loads(bytes(resp2.body))
    assert resp2.status_code == 502
    assert body2["error"]["retryable"] is False
    print("[ok] error_response_body_shape")


# --------------------------------------------------------------------------- #
# 7. /health stays lock-free
# --------------------------------------------------------------------------- #

def test_health_does_not_take_safari_lock():
    acquired = server._call_lock.acquire(blocking=False)
    assert acquired, "lock should be free at test start"
    try:
        result = server.health()  # would hang/deadlock here if it needed the lock
        assert "status" in result and "applescript_exists" in result
        assert server._call_lock.locked()
    finally:
        server._call_lock.release()
    print("[ok] health_does_not_take_safari_lock")


if __name__ == "__main__":
    test_success_ok_protocol_unchanged()
    test_success_chat_response_fields_compatible()
    test_success_body_with_meta_like_text_not_stripped()
    test_timeout_outcome_retryable_504()
    test_subprocess_timeout_maps_to_retryable_timeout()
    test_empty_result_outcome_retryable_504()
    test_ok_with_empty_body_is_empty_result()
    test_selector_missing_not_retryable_502()
    test_injection_failed_not_retryable_502()
    test_legacy_err_without_meta_is_unknown_502()
    test_nonzero_returncode_is_script_error()
    test_multiline_reason_preserved_and_junk_meta_tolerated()
    test_all_entries_share_same_failure_semantics()
    test_error_response_body_shape()
    test_health_does_not_take_safari_lock()
    print("\nALL TESTS PASSED")
