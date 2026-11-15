"""Tests for cumulative, fault-tolerant pagination in ArxivRepo.fetch_papers
and the way partial failures are surfaced through the use case and Flask route.
"""

import json
from unittest import mock

import pytest
import responses

from webminer.entities.arxiv_document import ArxivDocument
from webminer.external_interfaces.flask_server.app import create_app
from webminer.external_interfaces.flask_server.settings import TestConfig
from webminer.interface_adapters import process_arxiv_request as uc
from webminer.interface_adapters.rest_adapters import request_objects as req
from webminer.use_cases.request_arxiv import arxiv_repo as a_repo


def _entry_xml(rawid, version, title=None):
    """Build a minimal Atom <entry> that feedparser can parse."""

    title = title or f"title {rawid}"
    return f"""
  <entry>
    <id>http://arxiv.org/abs/{rawid}v{version}</id>
    <updated>2018-09-20T17:48:27Z</updated>
    <published>2018-09-20T17:48:27Z</published>
    <title>{title}</title>
    <summary>abstract for {rawid}</summary>
    <author><name>Author {rawid}</name></author>
    <link href="http://arxiv.org/abs/{rawid}v{version}" rel="alternate"
          type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/{rawid}v{version}"
          rel="related" type="application/pdf"/>
  </entry>"""


def _feed_xml(entries):
    body = "".join(entries)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"{body}</feed>"
    )


def _expected_paper(rawid, version):
    return {
        "publish_date": "2018-09-20T17:48:27Z",
        "authors": [f"Author {rawid}"],
        "title": f"title {rawid}",
        "abstract": f"abstract for {rawid}",
        "id": f"http://arxiv.org/abs/{rawid}v{version}",
        "link": f"http://arxiv.org/abs/{rawid}v{version}",
        "pdf": f"http://arxiv.org/pdf/{rawid}v{version}",
        "_rawid": rawid,
        "_version": version,
    }


def _query_url(start, max_results):
    return (
        f"{a_repo.ArxivRepo.base_url}search_query="
        f"{a_repo.ArxivRepo.default_search_query}"
        f"&sortBy=lastUpdatedDate&start={start}&max_results={max_results}"
    )


@responses.activate
def test_fetch_papers_accumulates_all_pages_instead_of_only_last():
    responses.add(
        responses.GET,
        _query_url(0, 10),
        body=_feed_xml([_entry_xml("1801.00001", 1), _entry_xml("1801.00002", 1)]),
        status=200,
    )
    responses.add(
        responses.GET,
        _query_url(10, 10),
        body=_feed_xml([_entry_xml("1801.00003", 2)]),
        status=200,
    )
    responses.add(
        responses.GET,
        _query_url(20, 10),
        body=_feed_xml([_entry_xml("1801.00004", 1), _entry_xml("1801.00005", 1)]),
        status=200,
    )

    repo = a_repo.ArxivRepo()
    papers = repo.fetch_papers(start_index=0, max_index=30, results_per_iteration=10)

    assert len(papers) == 5
    assert [p["_rawid"] for p in papers] == [
        "1801.00001",
        "1801.00002",
        "1801.00003",
        "1801.00004",
        "1801.00005",
    ]
    assert papers[0] == _expected_paper("1801.00001", 1)
    assert papers[2]["_version"] == 2
    assert repo.fetch_errors == []


@responses.activate
def test_fetch_papers_keeps_successful_pages_when_one_page_request_raises():
    responses.add(
        responses.GET,
        _query_url(0, 10),
        body=_feed_xml([_entry_xml("1801.00001", 1)]),
        status=200,
    )
    responses.add(
        responses.GET,
        _query_url(10, 10),
        body=responses.ConnectionError("boom"),
    )
    responses.add(
        responses.GET,
        _query_url(20, 10),
        body=_feed_xml([_entry_xml("1801.00003", 1)]),
        status=200,
    )

    repo = a_repo.ArxivRepo()
    papers = repo.fetch_papers(start_index=0, max_index=30, results_per_iteration=10)

    assert [p["_rawid"] for p in papers] == ["1801.00001", "1801.00003"]
    assert len(repo.fetch_errors) == 1
    assert repo.fetch_errors[0]["start_index"] == 10
    assert "ConnectionError" in repo.fetch_errors[0]["error"]


@responses.activate
def test_fetch_papers_records_page_without_entries_and_continues():
    responses.add(
        responses.GET,
        _query_url(0, 10),
        body=_feed_xml([_entry_xml("1801.00001", 1)]),
        status=200,
    )
    responses.add(responses.GET, _query_url(10, 10), body=_feed_xml([]), status=200)
    responses.add(
        responses.GET,
        _query_url(20, 10),
        body=_feed_xml([_entry_xml("1801.00003", 1)]),
        status=200,
    )

    repo = a_repo.ArxivRepo()
    papers = repo.fetch_papers(start_index=0, max_index=30, results_per_iteration=10)

    assert [p["_rawid"] for p in papers] == ["1801.00001", "1801.00003"]
    assert [failure["start_index"] for failure in repo.fetch_errors] == [10]
    assert "no entries" in repo.fetch_errors[0]["error"]


@responses.activate
def test_fetch_papers_raises_when_every_page_fails():
    responses.add(responses.GET, _query_url(0, 10), body=responses.ConnectionError("x"))
    responses.add(
        responses.GET, _query_url(10, 10), body=responses.ConnectionError("y")
    )

    repo = a_repo.ArxivRepo()
    with pytest.raises(RuntimeError):
        repo.fetch_papers(start_index=0, max_index=20, results_per_iteration=10)

    assert {failure["start_index"] for failure in repo.fetch_errors} == {0, 10}


