# 网络请求的有限次重试工具。
# 只对网络层的临时性失败（超时、连接错误等 requests.RequestException）重试；
# 页面结构不符、字段缺失这类确定性错误由调用方直接抛出，不进入重试。
import logging
from time import sleep

from requests import Session
from requests.exceptions import RequestException

from .SSO_error import SSONetworkError

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 0.5


def get_with_retry(session: Session, url: str, description: str,
                   max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                   retry_delay: float = DEFAULT_RETRY_DELAY):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return session.get(url)
        except RequestException as e:
            last_error = e
            logging.warning(f"{description}失败（第 {attempt}/{max_attempts} 次）：{e}")
            if attempt < max_attempts:
                sleep(retry_delay)
    raise SSONetworkError(description, max_attempts, last_error)
