import json

import pytest
from typer.testing import CliRunner

from njsparser.cli import app

runner = CliRunner()

DOMAIN = "https://example.com"
URL = f"{DOMAIN}/"
BUILD_ID = "test-build-id"

NEXTJS_HTML = f"""<html>
<head>
<link rel="stylesheet" href="/_next/static/css/abc123.css"/>
</head>
<body>
<script id="__NEXT_DATA__" type="application/json">
{{"props": {{"pageProps": {{}}}}, "buildId": "{BUILD_ID}"}}
</script>
</body>
</html>"""

NOT_NEXTJS_HTML = "<html><head><title>hi</title></head><body>hello</body></html>"

NO_BUILD_ID_HTML = """<html>
<body>
<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>
</body>
</html>"""

BUILD_MANIFEST_JS = (
    'self.__BUILD_MANIFEST = {sortedPages:["/","/about","/api/hello"]};'
)


class FakeResponse:
    def __init__(self, status_code=200, text="", url=None, headers=None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {}


def make_fake_get(routes: dict):
    """Returns a fake `requests.get` resolving urls from the given
    `{path_or_url: FakeResponse}` routes dict."""
    def fake_get(*args, **kwargs):
        url = kwargs.get("url") if "url" in kwargs else args[0]
        for route, response in routes.items():
            if url == route or url == f"{DOMAIN}{route}":
                if response.url is None:
                    response.url = url
                return response
        raise AssertionError(f"unexpected request to {url}")
    return fake_get


@pytest.fixture
def success_routes():
    return {
        "/": FakeResponse(text=NEXTJS_HTML),
        f"/_next/data/{BUILD_ID}/index.json": FakeResponse(
            status_code=404, headers={"Content-Type": "text/html"}
        ),
        f"/_next/static/{BUILD_ID}/_buildManifest.js": FakeResponse(
            text=BUILD_MANIFEST_JS
        ),
    }


def run_analyze(monkeypatch, routes, *args):
    monkeypatch.setattr("njsparser.cli.requests.get", make_fake_get(routes))
    return runner.invoke(app, list(args))


def read_result(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def test_analyze_json_success(monkeypatch, tmp_path, success_routes):
    json_path = tmp_path / "result.json"
    result = run_analyze(
        monkeypatch, success_routes, URL, "--json-output", str(json_path)
    )
    assert result.exit_code == 0, result.output
    data = read_result(json_path)
    assert data["outcome"] == "success"
    assert data["reason"]
    assert data["url"] == URL
    assert data["final_url"] == URL
    assert data["is_nextjs"] is True
    assert data["build_id"] == BUILD_ID
    assert data["base_path"] == ""
    assert data["has_next_data"] is True
    assert data["has_flight_data"] is False
    assert data["is_api_exposed"] is False
    assert data["pages"] == [
        f"{DOMAIN}/",
        f"{DOMAIN}/about",
        f"{DOMAIN}/api/hello",
    ]
    assert data["api_paths"] == []


def test_analyze_json_success_force_api(monkeypatch, tmp_path, success_routes):
    """`force_api` lists the api paths, but must not rewrite
    `is_api_exposed` to True."""
    json_path = tmp_path / "result.json"
    result = run_analyze(
        monkeypatch,
        success_routes,
        URL,
        "--force-api",
        "--json-output",
        str(json_path),
    )
    assert result.exit_code == 0, result.output
    data = read_result(json_path)
    assert data["outcome"] == "success"
    assert data["is_api_exposed"] is False
    assert data["api_paths"] == [
        f"{DOMAIN}/_next/data/{BUILD_ID}/index.json",
        f"{DOMAIN}/_next/data/{BUILD_ID}/about.json",
        f"{DOMAIN}/_next/data/{BUILD_ID}/api/hello.json",
    ]


def test_analyze_json_not_nextjs(monkeypatch, tmp_path):
    json_path = tmp_path / "result.json"
    routes = {"/": FakeResponse(text=NOT_NEXTJS_HTML)}
    result = run_analyze(monkeypatch, routes, URL, "--json-output", str(json_path))
    assert result.exit_code != 0
    data = read_result(json_path)
    assert data["outcome"] == "not_nextjs"
    assert "next" in data["reason"].lower()
    assert data["url"] == URL
    assert data["final_url"] == URL
    assert data["is_nextjs"] is False
    assert data["build_id"] is None
    assert data["pages"] == []
    assert data["api_paths"] == []


def test_analyze_json_no_build_id(monkeypatch, tmp_path):
    json_path = tmp_path / "result.json"
    routes = {"/": FakeResponse(text=NO_BUILD_ID_HTML)}
    result = run_analyze(monkeypatch, routes, URL, "--json-output", str(json_path))
    assert result.exit_code != 0
    data = read_result(json_path)
    assert data["outcome"] == "no_build_id"
    assert "build id" in data["reason"].lower()
    # Information determined before the abort is preserved.
    assert data["final_url"] == URL
    assert data["is_nextjs"] is True
    assert data["build_id"] is None


def test_analyze_json_main_page_500(monkeypatch, tmp_path):
    json_path = tmp_path / "result.json"
    routes = {"/": FakeResponse(status_code=500, text="Internal Server Error")}
    result = run_analyze(monkeypatch, routes, URL, "--json-output", str(json_path))
    assert result.exit_code != 0
    data = read_result(json_path)
    assert data["outcome"] == "main_page_error"
    assert "500" in data["reason"]
    assert data["url"] == URL
    assert data["final_url"] == URL
    assert data["is_nextjs"] is None
    assert data["build_id"] is None


def test_analyze_without_json_output_writes_nothing(
    monkeypatch, tmp_path, success_routes
):
    """Without `--json-output`, the behavior is unchanged and no file
    is created."""
    json_path = tmp_path / "result.json"
    result = run_analyze(monkeypatch, success_routes, URL)
    assert result.exit_code == 0, result.output
    assert not json_path.exists()
