"""Generate customer reply using retrieved knowledge and DeepSeek API.

Supports multi-turn conversation context via sliding window injection.
Includes prompt injection defense (filters system-instruction patterns).
"""

import logging
import re
from typing import Optional

from rag.llm_client import chat

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个专业的销售客服助手。你的回答必须严格遵守以下规则：

1. **只使用提供的知识片段来回答客户问题。** 如果知识片段中没有相关信息，或者客户问的话题知识片段完全没有涉及，你必须回复"需要人工处理"。不得编造、推测或补充任何知识片段中没有的信息。
2. 知识不足时的唯一正确回复是"需要人工处理"这6个字。
   - 禁止回复"我没有相关信息"、"我不太清楚"、"建议您咨询"、"或许可以"等变通说法
   - 禁止回复"请问您需要什么帮助"、"您好请问有什么可以帮您"、"请问您主要想解决什么问题"等泛泛问候
   - 禁止回复"很高兴为您服务"、"我是XX客服"等自我介绍
   - 价格/优惠/折扣等敏感信息同样回复"需要人工处理"，不得编造不同说辞
   - 知识不足时唯一正确回复就是"需要人工处理"
3. 当知识片段覆盖了客户问题时，回复要简洁、专业、友好，像真人销售在跟客户对话。
4. 不要透露你是AI或机器人。
5. 不要使用"根据知识库"、"根据提供的信息"等暴露AI身份的表述。

{conversation_history}

可用的知识片段：
{context}

请根据以上信息回复客户的问题。记住：知识不足时只能回复"需要人工处理"。。"""

# Patterns to filter from customer messages (prompt injection defense)
_INJECTION_PATTERNS = [
    r'(?i)你是一个\S*',
    r'(?i)请忽略\S*',
    r'(?i)忽略上面的\S*',
    r'(?i)新的指令\S*',
    r'(?i)ignore\s+(all\s+)?(previous|above)\s+instructions',
    r'(?i)system\s*:\s*',
]


def _sanitize_message(text: str) -> str:
    """Remove prompt-injection patterns from customer messages."""
    for pattern in _INJECTION_PATTERNS:
        text = re.sub(pattern, '[filtered]', text)
    return text.strip()


def _format_history(history: list[dict]) -> str:
    """Format conversation history for prompt injection.

    Args:
        history: List of {"role": "customer"|"assistant", "text": "..."} dicts.

    Returns:
        Formatted string, or empty string if no history.
    """
    if not history:
        return ""
    lines = ["最近的对话记录："]
    for turn in history[-3:]:  # sliding window: last 3 turns only
        role = "客户" if turn.get("role") == "customer" else "客服"
        text = turn.get("text", "")
        # Sanitize customer messages to prevent prompt injection via history
        if turn.get("role") == "customer":
            text = _sanitize_message(text)
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def build_prompt(
    question: str,
    context_chunks: list[str],
    history: Optional[list[dict]] = None,
) -> list[dict]:
    """Build LLM chat messages with optional conversation history.

    Args:
        question: Current (sanitized) customer message.
        context_chunks: Retrieved knowledge chunks.
        history: Prior conversation turns (from ContextStore.get_recent).

    Returns:
        List of message dicts for LLM chat completion.
    """
    safe_question = _sanitize_message(question)
    context = "\n\n---\n\n".join(context_chunks)
    conv_history = _format_history(history or [])
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(
            conversation_history=conv_history,
            context=context,
        )},
        {"role": "user", "content": safe_question},
    ]


async def generate_reply(
    question: str,
    context_chunks: list[str],
    history: Optional[list[dict]] = None,
) -> str:
    """Generate a reply using DeepSeek with knowledge context and history."""
    messages = build_prompt(question, context_chunks, history=history)
    reply = await chat(messages, temperature=0.3, max_tokens=512)
    return reply.strip()
