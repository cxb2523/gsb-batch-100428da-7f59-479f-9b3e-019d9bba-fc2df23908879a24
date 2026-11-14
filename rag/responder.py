# rag/responder.py
"""Responder module — RAG reply generation + three-tier dispatch.

Responsibilities:
- RAG retrieval + LLM generation
- Three-tier quality gate (auto-send / human-confirm / human-handle)
- Message sending
- Structured auto-reply logging
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from qdrant_client import QdrantClient

from rag.embed_query import embed_query
from rag.retriever import search
from rag.generator import generate_reply
from rag.guard import classify_and_dispatch, check_retrieval, DEFAULT_HIGH_THRESHOLD
from rag.human_fallback import ContextStore
from wxbot.sender import MessageSender
from wxbot.scanner import Scanner
from notify.toast import notify_escalation

logger = logging.getLogger(__name__)

DispatchLevel = Literal["auto_send", "human_confirm", "human_handle"]


@dataclass
class ReplyResult:
    """Reply result with dispatch metadata."""
    success: bool
    reply_text: str = ""
    reason: str = ""
    escalated: bool = False
    dispatch_level: DispatchLevel = "human_handle"
    guard_decision: str = ""       # "pass" | "block" | "low_risk"
    guard_reason: str = ""
    retrieval_score: float = 0.0


class Responder:
    """RAG reply generator with three-tier quality gate."""

    def __init__(self, scanner: Scanner, qdrant_path: str = "data/qdrant"):
        self.scanner = scanner
        self.sender = MessageSender()
        self.qdrant = QdrantClient(path=qdrant_path)
        self._top_k = 5
        self._log_path = Path("logs/auto_replies.jsonl")
        self._context = ContextStore()

    def close(self):
        """Release Qdrant client so the file lock is freed."""
        try:
            self.qdrant.close()
        except Exception:
            pass

    def _log_reply(self, customer_name: str, message: str,
                   result: ReplyResult, send_ok: bool | None = None):
        """Write structured auto-reply log entry.

        Args:
            send_ok: None = send not attempted (human_confirm/escalated),
                     True = auto-send succeeded, False = auto-send failed.
        """
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now().isoformat(),
                "customer_name": customer_name,
                "customer_message": message[:500],
                "ai_reply": result.reply_text[:500] if result.reply_text else "",
                "confidence": result.retrieval_score,
                "dispatch_level": result.dispatch_level,
                "guard_decision": result.guard_decision,
                "guard_reason": result.guard_reason,
                "generated": result.success,
                "escalated": result.escalated,
            }
            if send_ok is not None:
                entry["sent"] = send_ok
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write reply log: {e}")

    def log_send_result(self, customer_name: str, reply_text: str,
                        send_ok: bool):
        """Log auto-send outcome AFTER the actual send completes.

        Called from main.py's _process_send_queue_tick after _do_send
        returns. This ensures the JSONL accurately reflects whether the
        message was actually delivered to WeChat.
        """
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now().isoformat(),
                "customer_name": customer_name,
                "customer_message": "",
                "ai_reply": reply_text[:500] if reply_text else "",
                "confidence": 0.0,
                "dispatch_level": "auto_send",
                "guard_decision": "",
                "guard_reason": "",
                "generated": True,
                "sent": send_ok,
                "escalated": False,
            }
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write send log: {e}")

    async def generate_reply(self, customer_name: str,
                             messages: list[str]) -> ReplyResult:
        """Generate reply and classify dispatch level.

        Returns a ReplyResult with dispatch_level indicating what the
        caller should do:
        - ``auto_send``: safe to send immediately
        - ``human_confirm``: show popup for human approval
        - ``human_handle``: escalate to human (toast notification)
        """
        text = " ".join(messages).strip()
        if not text or len(text) < 2:
            return ReplyResult(
                success=False, reason="消息太短",
                dispatch_level="human_handle",
            )

        # ── Embedding ──────────────────────────────────────────────

        try:
            qv = embed_query(text)
        except Exception as e:
            logger.error(f"Embedding失败: {e}")
            notify_escalation(customer_name, text, reason="Embedding错误")
            return ReplyResult(
                success=False, reason="Embedding错误", escalated=True,
                dispatch_level="human_handle",
            )

        # ── Retrieval ──────────────────────────────────────────────

        try:
            results = search(qv, self.qdrant, top_k=self._top_k)
        except Exception as e:
            logger.error(f"Qdrant检索异常: {e}")
            notify_escalation(customer_name, text, reason="检索服务异常")
            return ReplyResult(
                success=False, reason="检索服务异常", escalated=True,
                dispatch_level="human_handle",
            )

        scores = [r.score for r in results]
        chunks = [(r.payload or {}).get("text", "") for r in results]
        top_score = scores[0] if scores else 0.0

        # ── Pre-generation retrieval check ─────────────────────────

        retrieval = check_retrieval(scores)
        if retrieval.decision == "escalate":
            reason_str = f"检索低({top_score:.2f})" if scores else "无结果"
            notify_escalation(customer_name, text, reason=reason_str)
            return ReplyResult(
                success=False, reason=reason_str, escalated=True,
                dispatch_level="human_handle",
                retrieval_score=top_score,
            )

        # ── LLM Generation (with conversation history) ─────────────

        history = await asyncio.to_thread(
            self._context.get_recent, customer_name, 3)
        try:
            reply = await asyncio.wait_for(
                generate_reply(text, chunks, history=history), timeout=30)
        except asyncio.TimeoutError:
            logger.error("LLM 超时")
            notify_escalation(customer_name, text, reason="LLM超时")
            return ReplyResult(
                success=False, reason="LLM超时", escalated=True,
                dispatch_level="human_handle",
                retrieval_score=top_score,
            )
        except Exception as e:
            logger.error(f"LLM失败: {e}")
            notify_escalation(customer_name, text, reason="LLM错误")
            return ReplyResult(
                success=False, reason="LLM错误", escalated=True,
                dispatch_level="human_handle",
                retrieval_score=top_score,
            )

        # ── Three-tier quality gate ────────────────────────────────

        # Detect LLM's "needs human" signal — pass to guard, don't hard-block
        llm_requests_human = (
            "需要人工处理" in reply or "需要帮您确认" in reply
        )

        # Combined retrieval + reply quality check
        guard_result = classify_and_dispatch(
            scores, reply, chunks,
            llm_requests_human=llm_requests_human,
        )

        if guard_result.decision == "pass":
            if top_score > DEFAULT_HIGH_THRESHOLD:
                # High confidence → auto-send
                return ReplyResult(
                    success=True, reply_text=reply,
                    dispatch_level="auto_send",
                    guard_decision=guard_result.decision,
                    guard_reason=guard_result.reason,
                    retrieval_score=top_score,
                )
            else:
                # Medium confidence (0.4-0.7) → human confirm
                return ReplyResult(
                    success=True, reply_text=reply,
                    dispatch_level="human_confirm",
                    guard_decision=guard_result.decision,
                    guard_reason=guard_result.reason,
                    retrieval_score=top_score,
                )
        elif guard_result.decision == "low_risk":
            # Low risk (e.g. LLM says "需要人工处理") — check score
            if top_score > DEFAULT_HIGH_THRESHOLD:
                # High confidence overrides low_risk → auto-send
                return ReplyResult(
                    success=True, reply_text=reply,
                    dispatch_level="auto_send",
                    guard_decision=guard_result.decision,
                    guard_reason=guard_result.reason,
                    retrieval_score=top_score,
                )
            else:
                return ReplyResult(
                    success=True, reply_text=reply,
                    dispatch_level="human_confirm",
                    guard_decision=guard_result.decision,
                    guard_reason=guard_result.reason,
                    retrieval_score=top_score,
                )
        else:
            # Block → escalate to human
            notify_escalation(customer_name, text,
                              reason="质量不通过", draft_reply=reply)
            return ReplyResult(
                success=False, reason="质量不通过", escalated=True,
                reply_text=reply,
                dispatch_level="human_handle",
                guard_decision=guard_result.decision,
                guard_reason=guard_result.reason,
                retrieval_score=top_score,
            )

    def send_reply(self, text: str) -> bool:
        """Send reply via WeChat input box."""
        try:
            self.scanner.click_input_box()
            time.sleep(0.3)
            self.sender.send(text)
            return True
        except Exception as e:
            logger.error(f"发送失败: {e}")
            return False

    async def handle_customer_message(self, customer_name: str,
                                      messages: list[str]) -> ReplyResult:
        """Handle customer message: generate reply + log.

        Does NOT send — sending is the caller's responsibility via
        the unified send_queue → _do_send path (which handles
        window switching and keyboard input).
        """
        text = " ".join(messages).strip()
        # Record customer message
        await asyncio.to_thread(
            self._context.append, customer_name, "customer", text)

        result = await self.generate_reply(customer_name, messages)

        if result.success:
            # For auto_send, logging is deferred until the send actually
            # completes (caller calls log_send_result). For other dispatch
            # levels, log immediately since no send will be attempted.
            if result.dispatch_level != "auto_send":
                self._log_reply(customer_name, text, result)
            # Record assistant reply
            await asyncio.to_thread(
                self._context.append, customer_name, "assistant", result.reply_text)

        return result
