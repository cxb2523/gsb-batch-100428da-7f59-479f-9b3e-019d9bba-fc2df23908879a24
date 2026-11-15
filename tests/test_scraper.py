import socket

import pytest
import requests

from search_engine.exceptions import (
    FetchError,
    RateLimitError,
    ResponseRejectedError,
    UnsafeURLError,
)
from search_engine.scraper import HttpFetcher, URLPolicy


def resolver_for(address: str):
    def resolve(host: str, port: int, *, type: int):
        del host
        return [(socket.AF_INET, type, 6, "", (address, port))]

    return resolve


def response(status: int, content: bytes = b"", **headers: str) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers)
    result._content = content
    result._content_consumed = True
    result.encoding = "utf-8"
    result.url = "https://example.com/"
    return result


def test_url_policy_rejects_private_dns_target() -> None:
    policy = URLPolicy(resolver=resolver_for("127.0.0.1"))

    with pytest.raises(UnsafeURLError, match="non-public"):
        policy.validate("https://example.com/")


def test_url_policy_accepts_public_dns_target() -> None:
    policy = URLPolicy(resolver=resolver_for("93.184.216.34"))

    policy.validate("https://example.com/")


def test_fetcher_enforces_streamed_response_limit() -> None:
    session = requests.Session()
    session.get = lambda *args, **kwargs: response(  # type: ignore[method-assign]
        200, b"123456", **{"Content-Type": "text/html"}
    )
    fetcher = HttpFetcher(
        session=session,
        url_policy=URLPolicy(allow_private_network=True),
        max_response_bytes=5,
    )

    with pytest.raises(FetchError, match="size limit"):
        fetcher.fetch("https://example.com/")


def test_fetcher_detects_encoding_when_header_omits_charset() -> None:
    session = requests.Session()
    payload = "What\u2019s New \u00b6".encode()
    encoded_response = response(200, payload, **{"Content-Type": "text/html"})
    encoded_response.encoding = "ISO-8859-1"
    session.get = lambda *args, **kwargs: encoded_response  # type: ignore[method-assign]
    fetcher = HttpFetcher(
        session=session,
        url_policy=URLPolicy(allow_private_network=True),
    )

    page = fetcher.fetch("https://example.com/")

    assert page.text == "What\u2019s New \u00b6"


def test_fetcher_validates_redirect_target_before_following() -> None:
    calls = []
    session = requests.Session()

    def get(url: str, **kwargs):
        calls.append((url, kwargs))
        return response(302, Location="http://internal.test/admin")

    session.get = get  # type: ignore[method-assign]

    def resolver(host: str, port: int, *, type: int):
        address = "93.184.216.34" if host == "example.com" else "10.0.0.5"
        return [(socket.AF_INET, type, 6, "", (address, port))]

    fetcher = HttpFetcher(session=session, url_policy=URLPolicy(resolver=resolver))

    with pytest.raises(UnsafeURLError):
        fetcher.fetch("https://example.com/")
    assert len(calls) == 1


def test_fetcher_flags_redirect_on_returned_page() -> None:
    session = requests.Session()
    responses = [
        response(302, Location="https://example.com/final"),
        response(200, b"<p>ok</p>", **{"Content-Type": "text/html"}),
    ]
    session.get = lambda *args, **kwargs: responses.pop(0)  # type: ignore[method-assign]
    fetcher = HttpFetcher(
        session=session,
        url_policy=URLPolicy(allow_private_network=True),
    )

    page = fetcher.fetch("https://example.com/start")

    assert page.url == "https://example.com/final"
    assert page.redirected is True


def test_fetcher_marks_unredirected_page_as_not_redirected() -> None:
    session = requests.Session()
    session.get = lambda *args, **kwargs: response(  # type: ignore[method-assign]
        200, b"<p>ok</p>", **{"Content-Type": "text/html"}
    )
    fetcher = HttpFetcher(
        session=session,
        url_policy=URLPolicy(allow_private_network=True),
    )

    page = fetcher.fetch("https://example.com/")

    assert page.redirected is False


def test_fetcher_rejects_unsupported_content_type_with_typed_error() -> None:
    session = requests.Session()
    session.get = lambda *args, **kwargs: response(  # type: ignore[method-assign]
        200, b"binary", **{"Content-Type": "application/pdf"}
    )
    fetcher = HttpFetcher(
        session=session,
        url_policy=URLPolicy(allow_private_network=True),
    )

    with pytest.raises(ResponseRejectedError, match="Unsupported content type"):
        fetcher.fetch("https://example.com/file.pdf")


def test_fetcher_maps_exhausted_retries_to_rate_limit_error() -> None:
    session = requests.Session()

    def raise_retry_error(*args, **kwargs):
        raise requests.exceptions.RetryError("too many 429 error responses")

    session.get = raise_retry_error  # type: ignore[method-assign]
    fetcher = HttpFetcher(
        session=session,
        url_policy=URLPolicy(allow_private_network=True),
    )

    with pytest.raises(RateLimitError, match="Retries exhausted"):
        fetcher.fetch("https://example.com/")