@responses.activate
def test_fetch_papers_returns_empty_without_error_when_no_pages_requested():
    repo = a_repo.ArxivRepo()
    assert repo.fetch_papers(start_index=10, max_index=10) == []
    assert repo.fetch_errors == []


@responses.activate
def test_filters_apply_to_papers_accumulated_from_every_page():
    responses.add(
        responses.GET,
        _query_url(0, 10),
        body=_feed_xml(
            [
                _entry_xml("1801.00001", 1, title="alpha paper"),
                _entry_xml("1801.00002", 1, title="beta paper"),
            ]
        ),
        status=200,
    )
    responses.add(
        responses.GET,
        _query_url(10, 10),
        body=_feed_xml(
            [
                _entry_xml("1801.00003", 1, title="alpha sequel"),
                _entry_xml("1801.00004", 1, title="gamma paper"),
            ]
        ),
        status=200,
    )

    repo = a_repo.ArxivRepo()
    # list() calls fetch_papers() with its default page size; force small
    # pages so the accumulation spans multiple requests.
    repo.fetch_papers = lambda *a, **kw: a_repo.ArxivRepo.fetch_papers(
        repo, start_index=0, max_index=20, results_per_iteration=10
    )
    filtered = repo.list(filters={"title__eq": "alpha sequel"})

    assert len(filtered) == 1
    assert isinstance(filtered[0], ArxivDocument)
    assert filtered[0].title == "alpha sequel"

    alpha_like = repo.list(filters={"title__lt": "b"})
    assert {doc.title for doc in alpha_like} == {"alpha paper", "alpha sequel"}


@responses.activate
def test_use_case_survives_partial_page_failure_and_reports_them():
    responses.add(
        responses.GET,
        _query_url(0, 10),
        body=_feed_xml([_entry_xml("1801.00001", 1)]),
        status=200,
    )
    responses.add(
        responses.GET, _query_url(10, 10), body=responses.ConnectionError("x")
    )

    repo = a_repo.ArxivRepo()
    repo.fetch_papers = lambda *a, **kw: a_repo.ArxivRepo.fetch_papers(
        repo, start_index=0, max_index=20, results_per_iteration=10
    )
    use_case = uc.ProcessArxivDocuments(repo)
    request_object = req.ArxivDocumentListRequestObject.from_dict(
        {"filters": {"title__eq": "title 1801.00001"}}
    )

    response = use_case.execute(request_object)

    assert bool(response) is True
    assert response.type == "SUCCESS"
    assert len(response.value) == 1
    assert response.fetch_errors == repo.fetch_errors
    assert response.fetch_errors[0]["start_index"] == 10


def test_use_case_response_has_no_failure_metadata_when_all_pages_ok():
    repo = mock.Mock()
    repo.list.return_value = [
        ArxivDocument(
            doc_id="url_1",
            url="url_1",
            title="title_1",
            abstract="abstract_1",
            authors=["author1"],
            publish_date="publish_date_1",
            pdf_url="pdf_url_1",
        )
    ]
    repo.fetch_errors = []

    use_case = uc.ProcessArxivDocuments(repo)
    response = use_case.execute(req.ArxivDocumentListRequestObject.from_dict({}))

    assert bool(response) is True
    assert not hasattr(response, "fetch_errors")


@responses.activate
def test_flask_route_returns_papers_and_failed_page_header_with_200():
    responses.add(
        responses.GET,
        _query_url(0, 10),
        body=_feed_xml([_entry_xml("1801.00001", 1)]),
        status=200,
    )
    responses.add(
        responses.GET, _query_url(10, 10), body=responses.ConnectionError("x")
    )

    original_fetch = a_repo.ArxivRepo.fetch_papers

    def small_page_fetch(self, *args, **kwargs):
        kwargs.update(start_index=0, max_index=20, results_per_iteration=10)
        return original_fetch(self, *args, **kwargs)

    app = create_app(TestConfig)
    client = app.test_client()
    with mock.patch.object(a_repo.ArxivRepo, "fetch_papers", small_page_fetch):
        http_response = client.get("/arxiv?filter_title__eq=title%201801.00001")

    assert http_response.status_code == 200
    payload = json.loads(http_response.get_data(as_text=True))
    assert len(payload) == 1
    assert payload[0]["title"] == "title 1801.00001"

    failed_pages = json.loads(http_response.headers["X-Arxiv-Failed-Pages"])
    assert failed_pages[0]["start_index"] == 10


@responses.activate
def test_flask_route_returns_500_when_all_pages_fail():
    responses.add(responses.GET, _query_url(0, 10), body=responses.ConnectionError("x"))

    original_fetch = a_repo.ArxivRepo.fetch_papers

    def small_page_fetch(self, *args, **kwargs):
        kwargs.update(start_index=0, max_index=10, results_per_iteration=10)
        return original_fetch(self, *args, **kwargs)

    app = create_app(TestConfig)
    client = app.test_client()
    with mock.patch.object(a_repo.ArxivRepo, "fetch_papers", small_page_fetch):
        http_response = client.get("/arxiv?filter_title__eq=anything")

    assert http_response.status_code == 500
    payload = json.loads(http_response.get_data(as_text=True))
    assert payload["type"] == "SYSTEM_ERROR"
    assert "all arxiv pages failed" in payload["message"]
