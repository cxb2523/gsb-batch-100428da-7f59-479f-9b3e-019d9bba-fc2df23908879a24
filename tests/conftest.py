from pathlib import Path

import httpx
import pytest

from gmaps_extractor import client as client_module

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch):
    for name in ("GMAPS_PROXY_URL", "GMAPS_PROXY_HOST", "GMAPS_PROXY_USER", "GMAPS_PROXY_PASS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail any test that would open a real connection."""

    def refuse(self, request):
        raise AssertionError(f"unexpected network request to {request.url.host}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    monkeypatch.setattr(client_module, "RETRY_WAIT_SECONDS", 0)


class Recorder:
    """An httpx MockTransport handler that serves queued answers and records requests."""

    def __init__(self, answers=None, default=None):
        self.answers = list(answers or [])
        self.default = default
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/maps" and request.url.host == "www.google.com":
            return httpx.Response(200, text="<html>maps</html>", request=request)
        if self.answers:
            answer = self.answers.pop(0)
        elif self.default is not None:
            answer = self.default
        else:
            raise AssertionError(f"no answer queued for {request.url}")
        if isinstance(answer, httpx.Response):
            return answer
        status, text = answer
        return httpx.Response(status, text=text, request=request)

    def api_requests(self):
        return [r for r in self.requests if r.url.path != "/maps"]


def make_client(recorder: Recorder, **kwargs) -> client_module.GMapsClient:
    return client_module.GMapsClient(transport=httpx.MockTransport(recorder), min_interval=0, **kwargs)
