# rag/guard.py
"""Two-layer quality guard for RAG auto-reply system.

Layer 1: Retrieval relevance check (similarity score thresholds).
Layer 2: Generated reply quality check — three-state classification
         (pass / block / low_risk) replacing the old bool return.

Thresholds are read from config.json at init and support hot-reload.
"""

import re
import logging
from dataclasses import dataclass
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# ── keyword lists ──────────────────────────────────────────────────────────

UNCERTAINTY_KEYWORDS = [
    "我不确定", "我不太确定", "可能", "也许", "大概", "据我所知",
    "我不是很了解", "建议咨询", "请咨询", "详情请咨询",
    "这个我不清楚", "抱歉我不太清楚",
    "或许可以", "可能需要", "您可以试试",
]

GENERIC_REPLY_PATTERNS = [
    "请问您主要想解决什么问题",
    "请问有什么可以帮您",
    "请问您需要什么帮助",
    "很高兴为您服务",
    "我是您的销售客服",
    "我是销售客服",
    "请问您需要咨询什么",
    "有什么可以帮到您",
    "请问您想了解什么",
]

# ── hedge phrases (weak signal, not a definite block) ──────────────────────

HEDGE_PHRASES = [
    "具体价格请咨询",
    "详情请联系",
    "以实际为准",
    "具体请咨询",
    "最终价格以",
    "请联系客服",
    "以上仅供参考",
]

# ── defaults (overridable via config.json → rag section) ───────────────────

DEFAULT_HIGH_THRESHOLD = 0.7
DEFAULT_LOW_THRESHOLD = 0.4

# Retrieval score below this triggers low_risk downgrade even if reply
# passes content checks.  Prevents auto-sending fabricated replies when
# the knowledge base doesn't cover the question.
LOW_RETRIEVAL_THRESHOLD = 0.5


# ── dataclasses ────────────────────────────────────────────────────────────


@dataclass
class RetrievalResult:
    decision: str       # "generate" | "escalate"
    confidence: str     # "high" | "low" | "none"


@dataclass
class GuardResult:
    """Three-state reply quality classification.

    Attributes:
        decision: ``"pass"`` (safe to auto-send), ``"block"`` (escalate),
                  or ``"low_risk"`` (needs human confirmation).
        reason: Human-readable reason string.
        confidence: Guard's own confidence in this classification (0.0-1.0).
    """
    decision: Literal["pass", "block", "low_risk"]
    reason: str = ""
    confidence: float = 0.0


# ── threshold access ──────────────────────────────────────────────────────


def _get_thresholds(config: Optional[dict] = None) -> tuple[float, float]:
    """Return (high, low) thresholds from config or defaults."""
    high = DEFAULT_HIGH_THRESHOLD
    low = DEFAULT_LOW_THRESHOLD
    if config:
        rag = config.get("rag", {})
        high = rag.get("high_confidence_threshold", high)
        low = rag.get("low_confidence_threshold", low)
    return high, low


# ── Layer 1 ────────────────────────────────────────────────────────────────


def check_retrieval(scores: list[float], config: dict = None) -> RetrievalResult:
    """Check if retrieved knowledge is relevant enough."""
    high_thresh, low_thresh = _get_thresholds(config)

    if not scores:
        return RetrievalResult(decision="escalate", confidence="none")

    top_score = scores[0]

    if top_score > high_thresh:
        return RetrievalResult(decision="generate", confidence="high")
    elif top_score >= low_thresh:
        return RetrievalResult(decision="generate", confidence="low")
    else:
        return RetrievalResult(decision="escalate", confidence="none")


# ── Layer 2 (three-state) ──────────────────────────────────────────────────


def check_reply(reply: str, context_chunks: list[str]) -> bool:
    """Legacy boolean check — delegates to check_reply_and_classify.

    Returns True if the new classifier returns ``pass`` or ``low_risk``
    (i.e. not a definite block).  For strict auto-send gating use
    check_reply_and_classify directly.
    """
    result = check_reply_and_classify(reply, context_chunks)
    return result.decision != "block"


