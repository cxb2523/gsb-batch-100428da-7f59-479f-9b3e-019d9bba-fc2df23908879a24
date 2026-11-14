import json
import logging
import time

import requests

import config

log = logging.getLogger("wxbot")

# 临时性故障（限流/超时/服务端错误）：按退避重试，重试耗尽后抛 retryable=True 的 SearchError
RETRYABLE_STATUS = (408, 429, 500, 502, 503, 504)


class SearchError(RuntimeError):
    """搜索失败。

    retryable=True：临时性故障（408/429/5xx、超时、连接错误），稍后可重试；
    retryable=False：请求本身有问题（4xx 等），重试无意义。
    """

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class DeepSeekSearch:
    """DeepSeek 官方 Search / Fetch API（和聊天共用同一个 key）。"""

    def __init__(self, base_url=None, api_key=None, timeout=None):
        self.base_url = (
            base_url
            or getattr(config, "SEARCH_BASE_URL", "")
            or config.LLM_BASE_URL
        ).rstrip("/")
        self.api_key = api_key or getattr(config, "SEARCH_API_KEY", "") or config.LLM_API_KEY
        self.timeout = float(
            timeout or getattr(config, "SEARCH_TIMEOUT", 20) or 20
        )
        self.cache_seconds = float(getattr(config, "SEARCH_CACHE_SECONDS", 600) or 0)
        self._cache = {}
        self.session = requests.Session()

    @property
    def available(self):
        return bool(self.api_key)

    def _post(self, path, payload, retries=2):
        if not self.api_key:
            raise SearchError("没有可用的 DeepSeek API Key")
        url = self.base_url + path
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.post(
                    url,
                    headers=headers,
                    data=body,
                    timeout=self.timeout,
                )
            except (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ) as exc:
                # 超时/连接错误：临时性故障，走退避重试
                last_error = SearchError("网络错误：%s" % exc, retryable=True)
            except Exception as exc:  # noqa: BLE001
                raise SearchError("搜索请求异常：%s" % exc, retryable=False) from exc
            else:
                if resp.status_code in RETRYABLE_STATUS:
                    last_error = SearchError(
                        "HTTP %s（临时故障，可重试）" % resp.status_code,
                        retryable=True,
                    )
                elif resp.status_code != 200:
                    # 4xx 等不可重试错误：立即抛出，不重试
                    raise SearchError(
                        "HTTP %s：%s" % (resp.status_code, resp.text[:200]),
                        retryable=False,
                    )
                else:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise SearchError(
                            "响应不是合法 JSON：%s" % exc, retryable=False
                        ) from exc
            if attempt < retries:
                wait = 1.0 * (attempt + 1)
                log.warning(
                    "搜索请求失败（%s），%.1f 秒后重试（第 %d/%d 次）",
                    last_error,
                    wait,
                    attempt + 1,
                    retries,
                )
                time.sleep(wait)
        raise SearchError(
            "搜索请求失败（已重试 %d 次）：%s" % (retries, last_error),
            retryable=last_error.retryable,
        )

    def search(self, query, count=6, ori_question="", need_content=False):
        """联网搜索，返回结果列表。

        空列表表示“搜过但没有结果”，不是异常，调用方用 `results == []` 区分；
        搜索失败抛 SearchError（retryable 标记是否值得稍后重试）。
        """
        query = (query or "").strip()
        if not query:
            raise SearchError("搜索词为空")
        query = query[:128]
        count = max(1, min(8, int(count or 6)))
        key = ("search", query, count, bool(need_content), (ori_question or "")[:128])
        cached = self._cache.get(key)
        if cached and self.cache_seconds > 0:
            age = time.time() - cached[0]
            if age <= self.cache_seconds:
                log.info("联网搜索（缓存命中）：%s -> %d 条", query[:60], len(cached[1]))
                return [dict(item) for item in cached[1]]

        payload = {"query": query, "count": count, "need_content": bool(need_content)}
        if ori_question:
            payload["ori_question"] = (ori_question or "")[:128]
        data = self._post("/tools/search", payload)
        results = []
        for item in data.get("results") or []:
            results.append(
                {
                    "title": (item.get("title") or "").strip(),
                    "url": (item.get("url") or "").strip(),
                    "snippet": (item.get("snippet") or "").strip(),
                    "summary": (item.get("summary") or "").strip()[:1600],
                    "date": item.get("date") or "",
                    "site_name": item.get("site_name") or "",
                    "authority": item.get("authority"),
                    "content": (item.get("content") or "").strip()[:8000],
                }
            )
        if self.cache_seconds > 0:
            self._cache[key] = (time.time(), [dict(item) for item in results])
        if results:
            log.info("联网搜索（实时请求）：%s -> %d 条", query[:60], len(results))
        else:
            log.info("联网搜索（实时请求）：%s -> 0 条（搜过但没有结果）", query[:60])
        return results

    def fetch(self, url):
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise SearchError("只能抓 http/https 链接")
        data = self._post("/tools/fetch", {"url": url})
        return {
            "url": url,
            "title": (data.get("title") or "").strip(),
            "content": (data.get("content") or "").strip(),
        }
