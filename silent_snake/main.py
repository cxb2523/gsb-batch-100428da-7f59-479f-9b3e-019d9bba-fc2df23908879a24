import requests
from bs4 import BeautifulSoup
from urllib.parse import parse_qs, urlparse, urljoin
import re
from typing import Any, List
import signal
from ssl import SSLCertVerificationError
from details import server, techs
import argparse
import asyncio

type URL = str
type Domain = str
type Link = str
UserAgents = {
    "1": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",  # pc
    "2": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",  # pc
    "3": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",  # Iphone
    "4": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",  # Android
}


def check_url(url) -> URL:
    """
    Lower case url and remove any white space at the end
    """
    if re.search(r"https?://", url):
        return url.lower().rstrip("/")
    url = "https://" + url
    return url.lower().rstrip("/")


def get_domain(url) -> Domain:
    """
    returns scheme://netloc

    Example : https://github.com
    """

    parsed_url = urlparse(url)
    domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    return domain


def is_page(url: URL, extra_patterns: list[str] = None) -> bool:
    """
    check for pagination
    """

    default_patterns = [
        r"page",
        r"p",
        r"pg",
        r"pagenumber",
        r"start",
        r"offset",
        r"limit",
    ]

    patterns = set(default_patterns + (extra_patterns or []))

    # Parse the query parameters of the URL
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)

    # Check query parameter names
    for param in query_params:
        for pattern in patterns:
            if re.search(pattern, param, re.IGNORECASE):
                return True

    # Also check fragments (e.g., #page=2)
    if parsed.fragment:
        for pattern in patterns:
            if re.search(pattern, parsed.fragment, re.IGNORECASE):
                return True

    return False


def shortify(str):
    try:
        return str[:25] + "..."
    except:
        return str + "..."


### Failure / skip reasons recorded per URL ###
REASON_CONNECTION = "connection failed"
REASON_CERT = "certificate problem"
REASON_NON_HTML = "non-HTML response"
REASON_PAGINATION = "pagination"
REASON_OTHER = "other failure"


def scrape(
    url: URL, domain: Domain, media_li: List[Link]
) -> tuple[dict[str, Any] | None, list, str | None]:
    """
    page_data contains

    - Title

    - Meta Description

    - URL, Status code

    links = All a tag links in page

    Returns (page_data, links, error). On success error is None.
    On failure page_data is None, links is [] and error holds the reason
    (one of the REASON_* constants, or a short description for unexpected
    errors) so the caller can record it instead of silently dropping the URL.
    """

    try:
        headers = {"User-Agent": UserAgents["1"]}
        response = requests.get(url, headers=headers, timeout=10)
        if "text/html" not in response.headers.get("Content-Type", ""):
            return None, [], REASON_NON_HTML
    except KeyError:
        print("check UserAgent Dict !!!")
        return None, [], REASON_OTHER

    ### handling cert & connection Errors
    except SSLCertVerificationError:
        print(
            "Certificate problem \n use 'response = requests.get(url, headers=headers, verify=False)' (NOT RECOMMENDED)"
        )
        return None, [], REASON_CERT
    except requests.exceptions.SSLError:
        print(
            "Certificate problem \n use 'response = requests.get(url, headers=headers, verify=False)' (NOT RECOMMENDED)"
        )
        return None, [], REASON_CERT
    except requests.exceptions.ConnectionError:
        print(f"Failed to Connect: {url}")
        return None, [], REASON_CONNECTION
    except requests.exceptions.RequestException as e:
        # timeouts, too many redirects, etc. must not abort the crawl
        return None, [], f"{REASON_OTHER} ({type(e).__name__})"

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        page_data = {"URL": url, "status": response.status_code}

        page_data.update(
            {
                "Title": soup.title.string.strip()
                if soup.title and soup.title.string
                else "",
                "META Description": soup.find("meta", {"name": "description"})[
                    "content"
                ].strip()
                if soup.find("meta", {"name": "description"})
                else "",
            }
        )

        links = []

        # Note that some links will be revealed if you preform a certain action for example loggin in as Admin will reveal Admin Dash link

        for a in soup.find_all("a", href=True):
            link = check_url(urljoin(url, a["href"]))
            if get_domain(link) == domain and "#" not in link:
                links.append(link)

        for media in soup.find_all(src=True):
            link = check_url(urljoin(url, media["src"]))
            if get_domain(link) == domain and not re.search(
                r"(.svg|.js|.gif|#)", link
            ):  # js and svg files are excluded
                media_li.append(link)
    except Exception as e:
        # a malformed page (e.g. missing <title>) must not abort the crawl
        return None, [], f"{REASON_OTHER} ({type(e).__name__}: {e})"

    return page_data, links, None


