# rag/llm_client.py
"""DeepSeek API chat wrapper with simple retry."""

import os
import asyncio
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None
MAX_RETRIES = 2


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY 环境变量未设置。"
                "请在 .env 文件中添加: DEEPSEEK_API_KEY=your_key"
            )
        _client = AsyncOpenAI(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            timeout=60.0,
        )
    return _client


async def chat(messages: list[dict], temperature: float = 0.7,
               max_tokens: int = 1024) -> str:
    """Send chat completion to DeepSeek API with retry.

    Returns the reply text.
    Raises RuntimeError if all retries fail.
    """
    client = get_client()
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            last_error = e
            # 4xx 不可重试（除 429 RateLimit）
            status = getattr(e, 'status_code', None)
            if status is not None and 400 <= status < 500 and status != 429:
                logger.error(f"DeepSeek API 不可重试错误 {status}: {e}")
                raise RuntimeError(f"DeepSeek API error {status}: {e}") from e
            logger.warning(
                f"DeepSeek API attempt {attempt + 1}/{MAX_RETRIES + 1} failed: {e}"
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(1.0 * (attempt + 1))

    raise RuntimeError(
        f"DeepSeek API failed after {MAX_RETRIES + 1} attempts: {last_error}"
    )
