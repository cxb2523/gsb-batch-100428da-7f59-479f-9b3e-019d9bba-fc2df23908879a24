import json
from typing import Optional

import requests
import typer
from rich.console import Console

from njsparser import tools, utils, parser, api

app = typer.Typer()
console = Console()

# Possible values of the `"outcome"` field of the JSON result file.
OUTCOME_SUCCESS = "success"
OUTCOME_MAIN_PAGE_ERROR = "main_page_error"
OUTCOME_NOT_NEXTJS = "not_nextjs"
OUTCOME_NO_BUILD_ID = "no_build_id"
OUTCOME_NO_STATIC_PATH = "no_static_path"
OUTCOME_BUILD_MANIFEST_FETCH_FAILED = "build_manifest_fetch_failed"


def _new_result(url: str) -> dict:
    """Creates the dict that will be dumped as the JSON result file."""
    return {
        "url": url,
        "final_url": None,
        "is_nextjs": None,
        "build_id": None,
        "base_path": None,
        "has_flight_data": None,
        "has_next_data": None,
        "is_api_exposed": None,
        "pages": [],
        "api_paths": [],
        "outcome": None,
        "reason": None,
    }


def _write_json_result(path: str, result: dict):
    """Writes the analysis result dict as JSON to the given path."""
    with open(path, "w", encoding="utf-8") as file:
        json.dump(result, file, indent=2, ensure_ascii=False)
        file.write("\n")


@app.command()
def analyze(
    url: str = typer.Argument(
        ...,
        help="The url of the website to analyze (can contain a path).",
        show_default=False,
    ),
    force_api: bool = False,
    json_output: Optional[str] = typer.Option(
        None,
        "--json-output",
        help="Optional path of a JSON file where the analysis result will be "
        "written, whether the analysis succeeds or fails.",
        show_default=False,
    ),
):
    """Scan the given website to find any interesting exploitable data."""
    if not url or not url.strip():
        console.print("[red]Error: URL is required.[/red]")
        raise typer.Exit(2)
    result = _new_result(url=url)

    def abort(outcome: str, reason: str, assertion_message):
        """Records the failure in the result, writes the JSON file if a path
        was given, and aborts like the asserts used to."""
        result["outcome"] = outcome
        result["reason"] = reason
        if json_output is not None:
            _write_json_result(path=json_output, result=result)
        raise AssertionError(assertion_message)

    main_response = requests.get(url=url)
    result["final_url"] = main_response.url
    if main_response.status_code != 200:
        abort(
            outcome=OUTCOME_MAIN_PAGE_ERROR,
            reason=f"The main page returned status code "
            f"{main_response.status_code}, expected 200.",
            assertion_message=main_response.status_code,
        )
    domain = main_response.url.split("/", 3).pop(2)
    base_url = f"https://{domain}"
    value = utils.make_tree(value=main_response.text)
    result["is_nextjs"] = tools.has_nextjs(value=value)
    if result["is_nextjs"] is not True:
        abort(
            outcome=OUTCOME_NOT_NEXTJS,
            reason="The page doesn't contain any Next.js data.",
            assertion_message="page doesn't have nextjs",
        )
    result["build_id"] = build_id = tools.find_build_id(value=value)
    if build_id is None:
        abort(
            outcome=OUTCOME_NO_BUILD_ID,
            reason="Couldn't find any build id in the page.",
            assertion_message="can't find any build id",
        )
    print("Build Id:", build_id)
    result["base_path"] = base_path = parser.get_base_path(
        value=value, remove_domain=True
    )
    if base_path is None:
        abort(
            outcome=OUTCOME_NO_STATIC_PATH,
            reason="No `/_next/static/` path found in the page.",
            assertion_message="no next static url found",
        )
    result["has_flight_data"] = has_flight_data = parser.has_flight_data(value=value)
    if has_flight_data:
        print("The site contains flight data.")
    result["has_next_data"] = has_next_data = parser.has_next_data(value=value)
    if has_next_data:
        print("The site contains a __NEXT_DATA__ script.")
    index_api_path = api.get_index_api_path(build_id=build_id, base_path=base_path)
    is_api_exposed_response = requests.get(f"{base_url}{index_api_path}")
    if is_api_exposed_response.status_code == 307 and (
        nextjs_redirect := is_api_exposed_response.headers.get("X-Nextjs-Redirect")
    ):
        is_api_exposed_response = requests.get(
            f"{base_url}{nextjs_redirect}{index_api_path}"
        )
    is_api_exposed = api.is_api_exposed_from_response(
        status_code=is_api_exposed_response.status_code,
        content_type=is_api_exposed_response.headers.get("Content-Type"),
        text=is_api_exposed_response.text,
    )
    result["is_api_exposed"] = is_api_exposed
    build_manifest_path = parser.get_build_manifest_path(
        build_id=build_id, base_path=base_path
    )
    build_manifest_resp = requests.get(f"{base_url}{build_manifest_path}")
    if build_manifest_resp.status_code != 200:
        abort(
            outcome=OUTCOME_BUILD_MANIFEST_FETCH_FAILED,
            reason=f"The build manifest request returned status code "
            f"{build_manifest_resp.status_code}, expected 200.",
            assertion_message=build_manifest_resp.status_code,
        )
    build_manifest = parser.parse_buildmanifest(script=build_manifest_resp.text)
    sorted_pages = build_manifest.get("sortedPages") or []
    result["pages"] = [f"{base_url}{page}" for page in sorted_pages]
    if sorted_pages:
        print("Pages:")
        for page in sorted_pages:
            print(f"- {base_url}{page}")
    api_paths = api.list_api_paths(
        sorted_pages=sorted_pages,
        build_id=build_id,
        base_path=base_path,
        is_api_exposed=is_api_exposed or force_api,
    )
    result["api_paths"] = [f"{base_url}{api_path}" for api_path in api_paths]
    if api_paths:
        if is_api_exposed:
            print("The api is exposed, possible endpoints:")
        else:
            print("Forced api endpoints display:")
        for page in api_paths:
            print(f"- {base_url}{page}")
    result["outcome"] = OUTCOME_SUCCESS
    result["reason"] = "The analysis completed successfully."
    if json_output is not None:
        _write_json_result(path=json_output, result=result)


# https://linktr.ee/profiles/_next/data/30108fd15e4750972e218b4f910c6d98db8e774c/index.json
# Redirecting infinitely
#

if __name__ == "__main__":
    app()
