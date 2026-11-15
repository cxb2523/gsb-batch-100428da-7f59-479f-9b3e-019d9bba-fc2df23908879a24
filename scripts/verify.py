#!/usr/bin/env python3
"""Re-verify every tool's published version and repository health.

The comparison in this repo goes stale the moment a tool ships a release, so the
version and project-health tables are generated rather than hand-written.

    python scripts/verify.py              # print the tables
    python scripts/verify.py --write      # also regenerate STATUS.md

Uses only the standard library. Set GITHUB_TOKEN to avoid the 60 req/hour
unauthenticated rate limit (the script needs ~25 GitHub calls).

Every upstream request is tracked separately. A request that fails (timeout, HTTP
error, or throttling) is rendered with a `!` in its own cell and listed under
"Sources not confirmed", while a source that genuinely has nothing to report is
rendered as `—`. One failed source never blanks the rest of a tool's row.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

OK = "ok"
MISSING = "missing"
FAILED = "failed"

REPO_ROOT = Path(__file__).resolve().parent.parent
UA = "anti-detect-browser-tools-tech-comparison/verify.py"
TIMEOUT = 30

# One entry per tool tracked by the report. `repos` lists every upstream repo we
# clone for source verification; `primary` is the one used for health metrics.
TOOLS = [
    {
        "name": "Camoufox",
        "primary": "daijro/camoufox",
        "repos": ["daijro/camoufox", "daijro/browserforge"],
        "pypi": ["camoufox"],
        "npm": [],
        "include_prereleases": True,
    },
    {
        "name": "Patchright",
        "primary": "Kaliiiiiiiiii-Vinyzu/patchright",
        "repos": [
            "Kaliiiiiiiiii-Vinyzu/patchright",
            "Kaliiiiiiiiii-Vinyzu/patchright-python",
            "Kaliiiiiiiiii-Vinyzu/patchright-nodejs",
            "Kaliiiiiiiiii-Vinyzu/CDP-Patches",
            "DevEnterpriseSoftware/patchright-dotnet",
        ],
        "pypi": ["patchright"],
        "npm": ["patchright"],
        "include_prereleases": False,
    },
    {
        "name": "SeleniumBase",
        "primary": "seleniumbase/SeleniumBase",
        "repos": ["seleniumbase/SeleniumBase"],
        "pypi": ["seleniumbase"],
        "npm": [],
        "include_prereleases": False,
    },
    {
        "name": "Botasaurus",
        "primary": "omkarcloud/botasaurus",
        "repos": ["omkarcloud/botasaurus", "omkarcloud/botasaurus-driver"],
        "pypi": ["botasaurus", "botasaurus-driver"],
        "npm": [],
        "include_prereleases": False,
    },
    {
        "name": "XDriver",
        "primary": "arjun-sha/XDriver",
        "repos": ["arjun-sha/XDriver", "rebrowser/rebrowser-patches"],
        "pypi": [],
        "npm": [],
        "include_prereleases": False,
    },
    {
        "name": "CloakBrowser",
        "primary": "CloakHQ/CloakBrowser",
        "repos": ["CloakHQ/CloakBrowser"],
        "pypi": ["cloakbrowser"],
        "npm": ["cloakbrowser"],
        "include_prereleases": True,
    },
    {
        "name": "Scrapling",
        "primary": "D4Vinci/Scrapling",
        "repos": ["D4Vinci/Scrapling"],
        "pypi": ["scrapling"],
        "npm": [],
        "include_prereleases": False,
    },
    {
        "name": "Obscura",
        "primary": "h4ckf0r0day/obscura",
        "repos": ["h4ckf0r0day/obscura", "h4ckf0r0day/obscura-benchmark"],
        "pypi": [],
        "npm": [],
        "include_prereleases": False,
    },
    {
        "name": "invisible_playwright",
        "primary": "feder-cr/invisible_playwright",
        "repos": [
            "feder-cr/invisible_playwright",
            "feder-cr/invisible_core",
            "feder-cr/firefox_antidetect_patch",
        ],
        "pypi": ["invisible-playwright", "invisible-core"],
        "npm": [],
        "include_prereleases": False,
    },
    {
        "name": "Clearcote",
        "primary": "clearcotelabs/clearcote-browser",
        "repos": ["clearcotelabs/clearcote-browser", "clearcotelabs/clearcote-profiles"],
        "pypi": ["clearcote"],
        "npm": ["clearcote"],
        "include_prereleases": True,
    },
]


@dataclass(frozen=True)
class FetchError:
    """Why a request produced no usable payload."""

    reason: str
    not_found: bool = False
    empty: bool = False


@dataclass(frozen=True)
class Result:
    """Outcome of one upstream source.

    ``OK`` carries the rendered cell value and is indistinguishable from the
    pre-failure-tracking output. ``MISSING`` means the source answered and had
    nothing to report (the package is not published, the repo has no releases);
    it renders as ``—``. ``FAILED`` means we do not know, and renders as ``!``.
    """

    status: str
    value: object = ""
    detail: str = ""

    @classmethod
    def ok(cls, value: object) -> "Result":
        return cls(OK, value)

    @classmethod
    def missing(cls, value: object = "—", detail: str = "") -> "Result":
        return cls(MISSING, value, detail)

    @classmethod
    def failed(cls, detail: str) -> "Result":
        return cls(FAILED, "!", detail)

    @property
    def is_failed(self) -> bool:
        return self.status == FAILED


def fetch_json(url: str, token: str | None = None) -> tuple[object | None, FetchError | None]:
    """Return ``(payload, None)`` on success or ``(None, FetchError)`` on failure.

    An empty 2xx body comes back as ``FetchError(empty=True)`` rather than as data:
    only the caller knows whether "the source said nothing" means an empty list
    (GitHub answers 204 for a repository with no contributors) or a broken response.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    if token and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
        if exc.code == 404:
            return None, FetchError("HTTP 404 (not found)", not_found=True)
        if exc.code == 429 or (exc.code == 403 and remaining == "0"):
            return None, FetchError(f"HTTP {exc.code} (rate limited)")
        return None, FetchError(f"HTTP {exc.code} {exc.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, FetchError(f"network error: {reason}")
    except http.client.HTTPException as exc:
        return None, FetchError(f"malformed HTTP response: {type(exc).__name__}")
    if not body.strip():
        return None, FetchError("HTTP response had an empty body", empty=True)
    try:
        return json.loads(body), None
    except json.JSONDecodeError:
        return None, FetchError("response was not valid JSON")


def pypi_version(pkg: str) -> Result:
    data, err = fetch_json(f"https://pypi.org/pypi/{pkg}/json")
    if err is not None:
        if err.not_found:
            return Result.missing("—", f"`{pkg}` is not published on PyPI")
        return Result.failed(f"`{pkg}` PyPI request failed: {err.reason}")
    if not isinstance(data, dict):
        return Result.failed(f"`{pkg}` PyPI response was not a JSON object")
    version = data.get("info", {}).get("version", "?")
    files = data.get("releases", {}).get(version) or []
    date = files[0]["upload_time"][:10] if files else "?"
    return Result.ok(f"`{version}` ({date})")


def npm_version(pkg: str) -> Result:
    data, err = fetch_json(f"https://registry.npmjs.org/{pkg}")
    if err is not None:
        if err.not_found:
            return Result.missing("—", f"`{pkg}` is not published on npm")
        return Result.failed(f"`{pkg}` npm request failed: {err.reason}")
    if not isinstance(data, dict):
        return Result.failed(f"`{pkg}` npm response was not a JSON object")
    latest = data.get("dist-tags", {}).get("latest", "?")
    date = (data.get("time", {}).get(latest) or "?")[:10]
    return Result.ok(f"`{latest}` ({date})")


def gh_repo(repo: str, token: str | None) -> Result:
    data, err = fetch_json(f"https://api.github.com/repos/{repo}", token)
    if err is not None:
        if err.not_found:
            return Result.missing("—", f"repository `{repo}` was not found on GitHub")
        return Result.failed(f"GitHub repo request for `{repo}` failed: {err.reason}")
    if not isinstance(data, dict) or "full_name" not in data:
        return Result.failed(f"GitHub repo response for `{repo}` was not a repository object")
    return Result.ok(
        {
            "repo": repo,
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "pushed": (data.get("pushed_at") or "?")[:10],
            "created": (data.get("created_at") or "?")[:10],
            "license": (data.get("license") or {}).get("spdx_id") or "none",
            "open_issues": data.get("open_issues_count", 0),
            "archived": data.get("archived", False),
        }
    )


def gh_release(repo: str, token: str | None, include_prereleases: bool) -> Result:
    releases, err = fetch_json(
        f"https://api.github.com/repos/{repo}/releases?per_page=20", token
    )
    if err is not None:
        if err.not_found:
            return Result.missing("— (no GitHub releases)", f"`{repo}` has no releases endpoint")
        if err.empty:
            return Result.missing("— (no GitHub releases)", f"`{repo}` has published no releases")
        return Result.failed(f"GitHub release request for `{repo}` failed: {err.reason}")
    if not isinstance(releases, list):
        return Result.failed(f"GitHub release response for `{repo}` was not a list")
    if not releases:
        return Result.missing("— (no GitHub releases)", f"`{repo}` has published no releases")
    if not include_prereleases:
        releases = [r for r in releases if not r.get("prerelease")] or releases
    top = releases[0]
    tag = top.get("tag_name", "?")
    date = (top.get("published_at") or "?")[:10]
    flag = " *(pre)*" if top.get("prerelease") else ""
    return Result.ok(f"`{tag}` ({date}){flag}")


def gh_contributors(repo: str, token: str | None) -> Result:
    """Distinct contributors, capped at 100 — a rough bus-factor signal."""
    data, err = fetch_json(
        f"https://api.github.com/repos/{repo}/contributors?per_page=100&anon=false", token
    )
    if err is not None:
        if err.not_found:
            return Result.missing("—", f"contributors for `{repo}` were not found")
        if err.empty:
            return Result.missing("0", f"`{repo}` has no contributors")
        return Result.failed(f"GitHub contributors request for `{repo}` failed: {err.reason}")
    if not isinstance(data, list):
        return Result.failed(f"GitHub contributors response for `{repo}` was not a list")
    if not data:
        return Result.missing("0", f"`{repo}` has no contributors")
    return Result.ok(f"{len(data)}{'+' if len(data) >= 100 else ''}")


def collect(token: str | None) -> list[dict]:
    def settle(future, label: str) -> Result:
        """Never let one source's surprise abort the run or blank its siblings."""
        try:
            return future.result()
        except Exception as exc:  # noqa: BLE001 - each source is independently reported
            return Result.failed(f"{label} raised {type(exc).__name__}: {exc}")

    def one(tool: dict) -> dict:
        primary = tool["primary"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            f_repo = pool.submit(gh_repo, primary, token)
            f_rel = pool.submit(gh_release, primary, token, tool["include_prereleases"])
            f_contrib = pool.submit(gh_contributors, primary, token)
            f_pypi = [(p, pool.submit(pypi_version, p)) for p in tool["pypi"]]
            f_npm = [(p, pool.submit(npm_version, p)) for p in tool["npm"]]
        return {
            **tool,
            "health": settle(f_repo, f"GitHub repo lookup for `{primary}`"),
            "release": settle(f_rel, f"GitHub release lookup for `{primary}`"),
            "contributors": settle(f_contrib, f"GitHub contributors lookup for `{primary}`"),
            # Keep the package names and their outcomes paired: a failed package must
            # not shift the rendered cells of the ones that succeeded.
            "pypi_results": [settle(f, f"PyPI lookup for `{p}`") for p, f in f_pypi],
            "npm_results": [settle(f, f"npm lookup for `{p}`") for p, f in f_npm],
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(one, TOOLS))


def cell(result: Result) -> str:
    """Render a bare source result: its value, `—` if absent, `!` if unconfirmed."""
    return "!" if result.is_failed else str(result.value)


def package_cell(pkg: str, result: Result) -> str:
    return f"`{pkg}` {cell(result)}"


def failures(rows: list[dict]) -> list[tuple[str, str]]:
    """Every failed request this run, as ``(tool name, detail)`` in table order."""
    found: list[tuple[str, str]] = []

    def add(tool: str, result: Result) -> None:
        if result.is_failed:
            found.append((tool, result.detail))

    for r in rows:
        add(r["name"], r["health"])
        add(r["name"], r["release"])
        add(r["name"], r["contributors"])
        for result in r["pypi_results"]:
            add(r["name"], result)
        for result in r["npm_results"]:
            add(r["name"], result)
    return found


def render(rows: list[dict], stamp: str, token_present: bool) -> str:
    out: list[str] = []
    out.append("# Verified Status")
    out.append("")
    out.append(
        f"Generated by [`scripts/verify.py`](scripts/verify.py) on **{stamp}**. "
        "Every number below came from the package registry or the GitHub API at that "
        "moment — nothing here is hand-edited. Re-run the script to refresh it."
    )
    out.append("")
    failed = failures(rows)
    if failed:
        out.append(
            "> **`!` means unconfirmed, not absent.** A cell marked `!` belongs to a "
            "source whose request failed during this run (timeout, HTTP error, or "
            "rate limiting), so its real value is unknown. `—` still means the source "
            "answered and had nothing to report. Other cells in the same row are live "
            "data from this same run. See [Sources not confirmed]"
            "(#sources-not-confirmed) for the per-request reasons."
        )
        out.append("")
    if not token_present:
        out.append(
            "> **GitHub requests were unauthenticated.** No `GITHUB_TOKEN` was set, so "
            "the API allows only 60 requests/hour; some GitHub values below may be "
            "missing or marked `!` because of that limit. Set `GITHUB_TOKEN` and re-run "
            "to confirm them."
        )
        out.append("")
    out.append("```bash")
    out.append("python scripts/verify.py --write")
    out.append("```")
    out.append("")
    out.append("## Latest published versions")
    out.append("")
    out.append("| Tool | Latest GitHub release | Python package | npm package |")
    out.append("|------|----------------------|----------------|-------------|")
    for r in rows:
        pypi = (
            "<br>".join(package_cell(p, v) for p, v in zip(r["pypi"], r["pypi_results"]))
            or "—"
        )
        npm = (
            "<br>".join(package_cell(p, v) for p, v in zip(r["npm"], r["npm_results"]))
            or "—"
        )
        out.append(f"| **{r['name']}** | {cell(r['release'])} | {pypi} | {npm} |")
    out.append("")
    out.append("## Project health")
    out.append("")
    out.append(
        "Stars measure popularity, not stealth. The columns that matter for a build "
        "decision are **License** (can you ship it commercially), **Last push** (is "
        "anyone home), and **Contributors** (what happens if the maintainer stops)."
    )
    out.append("")
    out.append(
        "| Tool | Primary repo | License | Last push | Stars | Forks | Contributors | Open issues |"
    )
    out.append(
        "|------|--------------|---------|-----------|------:|------:|-------------:|------------:|"
    )
    for r in rows:
        h = r["health"]
        repo = h.value["repo"] if h.status == OK else r["primary"]
        if h.status == OK:
            repo_cell = f"[`{repo}`](https://github.com/{repo})"
            archived = " ⚠️ archived" if h.value["archived"] else ""
            license_cell = h.value["license"]
            pushed_cell = f"{h.value['pushed']}{archived}"
            stars_cell = f"{h.value['stars']:,}"
            forks_cell = f"{h.value['forks']:,}"
            issues_cell = str(h.value["open_issues"])
        else:
            # The repo lookup failed or found nothing; every metric it would have
            # supplied is unknown, but contributors is a separate request and is
            # rendered from its own outcome below.
            repo_cell = f"`{repo}`"
            unknown = cell(h)
            license_cell = pushed_cell = stars_cell = forks_cell = issues_cell = unknown
        out.append(
            f"| **{r['name']}** | {repo_cell} "
            f"| {license_cell} | {pushed_cell} | {stars_cell} | {forks_cell} "
            f"| {cell(r['contributors'])} | {issues_cell} |"
        )
    out.append("")
    if failed:
        out.append("## Sources not confirmed")
        out.append("")
        out.append(
            "Each entry is a source this run could not read. The corresponding cells "
            "above show `!`; an empty value here would have been indistinguishable from "
            "a package that is genuinely unpublished."
        )
        out.append("")
        for name, detail in failed:
            out.append(f"- **{name}** — {detail}")
        out.append("")
    out.append("## Repos cloned for source verification")
    out.append("")
    out.append(
        "Claims marked **Tier A** in the report were read from this code. The trees are "
        "unaudited third-party projects, so they are cloned into a hardened, "
        "network-severed Docker sandbox by [`scripts/sandbox.sh`](scripts/sandbox.sh) — "
        "never onto a workstation. See [METHODOLOGY.md](METHODOLOGY.md#source-handling)."
    )
    out.append("")
    for r in rows:
        repos = " · ".join(f"[`{x}`](https://github.com/{x})" for x in r["repos"])
        out.append(f"- **{r['name']}** — {repos}")
    out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="regenerate STATUS.md")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("note: no GITHUB_TOKEN set — may hit the 60 req/hour limit\n", file=sys.stderr)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = collect(token)
    # Preserve the declaration order in TOOLS regardless of completion order.
    order = {t["name"]: i for i, t in enumerate(TOOLS)}
    rows.sort(key=lambda r: order[r["name"]])

    text = render(rows, stamp, token_present=bool(token))
    print(text)

    failed = failures(rows)
    if failed:
        print(
            f"\nnote: {len(failed)} source(s) could not be confirmed; "
            "affected cells are marked '!'",
            file=sys.stderr,
        )

    if args.write:
        target = REPO_ROOT / "STATUS.md"
        target.write_text(text, encoding="utf-8")
        print(f"\nwrote {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