async def main():
    ### arg input handling ###
    parser = argparse.ArgumentParser(
        description="Silent Snake - A fast and reliable web scraper for gathering server and technology details along with almost all of inside links.",
        epilog="Example usage: python silent_snake/main.py -u example.com -o n ",
    )
    parser.add_argument(
        "-u",
        "--url",
        type=str,
        help="Target URL to scrape (http/https is optional, default = https)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        choices=["Y", "n", "y"],
        default="Y",
        help="Do you want extra data output? (Y/n)",
    )
    args = parser.parse_args()
    if args.url:
        input_url = args.url
        output = args.output.lower()

    ### Interactive input handling ###
    else:
        input_url = input("Enter the URL(http/https is optional , default = https): ")
        output = input("do you want extra data output?(Y/n) ").lower()

    if input_url == "":
        print("???")
        exit(0)
    start_url = check_url(input_url)

    media = []

    def media_output(media=media, output=output):
        """
        Print discovered media links
        """
        if output == "" or output == "y":
            if len(media) > 0:
                print("Also found: ")
                for m in media:
                    print(m)

    ### Per-URL outcome tracking ###
    # Every URL popped from the queue ends up in exactly one record:
    # either a scrape result ("success") or a failure/skip record with a reason.
    outcomes = []  # list of (url, outcome, reason)
    counts = {
        "success": 0,
        REASON_CONNECTION: 0,
        REASON_CERT: 0,
        REASON_NON_HTML: 0,
        REASON_PAGINATION: 0,
        REASON_OTHER: 0,
    }
    visited = set()
    to_visit = {start_url}

    def record(url: URL, outcome: str, reason: str = ""):
        outcomes.append((url, outcome, reason))
        key = reason if reason in counts else outcome
        if key not in counts:
            key = REASON_OTHER
        counts[key] += 1

    summary_printed = False

    def print_summary():
        """
        Per-page outcome summary: successes, failures by reason,
        pagination skips and the deduplicated total URL count.
        Printed on normal completion and on Ctrl+C.
        """
        nonlocal summary_printed
        if summary_printed:
            return
        summary_printed = True
        print("\n===== Per-page outcomes =====")
        for url, outcome, reason in outcomes:
            line = f"{url} -> {outcome}"
            if reason:
                line += f" ({reason})"
            print(line)
        print("===== Crawl summary =====")
        print(f"Succeeded: {counts['success']}")
        print(f"Failed (connection): {counts[REASON_CONNECTION]}")
        print(f"Failed (certificate): {counts[REASON_CERT]}")
        print(f"Failed (non-HTML): {counts[REASON_NON_HTML]}")
        print(f"Failed (other): {counts[REASON_OTHER]}")
        print(f"Skipped (pagination): {counts[REASON_PAGINATION]}")
        print(f"Total unique URLs visited: {len(visited)}")
        if to_visit:
            print(f"Pending (not yet visited): {len(to_visit)}")

    # Handle graceful exit on Ctrl+C
    def exit_gracefully(signum, frame):
        print("\nInterrupted (Ctrl+C).")
        print_summary()
        media_output()
        print("Exiting gracefully...")

        # Perform collective tasks here if needed
        exit(0)

    signal.signal(signal.SIGINT, exit_gracefully)

    ### Get and print details about the server and technologies used ###
    server_obj = server.Server(
        host=get_domain(start_url).replace("https://", "").replace("http://", "")
    )
    ui_frameworks_obj = techs.UiFrameworks()

    try:
        await asyncio.gather(
            server_obj.fetch_details(), ui_frameworks_obj.detect(url=start_url)
        )
    except Exception as e:
        # unreachable/unresponsive target must not abort the crawl below
        print(f"Could not fetch server/tech details: {e}")

    print(server_obj)
    print(ui_frameworks_obj)

    domain = get_domain(start_url)
    all_headers = set(["URL", "Title", "META Description", "Status code"])

    try:
        while len(to_visit) > 0:
            current_url = to_visit.pop()
            if current_url in visited:
                continue  # each URL is processed exactly once
            visited.add(current_url)

            if is_page(current_url):
                # pagination URLs are recorded as skipped, not silently dropped
                record(current_url, "skipped", REASON_PAGINATION)
                continue

            try:
                page_data, links, error = scrape(current_url, domain, media)
            except Exception as e:
                # a failure on one page must never abort the whole crawl
                page_data, links, error = (
                    None,
                    [],
                    f"{REASON_OTHER} ({type(e).__name__}: {e})",
                )

            if error is not None:
                record(current_url, "failed", error)
                continue

            record(current_url, "success")
            all_headers.update(page_data.keys())

            if output == "" or output == "y":
                print(
                    f"{current_url}, {shortify(page_data['Title'])}, {shortify(page_data['META Description'])}"
                )

            for link in links:
                if link not in visited:
                    to_visit.add(link)
    except KeyboardInterrupt:
        # fallback in case the SIGINT handler is bypassed
        print("\nInterrupted (Ctrl+C).")
    finally:
        print_summary()

    print("No more Urls")

    if output == "" or output == "y":
        media_output()


if __name__ == "__main__":
    asyncio.run(main())
