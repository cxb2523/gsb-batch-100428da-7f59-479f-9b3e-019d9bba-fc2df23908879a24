# -*- coding: utf-8 -*-
"""测试夹具：在导入 crawler_batch 前为 akshare 打桩，并把网络请求替换为可控桩。

不依赖真实 token、真实接口或交易日历，crawl_date 可离线反复运行。
"""
import sys
import types
from pathlib import Path

# 让测试直接导入仓库根目录下的 crawler_batch
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# akshare 只在获取交易日时用到；缺少该依赖时 crawler_batch 也能导入，
# 这里额外打桩，保证测试在任意环境都能运行
if "akshare" not in sys.modules:
    _ak = types.ModuleType("akshare")
    _ak.tool_trade_date_hist_sina = lambda *a, **k: None
    sys.modules["akshare"] = _ak

import crawler_batch as cb  # noqa: E402


class FakeResponse:
    def __init__(self, text):
        self.text = text


def body_params(body):
    """把 form-urlencoded 请求体解析成 dict。"""
    params = {}
    for part in (body or "").split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k] = v
    return params


class FakeApi:
    """按接口名分发响应。

    routes: 接口名(a) -> 字符串 / 可调用对象(body) -> 字符串
    failing: 可选的判定函数 fn(api, params) -> 异常信息或 None；
             返回非空即模拟该次请求失败（重试耗尽由被测代码决定）。
    """

    def __init__(self, routes, failing=None):
        self.routes = routes
        self.failing = failing
        self.calls = []  # [(api, params), ...]

    def post(self, url, data=None, headers=None, timeout=None):
        params = body_params(data)
        api = params.get("a")
        self.calls.append((api, params))

        if self.failing:
            err = self.failing(api, params)
            if err:
                raise RuntimeError(err)

        handler = self.routes.get(api)
        if handler is None:
            raise AssertionError(f"未配置的接口: {api}")
        text = handler(params) if callable(handler) else handler
        return FakeResponse(text)

    def count(self, api, **match):
        """统计某接口（可加参数过滤）的请求次数。"""
        return sum(1 for name, params in self.calls
                   if name == api and all(params.get(k) == v for k, v in match.items()))


def install(monkeypatch, tmp_path, fake_api):
    """把 crawl_date 依赖的数据目录、网络、等待全部替换成可控实现。"""
    monkeypatch.setattr(cb, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cb.requests, "post", fake_api.post)
    monkeypatch.setattr(cb.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(cb.random, "uniform", lambda *a, **k: 0.0)
    return fake_api