def check_reply_and_classify(
    reply: str, context_chunks: list[str]
) -> GuardResult:
    """Three-state reply quality classification.

    *pass*   — safe to auto-send: no uncertainty, no hallucination, no generic pattern.
    *block*  — escalate to human: contains uncertainty keywords, hallucinated numbers,
               or generic AI fabrications.
    *low_risk* — reply is mostly fine but contains a hedge phrase → ask human
                 to confirm before sending.

    Args:
        reply: Generated reply text from the LLM.
        context_chunks: Retrieved knowledge chunks for grounding check.

    Returns:
        GuardResult with the appropriate decision.
    """
    # Rule 1: Uncertainty keyword → block
    for keyword in UNCERTAINTY_KEYWORDS:
        if keyword in reply:
            logger.info(f"Guard: block — uncertainty keyword '{keyword}'")
            return GuardResult(
                decision="block",
                reason=f"uncertainty_keyword: {keyword}",
                confidence=0.95,
            )

    # Rule 2: Hallucinated numbers → block
    numbers_in_reply = set(re.findall(r'\d+', reply))
    if numbers_in_reply:
        context_text = " ".join(context_chunks)
        numbers_in_context = set(re.findall(r'\d+', context_text))
        unknown = numbers_in_reply - numbers_in_context
        if len(unknown) > 0 and len(unknown) >= len(numbers_in_reply) * 0.3:
            logger.info(f"Guard: block — ungrounded numbers {unknown}")
            return GuardResult(
                decision="block",
                reason=f"hallucinated_number: {unknown}",
                confidence=0.90,
            )

    # Rule 3: Generic AI pattern → block
    for pattern in GENERIC_REPLY_PATTERNS:
        if pattern in reply:
            logger.info(f"Guard: block — generic pattern '{pattern}'")
            return GuardResult(
                decision="block",
                reason=f"generic_pattern: {pattern}",
                confidence=0.85,
            )

    # Rule 4: Hedge phrases → low_risk (need human to glance)
    for phrase in HEDGE_PHRASES:
        if phrase in reply:
            logger.info(f"Guard: low_risk — hedge phrase '{phrase}'")
            return GuardResult(
                decision="low_risk",
                reason=f"hedge_phrase: {phrase}",
                confidence=0.70,
            )

    # All checks passed → safe
    return GuardResult(
        decision="pass",
        reason="all_checks_passed",
        confidence=0.90,
    )


# ── convenience ────────────────────────────────────────────────────────────


def classify_and_dispatch(
    retrieval_scores: list[float],
    reply: str,
    context_chunks: list[str],
    config: dict = None,
    llm_requests_human: bool = False,
) -> GuardResult:
    """Combined two-layer quality gate returning a single GuardResult.

    Layer 1: retrieval score check → block if top < low threshold.
    Layer 2: reply content check → pass / block / low_risk.

    When ``llm_requests_human`` is True (LLM reply contains "需要人工处理"
    etc.), treat as ``low_risk`` instead of ``block``. High-confidence
    retrieval (>0.7) can still auto-send; medium-confidence becomes
    human_confirm.

    Returns ``block`` with ``confidence="none"`` if retrieval fails;
    otherwise delegates to check_reply_and_classify.
    """
    ret = check_retrieval(retrieval_scores, config=config)
    if ret.decision == "escalate":
        return GuardResult(
            decision="block",
            reason=f"retrieval_low_confidence: {ret.confidence}",
            confidence=0.30,
        )

    result = check_reply_and_classify(reply, context_chunks)

    # ── Low retrieval score → downgrade pass to low_risk ────────────
    # When retrieval confidence is low, the knowledge base doesn't
    # cover the question well. Even if the reply looks OK (no keywords
    # triggered), it may be fabricated. Downgrade to low_risk so a
    # human can verify before sending.
    if (retrieval_scores and retrieval_scores[0] < LOW_RETRIEVAL_THRESHOLD
            and result.decision == "pass"):
        logger.info(
            f"Guard: low_risk — low retrieval score "
            f"({retrieval_scores[0]:.2f} < {LOW_RETRIEVAL_THRESHOLD})"
        )
        return GuardResult(
            decision="low_risk",
            reason=f"low_retrieval_confidence: {retrieval_scores[0]:.2f}",
            confidence=0.50,
        )

    # LLM requests human → downgrade to low_risk (not block)
    if llm_requests_human and result.decision == "pass":
        logger.info("Guard: low_risk — LLM requests human handling")
        return GuardResult(
            decision="low_risk",
            reason="llm_requests_human",
            confidence=0.60,
        )

    return result
