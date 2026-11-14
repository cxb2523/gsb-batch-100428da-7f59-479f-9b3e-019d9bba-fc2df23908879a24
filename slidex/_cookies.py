"""Cookie 注入与快照：按目标 URL 选域，避免同名跨域互相覆盖。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse


# 二级公共后缀（SLD.ccTLD）两段式判断：
# - _PUBLIC_SUFFIX_PAIRS：显式枚举的两段公共后缀（example.com.cn 的注册域是
#   example.com.cn，不是 com.cn）。
# - _SLD_LABELS：通用 SLD 前缀。仅当末段是两字母 ccTLD 且倒数第二段命中时，
#   注册域同样取三段——覆盖 edu.cn / ac.cn / co.jp / com.tw / co.uk 这类
#   枚举不完的组合。单标签绝不直接匹配：example.com 的注册域仍是 example.com。
_PUBLIC_SUFFIX_PAIRS = {
    "com.cn",
    "net.cn",
    "org.cn",
    "gov.cn",
    "edu.cn",
    "ac.cn",
}

_SLD_LABELS = {
    "com",
    "net",
    "org",
    "edu",
    "gov",
    "mil",
    "int",
    "ac",
    "co",
    "biz",
    "info",
}


def cookie_domain_for_url(url: str, fallback: str = ".goofish.com") -> str:
    """从 verify_url 推导 Playwright add_cookies 用的 Domain（带前导点）。"""
    if not isinstance(url, str):
        url = ""
    host = (urlparse(url or "").hostname or "").lower().strip(".")
    if not host or host in {"localhost"} or host.replace(".", "").isdigit():
        return fallback if fallback.startswith(".") else f".{fallback}"
    labels = host.split(".")
    if len(labels) == 1:
        return f".{host}"
    # example.com.cn / login.taobao.com → .example.com.cn / .taobao.com
    if len(labels) >= 3:
        last_two = ".".join(labels[-2:])
        sld_under_cctld = len(labels[-1]) == 2 and labels[-2] in _SLD_LABELS
        if last_two in _PUBLIC_SUFFIX_PAIRS or sld_under_cctld:
            return "." + ".".join(labels[-3:])
    return "." + ".".join(labels[-2:])


def parse_cookie_header(cookies_str: str, default_domain: str) -> List[Dict[str, str]]:
    """解析 `a=1; b=2` 头。条目可带 `Domain=` 覆盖默认域。"""
    if not cookies_str or not str(cookies_str).strip():
        return []
    default_domain = default_domain if default_domain.startswith(".") else f".{default_domain}"
    out: List[Dict[str, str]] = []
    for part in str(cookies_str).split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered in {"domain", "path", "expires", "max-age", "secure", "httponly", "samesite"}:
            if out and lowered == "domain" and value:
                domain = value if value.startswith(".") else f".{value}"
                out[-1]["domain"] = domain
            continue
        out.append({"name": name, "value": value, "domain": default_domain, "path": "/"})
    return out


def _host_matches_cookie_domain(host: str, domain: str) -> bool:
    host = (host or "").lower().lstrip(".")
    domain = (domain or "").lower().lstrip(".")
    if not host or not domain:
        return False
    return host == domain or host.endswith("." + domain)


def _cookie_specificity(cookie: Dict[str, Any]) -> Tuple[int, int]:
    domain = str(cookie.get("domain") or "")
    path = str(cookie.get("path") or "/")
    return (len(domain.lstrip(".")), len(path))


def select_cookies_for_url(
    cookies: Iterable[Dict[str, Any]],
    url: str,
) -> Dict[str, str]:
    """把多域 cookie jar 压成 name→value，只保留匹配 url 的条目；同名取更具体的域/路径。"""
    if not isinstance(url, str):
        url = ""
    host = (urlparse(url or "").hostname or "").lower()
    selected: Dict[str, Tuple[Tuple[int, int], str]] = {}
    unmatched: Dict[str, Tuple[Tuple[int, int], str]] = {}
    for cookie in cookies:
        name = cookie.get("name")
        if not name:
            continue
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "")
        spec = _cookie_specificity(cookie)
        if host:
            matches = (not domain) or _host_matches_cookie_domain(host, domain)
            bucket = selected if matches else unmatched
        else:
            bucket = unmatched
        prev = bucket.get(name)
        if prev is None or spec >= prev[0]:
            bucket[name] = (spec, value)
    if host:
        return {name: pair[1] for name, pair in selected.items()}
    # 无目标 URL 时退回“最具体的同名”，避免 CDP getAllCookies 后写覆盖
    merged = dict(unmatched)
    merged.update(selected)
    return {name: pair[1] for name, pair in merged.items()}
