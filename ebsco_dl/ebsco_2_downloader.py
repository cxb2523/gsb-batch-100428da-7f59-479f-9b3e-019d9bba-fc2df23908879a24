import asyncio
import hashlib
import json
import logging
import os
import posixpath
import re
import sys
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import ParseResult, quote, unquote, urlparse, urlsplit, urlunsplit

import aiofiles
import aiohttp
import pypdf
import urllib3
from aiohttp.client_exceptions import ClientError, ClientResponseError
from lxml import etree
from requests.sessions import Session

from ebsco_dl.utils import Log
from ebsco_dl.utils import PathTools as PT
from ebsco_dl.utils import (
    SimpleCookieJar,
    SslHelper,
    check_verbose,
    convert_to_aiohttp_cookie_jar,
    format_bytes,
    parse_xml_string,
)


@dataclass
class EbscoBookInfo:
    parsed_url: ParseResult
    book_id: str
    user_id: str
    book_format: str
    base_webview: str = field(init=False, default=None)
    on_page_json: Dict = field(init=False, default=None)

    turn_json: Dict = field(init=False, default=None)
    checkout_token: str = field(init=False, default=None)

    book_info_json: Dict = field(init=False, default=None)
    pages_info_json: Dict = field(init=False, default=None)
    all_pages_ids: List = field(init=False, default=None)
    all_clean_pages_ids: List = field(init=False, default=None)


class ContentRangeError(ConnectionError):
    pass


@dataclass
class FetchedArtifact:
    status: int
    reason: str
    url: str
    content_type: str
    encoding: str
    body: bytes

    @property
    def ok(self) -> bool:
        return self.status < 400

    def text(self) -> str:
        return self.body.decode(self.encoding, errors='replace')


RETRYABLE_ERRORS = (ClientError, asyncio.TimeoutError, OSError, ValueError)
RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
# Errors that make a single page/include fail. They are recorded in the report instead of aborting the whole book.
# Anything outside this tuple is a bug or a broken session and is still allowed to propagate.
ARTIFACT_DOWNLOAD_ERRORS = RETRYABLE_ERRORS

# Written into the book directory instead of an incomplete .epub, so a partial run stays auditable and resumable.
DOWNLOAD_REPORT_NAME = 'download-report.json'


@dataclass
class EpubPageResult:
    """Outcome of one XHTML page download. Failures are reported, not raised, so one page cannot abort the book."""

    path: str
    url: str
    includes: List[str] = field(default_factory=list)
    failure_reason: str = None

    @property
    def ok(self) -> bool:
        return self.failure_reason is None


@dataclass
class EpubIncludeResult:
    """Outcome of one include download (image, stylesheet, font, ...)."""

    path: str
    url: str
    failure_reason: str = None
    discovered_includes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failure_reason is None


class Ebsco2Downloader:
    stdHeader = {
        'User-Agent': (
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
        ),
        'Content-Type': 'application/x-www-form-urlencoded',
    }

    def __init__(
        self,
        storage_path: str,
        download_url: str,
        skip_cert_verify: bool,
    ):
        self.storage_path = storage_path
        self.download_url = download_url
        self.skip_cert_verify = skip_cert_verify
        self.max_dl_retries = 10
        self.max_parallel_dl = 10
        self.client_timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
        self.verbose = check_verbose()

        logging.getLogger("requests").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        # logging.getLogger("requests").setLevel(logging.DEBUG)
        # logging.getLogger("urllib3").setLevel(logging.DEBUG)

        urllib3.disable_warnings()

        # init cookies
        cookies_path = 'cookies.txt'
        self.cookie_jar = SimpleCookieJar(cookies_path)
        if os.path.isfile(cookies_path):
            self.cookie_jar.load(ignore_discard=True, ignore_expires=True)
        else:
            Log.warning(f'No cookies file found at {os.path.abspath(cookies_path)}, continuing without cookies')

        self.session = self.create_session()

        # Parse download URL
        parsed_url = urlparse(self.download_url)

        # Check if it is a valid URL
        if not (parsed_url.path.startswith("/c/") and "/ebook-viewer/" in parsed_url.path):
            raise NotImplementedError('This type of URL is yet not supported')

        url_path_parts = parsed_url.path.split('/')
        self.book_info = EbscoBookInfo(
            parsed_url=parsed_url,
            user_id=url_path_parts[2],
            book_format=url_path_parts[4],
            book_id=url_path_parts[5],
        )

    @staticmethod
    def prettify_xml(xml_string):
        return etree.tostring(
            parse_xml_string(xml_string), pretty_print=True, encoding='utf-8', xml_declaration=True
        ).decode()

    def run(self):
        self.book_info.base_webview = self.get_url_view(self.download_url)
        self.book_info.on_page_json = json.loads(
            self.first_match(
                r'(?s)<script\s*id="__NEXT_DATA__"\s*type="application/json">\s*({.*?})\s*</script>',
                self.book_info.base_webview,
                'On page json',
            )
        )

        if self.book_info.book_id != self.book_info.on_page_json['query']['recordId']:
            Log.warning(
                f"recordId is not equal: {self.book_info.book_id} != {self.book_info.on_page_json['query']['recordId']}"
            )

        self.book_info.book_info_json = self.load_book_info()
        self.book_info.pages_info_json = self.load_pages_info()

        self.book_info.all_pages_ids, self.book_info.all_clean_pages_ids = self.extract_artifact_ids(
            self.book_info.pages_info_json.get('pages', [])
        )

        self.book_info.turn_json = self.load_page_turn()
        self.book_info.checkout_token = self.book_info.turn_json.get('checkoutToken')
        if not self.book_info.checkout_token:
            raise RuntimeError(
                f'EBSCO did not hand out a checkout token for this book: {json.dumps(self.book_info.turn_json)[:300]}'
            )

        if self.book_info.book_format == 'epub':
            return asyncio.run(self.download_epub())
        elif self.book_info.book_format == 'pdf':
            return self.download_pdf()
        else:
            raise NotImplementedError("This book format is not yet supported")

    def get_url_view(self, url: str):
        Log.info(f'Loading: `{url}`')

        response = self.session_get(url)

        if not response.ok:
            raise RuntimeError(f'Your session is broken! {response.reason}')

        redirected_url = urlparse(response.url)
        if not redirected_url.path.startswith(urlparse(url).path):
            raise RuntimeError(
                'Your cookies or the session id in the URL are invalid!'
                f' You were redirected to: {redirected_url.netloc}{redirected_url.path}'
            )

        return response.text

    def session_get(self, url):
        response = self.session.get(
            url,
            headers=self.stdHeader.copy(),
            verify=not self.skip_cert_verify,
            allow_redirects=True,
            timeout=60,
        )
        return response

    def get_cookie_jar(self) -> aiohttp.CookieJar:
        return convert_to_aiohttp_cookie_jar(self.cookie_jar)

    def create_client_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(cookie_jar=self.get_cookie_jar(), timeout=self.client_timeout)

    @staticmethod
    def retry_delay_seconds(try_num: int) -> int:
        return min(2**try_num, 30)

    @staticmethod
    def describe_error(err: BaseException) -> str:
        if isinstance(err, ClientResponseError):
            return f'{err.status} {err.message}'
        return str(err) or type(err).__name__

    @staticmethod
    def parse_json_response(response, name: str) -> Dict:
        if not response.ok:
            raise RuntimeError(f'Could not load {name}: {response.status_code} {response.reason}')
        try:
            return json.loads(response.text)
        except ValueError as err:
            raise RuntimeError(
                f'Could not load {name}, EBSCO answered with {response.headers.get("Content-Type")} instead of JSON'
            ) from err

    def create_session(self) -> Session:
        session = SslHelper.custom_requests_session(self.skip_cert_verify, True, True)
        session.cookies = self.cookie_jar
        return session

    @staticmethod
    def first_match(regex_with_group, text, pattern_name, default=None) -> str:
        found_match = re.search(regex_with_group, text)
        if found_match is None:
            if default is None:
                raise ValueError(f'Could not find {pattern_name}.')
            else:
                return default
        return found_match.group(1)

    def load_book_info(self):
        book_info_url = self.book_info.parsed_url._replace(
            path=f"/api/books/viewer/v1/c/{self.book_info.user_id}/record/{self.book_info.book_id}/format/{self.book_info.book_format}",
            query=None,
        ).geturl()

        Log.info(f"Loading book info: `{book_info_url}`")
        book_response = self.session_get(book_info_url)
        return self.parse_json_response(book_response, 'book info')

    def load_pages_info(self):
        pages_info_url = self.book_info.parsed_url._replace(
            path=f"/api/books/toc/v1/c/{self.book_info.user_id}/record/{self.book_info.book_id}/format/{self.book_info.book_format}/toc",
            query=None,
        ).geturl()

        Log.info(f"Loading pages info: `{pages_info_url}`")
        pages_response = self.session_get(pages_info_url)
        return self.parse_json_response(pages_response, 'pages info')

    @staticmethod
    def extract_artifact_ids(artifact_list: List):
        all_pages_ids = []
        all_clean_pages_ids = []
        for page in artifact_list:
            # artifact IDs
            page_artifact_id = page.get('artifactId')
            page_artifact_id = page_artifact_id.split('#')[0]
            if page_artifact_id not in all_pages_ids and page_artifact_id != 'previewlimit':
                all_pages_ids.append(page_artifact_id)

            # clean IDs
            page_clean_id = page.get('id')
            if page_clean_id not in all_clean_pages_ids and page_clean_id != 'previewlimit':
                all_clean_pages_ids.append(page_clean_id)
        return all_pages_ids, all_clean_pages_ids

    def load_page_turn(self):
        turn_data = {
            "RetrievalDatabase": self.book_info.book_info_json.get("db"),
            "PageIds": self.book_info.all_clean_pages_ids[:1],
            # "PatronRequestedPageLabel": "265",
        }
        turn_url = self.book_info.parsed_url._replace(
            path=f"/api/books/viewer/v1/book/{self.book_info.book_info_json.get('bookId')}/format/{self.book_info.book_format}/page-turn",
            query=None,
        ).geturl()

        Log.info(f"Loading turn: `{turn_url}`")
        headers = self.stdHeader.copy()
        headers["Content-Type"] = "application/json"
        turn_response = self.session.post(
            turn_url,
            data=json.dumps(turn_data),
            headers=headers,
            verify=(not self.skip_cert_verify),
            allow_redirects=True,
            timeout=60,
        )
        return self.parse_json_response(turn_response, 'page turn')

    def download_pdf(self):
        # Extract Meta data
        book_title = self.book_info.book_info_json.get('bookTitle', 'untitled')

        # Start downloading Artifacts
        book_path = PT.path_of_book(self.storage_path, book_title)
        os.makedirs(str(book_path), exist_ok=True)
        pdf_content_files = asyncio.run(self.batch_download_pdf_parts(self.book_info.all_pages_ids, book_path))

        Log.info('Merging pdf artifacts to one pdf')
        merger = pypdf.PdfWriter()
        for idx, pdf_page in enumerate(pdf_content_files):
            merger.append(pdf_page)
            Log.info(f'Merged artifact {idx}')

        all_contents = self.book_info.pages_info_json.get('sections', [])
        for entry in all_contents:
            self.build_outline(merger, entry)

        Log.info('Writing PDF to disk (this can take long for big PDFs)')
        merger.write(str(book_path) + '.pdf')
        merger.close()

    async def batch_download_pdf_parts(self, page_ids: List[str], book_path: Path) -> List[Path]:
        semaphore = asyncio.Semaphore(self.max_parallel_dl)
        async with self.create_client_session() as session:
            dl_results = await asyncio.gather(
                *[self.download_pdf_part(session, semaphore, page_id, book_path) for page_id in page_ids]
            )
        failed_page_ids = [page_id for page_id, dl_result in zip(page_ids, dl_results) if not dl_result[0]]
        if failed_page_ids:
            Log.error(f'Could not download {", ".join(failed_page_ids)}. Abort! Please try again later!')
            sys.exit(1)
        return [dl_result[1] for dl_result in dl_results]

    async def download_pdf_part(
        self, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore, page_id: str, book_path: Path
    ) -> Tuple[bool, Path]:
        """Returns True if the file was successfully downloaded or exists"""

        rel_file_path = page_id.rsplit('/', 1)[1]
        local_path_raw = book_path / rel_file_path
        local_path = str(local_path_raw)

        if os.path.exists(local_path):
            Log.info(f'{rel_file_path} is already present')
            return True, local_path_raw

        PT.make_base_dir(local_path)
        # Only complete downloads get the final name, so an aborted run never leaves a broken part behind.
        part_path = local_path + '.part'
        dl_url = f'{self.book_info.on_page_json["runtimeConfig"]["BOOKS_CONTENT_EDGE_CLIENT_URL"]}/v1/artifact/{quote(page_id, safe="")}/{self.book_info.checkout_token}'

        async with semaphore:
            if self.verbose:
                Log.info(f'Downloading {rel_file_path} from: {dl_url}')
            else:
                Log.info(f'Downloading {rel_file_path}...')
            finished_successfully = await self.download_to_file(session, dl_url, part_path, rel_file_path)

        if not finished_successfully:
            if os.path.exists(part_path):
                os.unlink(part_path)
            return False, local_path_raw

        os.replace(part_path, local_path)
        if self.verbose:
            Log.success(f'Downloaded {rel_file_path} to: {local_path}')
        else:
            Log.success(f'Successfully downloaded {rel_file_path}')
        return True, local_path_raw

    async def download_to_file(self, session: aiohttp.ClientSession, url: str, file_path: str, name: str) -> bool:
        """Retries failed tries and resumes where the last try stopped, if the server supports range requests."""
        ssl_context = SslHelper.get_ssl_context(self.skip_cert_verify, True, True)
        received = 0
        async with aiofiles.open(file_path, 'wb') as file_obj:

            async def start_over():
                nonlocal received
                await file_obj.seek(0)
                await file_obj.truncate()
                received = 0

            for try_num in range(1, self.max_dl_retries + 1):
                headers = self.stdHeader.copy()
                if received > 0:
                    headers['Range'] = f'bytes={received}-'
                try:
                    async with session.get(url, headers=headers, raise_for_status=True, ssl=ssl_context) as resp:
                        content_range = resp.headers.get('Content-Range', '')  # Example: bytes 200-1000/67589
                        is_resumed = resp.status == 206 and content_range.startswith(f'bytes {received}-')
                        if received > 0 and not is_resumed:
                            if resp.status == 206:
                                raise ContentRangeError(f'Server answered with the wrong range {content_range}')
                            await start_over()

                        total = received + int(resp.headers.get('Content-Length', 0))
                        chunk_idx = 0
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            received += len(chunk)
                            if chunk_idx % 16 == 0:
                                Log.info(f'{name} got {format_bytes(received)} / {format_bytes(total)}')
                            chunk_idx += 1
                            await file_obj.write(chunk)
                    return True

                except RETRYABLE_ERRORS as err:
                    if isinstance(err, ClientResponseError) and err.status not in RETRYABLE_STATUS_CODES:
                        Log.error(f'{name} could not be downloaded: {err.status} {err.message}')
                        return False

                    Log.warning(
                        f'(Try {try_num} of {self.max_dl_retries}) Unable to download {name}: {self.describe_error(err)}'
                    )
                    if isinstance(err, ContentRangeError):
                        await start_over()
                    if try_num < self.max_dl_retries:
                        await asyncio.sleep(self.retry_delay_seconds(try_num))
        return False

    @staticmethod
    def build_outline(merger, nav_dic, parent=None):
        new_parent = merger.add_outline_item(
            title=nav_dic.get('name'), page_number=nav_dic.get('startPageIndex'), parent=parent
        )
        child_contents = nav_dic.get('children', []) or []
        for entry in child_contents:
            Ebsco2Downloader.build_outline(merger, entry, new_parent)

    async def fetch_artifact(self, session: aiohttp.ClientSession, url: str) -> FetchedArtifact:
        ssl_context = SslHelper.get_ssl_context(self.skip_cert_verify, True, True)

        async def fetch_once() -> FetchedArtifact:
            async with session.get(url, headers=self.stdHeader, ssl=ssl_context) as response:
                body = await response.read()
                return FetchedArtifact(
                    status=response.status,
                    reason=response.reason,
                    url=str(response.url),
                    content_type=response.content_type,
                    encoding=response.get_encoding(),
                    body=body,
                )

        for try_num in range(1, self.max_dl_retries):
            try:
                response = await fetch_once()
                if response.status not in RETRYABLE_STATUS_CODES:
                    return response
                problem = f'{response.status} {response.reason}'
            except RETRYABLE_ERRORS as err:
                problem = self.describe_error(err)

            Log.warning(f'(Try {try_num} of {self.max_dl_retries}) Unable to load {url}: {problem}')
            await asyncio.sleep(self.retry_delay_seconds(try_num))

        return await fetch_once()

    @staticmethod
    def resolve_epub_path(referencing_file: str, reference: str) -> str:
        """EPUB paths always use forward slashes, also on Windows, because they end up in URLs and the EPUB."""
        return posixpath.normpath(posixpath.join(posixpath.dirname(referencing_file), reference))

    @staticmethod
    def is_ebsco_error_page(response_text: str) -> bool:
        return response_text.startswith('<script type="text/javascript">') and 'pageError' in response_text

    @staticmethod
    def extract_artifact_includes(xhtml_text: str, page_file_path: str) -> Tuple[List[str], List[str]]:
        """Returns the artifact paths a page references and the external URLs that are skipped."""
        split_xhtml = re.search(
            r'^(.*?<body[^>]*>)(.*)(</body[^>]*>.*)$', xhtml_text, flags=re.DOTALL | re.IGNORECASE
        )
        if split_xhtml is None:
            raise ValueError(f'Could not find the <body> of {page_file_path}')
        xhtml_head, xhtml_body, _xhtml_footer = split_xhtml.groups()

        # Find Header includes
        raw_includes = re.findall(r'src\s*=\s*"([^"]+)"', xhtml_head)
        raw_includes += re.findall(r'href\s*=\s*"([^"]+)"', xhtml_head)
        # Find Body includes
        raw_includes += re.findall(r'src\s*=\s*"([^"]+)"', xhtml_body)
        raw_includes += re.findall(r'<image[^>]+href\s*=\s*"([^"]+)"', xhtml_body)

        includes = []
        external = []
        for artifact_include in raw_includes:
            # Ignore URLs, only download relative and absolute paths
            if urlparse(artifact_include).scheme == '':
                resolved = Ebsco2Downloader.resolve_epub_path(page_file_path, artifact_include)
                if resolved not in includes:
                    includes.append(resolved)
            else:
                external.append(artifact_include)
        return includes, external

    @staticmethod
    def extract_css_includes(css_text: str, artifact_url_path: str) -> Tuple[List[str], List[str]]:
        includes = []
        external = []
        for artifact_include in re.findall(r'url\s*\(["\']?([^"\')]+)["\']?\)', css_text):
            if artifact_include.startswith(('http:', 'https:')):
                external.append(artifact_include)
                continue
            correct_path = Ebsco2Downloader.resolve_epub_path(artifact_url_path, artifact_include)
            if correct_path not in includes:
                includes.append(correct_path)
        return includes, external

    @staticmethod
    def remove_stale_file_and_part(file_path) -> None:
        for candidate in (Path(file_path), Path(str(file_path) + '.part')):
            if candidate.exists():
                try:
                    candidate.unlink()
                except OSError as err:
                    Log.debug(f'Could not remove stale {candidate}: {err}')

    async def download_epub_page(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        artifact_url: str,
        artifact_file_path,
        page_file_path: str,
    ) -> EpubPageResult:
        """Downloads one XHTML page. A failure is returned, not raised, so the remaining pages can still be fetched."""

        result = EpubPageResult(path=page_file_path, url=artifact_url)

        if os.path.exists(artifact_file_path):
            try:
                async with aiofiles.open(artifact_file_path, 'r', encoding='utf-8') as cache_file:
                    cached_text = await cache_file.read()
                result.includes, external_includes = self.extract_artifact_includes(cached_text, page_file_path)
            except (OSError, ValueError, UnicodeDecodeError) as err:
                Log.warning(f'Cached page {page_file_path} is unusable ({err}), downloading it again')
                self.remove_stale_file_and_part(artifact_file_path)
            else:
                for external_include in external_includes:
                    logging.warning('Skipping external include: %s', external_include)
                Log.info(f'{page_file_path} is already present')
                return result

        try:
            async with semaphore:
                response = await self.fetch_artifact(session, artifact_url)

            if not response.ok:
                result.failure_reason = f'{response.status} {response.reason}'
                return result
            if unquote(response.url) != unquote(artifact_url):
                result.failure_reason = f'We got rate limited, were redirected to {response.url}'
                return result

            response_text = response.text()
            if response.content_type == 'text/html' and self.is_ebsco_error_page(response_text):
                result.failure_reason = 'EBSCO returned an error page instead of the XHTML'
                return result

            try:
                result.includes, external_includes = self.extract_artifact_includes(response_text, page_file_path)
            except ValueError:
                result.failure_reason = f'EBSCO sent {response.content_type} instead of XHTML'
                return result

            for external_include in external_includes:
                logging.warning('Skipping external include: %s', external_include)

            # Only complete pages get the final name, so an interrupted run never leaves a truncated page behind.
            part_path = str(artifact_file_path) + '.part'
            async with aiofiles.open(part_path, 'w', encoding='utf-8') as fs:
                await fs.write(response_text)
            os.replace(part_path, artifact_file_path)

        except ARTIFACT_DOWNLOAD_ERRORS as err:
            result.failure_reason = self.describe_error(err)
            self.remove_stale_file_and_part(artifact_file_path)

        if not result.ok:
            Log.error(f'Could not download page {page_file_path}: {result.failure_reason}')
            return result

        Log.info(f'Loaded artifact url: `{artifact_url}`')
        Log.info(f'Written artifact: `{str(artifact_file_path)}`')
        return result

    async def download_epub_include(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        artifact_url: str,
        artifact_url_path: str,
        artifact_file_path,
    ) -> EpubIncludeResult:
        """Downloads one include. Failures are skipped and reported, they never abort the book."""

        result = EpubIncludeResult(path=artifact_url_path, url=artifact_url)

        if os.path.exists(artifact_file_path):
            if artifact_url.lower().endswith('.css'):
                try:
                    async with aiofiles.open(
                        artifact_file_path, 'r', encoding='utf-8', errors='replace'
                    ) as cache_file:
                        cached_text = await cache_file.read()
                    result.discovered_includes, external_includes = self.extract_css_includes(
                        cached_text, artifact_url_path
                    )
                except OSError as err:
                    Log.warning(f'Cached include {artifact_url_path} is unusable ({err}), downloading it again')
                    self.remove_stale_file_and_part(artifact_file_path)
                else:
                    for external_include in external_includes:
                        logging.warning('Skipping external include: %s', external_include)
                    Log.info(f'{artifact_url_path} is already present')
                    return result
            else:
                Log.info(f'{artifact_url_path} is already present')
                return result

        async with semaphore:
            try:
                response = await self.fetch_artifact(session, artifact_url)
            except ARTIFACT_DOWNLOAD_ERRORS as err:
                result.failure_reason = self.describe_error(err)
                logging.error('Could not download %s, epub could be broken: %s', artifact_url, result.failure_reason)
                return result

        if not response.ok:
            result.failure_reason = f'{response.status} {response.reason}'
            logging.error('Could not download %s, epub could be broken: %s.', artifact_url, result.failure_reason)
            return result
        if unquote(response.url) != unquote(artifact_url):
            result.failure_reason = f'We got rate limited, were redirected to {response.url}'
            logging.error('Could not download %s, epub could be broken: %s', artifact_url, result.failure_reason)
            return result

        if response.content_type == 'text/html' and self.is_ebsco_error_page(response.text()):
            result.failure_reason = 'EBSCO returned an error page instead of the artifact'
            logging.error('Could not download %s, epub could be broken.', artifact_url)
            return result

        if artifact_url.lower().endswith('.css'):
            result.discovered_includes, external_includes = self.extract_css_includes(
                response.text(), artifact_url_path
            )
            for external_include in external_includes:
                logging.warning('Skipping external include: %s', external_include)

        try:
            part_path = str(artifact_file_path) + '.part'
            async with aiofiles.open(part_path, 'wb') as fs:
                await fs.write(response.body)
            os.replace(part_path, artifact_file_path)
        except OSError as err:
            result.failure_reason = self.describe_error(err)
            self.remove_stale_file_and_part(artifact_file_path)
            logging.error('Could not write %s: %s', artifact_file_path, result.failure_reason)
            return result

        Log.info(f'Loaded artifact url: `{artifact_url}`')
        Log.info(f'Written artifact: `{str(artifact_file_path)}`')
        return result

    @staticmethod
    def escape(str_xml: str):
        str_xml = str_xml.replace("&", "&amp;")
        str_xml = str_xml.replace("<", "&lt;")
        str_xml = str_xml.replace(">", "&gt;")
        str_xml = str_xml.replace("\"", "&quot;")
        str_xml = str_xml.replace("'", "&apos;")
        return str_xml

    async def download_epub(self):
        # Extract Meta data
        book_title = self.book_info.book_info_json.get('bookTitle', 'untitled')
        authors = self.book_info.book_info_json.get('authors', ['anonymous'])
        if isinstance(authors, str):
            authors = authors.split(', ')
        publication_year = self.book_info.book_info_json.get('publicationYear', '1970')

        # Start downloading Artifacts
        book_directory = PT.path_of_book(self.storage_path, book_title)

        page_results: List[EpubPageResult] = []
        include_results: List[EpubIncludeResult] = []

        semaphore = asyncio.Semaphore(self.max_parallel_dl)
        async with self.create_client_session() as session:
            page_tasks = []

            for page_id in self.book_info.all_pages_ids:
                page_file_path = page_id
                artifact_file_path = book_directory / page_id
                os.makedirs(str(artifact_file_path.parent), exist_ok=True)

                artifact_url = f'{self.book_info.on_page_json["runtimeConfig"]["BOOKS_CONTENT_EDGE_CLIENT_URL"]}/v1/artifact/{quote(page_id, safe="")}/{self.book_info.checkout_token}'

                page_tasks.append(
                    self.download_epub_page(
                        session,
                        semaphore,
                        artifact_url,
                        artifact_file_path,
                        page_file_path,
                    )
                )

            page_results = list(await asyncio.gather(*page_tasks))

            # Collect all artifacts
            all_includes = []
            for page_result in page_results:
                for artifact_include in page_result.includes:
                    if artifact_include not in all_includes:
                        all_includes.append(artifact_include)

            # Download includes (Images, Stylesheets, Fonts)
            base_artifact_url = f'{self.book_info.on_page_json["runtimeConfig"]["BOOKS_CONTENT_EDGE_CLIENT_URL"]}/v1/checkout/{self.book_info.checkout_token}/artifact/'

            requested_includes = set()
            while len(all_includes) > 0:
                requested_includes.update(all_includes)
                include_tasks = []
                for artifact_url_path in all_includes:
                    artifact_url = base_artifact_url + artifact_url_path

                    artifact_url_parts = urlsplit(artifact_url)

                    normalized_path = posixpath.normpath(artifact_url_parts.path)

                    artifact_url = urlunsplit(
                        (
                            artifact_url_parts.scheme,
                            artifact_url_parts.netloc,
                            normalized_path,
                            artifact_url_parts.query,
                            artifact_url_parts.fragment,
                        )
                    )

                    artifact_file_path = book_directory / artifact_url_path
                    os.makedirs(str(artifact_file_path.parent), exist_ok=True)

                    include_tasks.append(
                        self.download_epub_include(
                            session,
                            semaphore,
                            artifact_url,
                            artifact_url_path,
                            artifact_file_path,
                        )
                    )

                batch_results = await asyncio.gather(*include_tasks)
                include_results.extend(batch_results)

                # CSS files may reference further includes, continue until all are known.
                all_css_includes = []
                for include_result in batch_results:
                    if not include_result.ok:
                        continue
                    for css_include in include_result.discovered_includes:
                        if css_include not in all_css_includes:
                            all_css_includes.append(css_include)
                all_includes = [include for include in all_css_includes if include not in requested_includes]

        report_path = book_directory / DOWNLOAD_REPORT_NAME
        missing_pages = self.unique_failures_by_path(page_results)

        if missing_pages:
            report = self.write_download_report(book_directory, book_title, page_results, include_results)
            Log.error(
                f'Could not download {len(missing_pages)} of {len(page_results)} pages, so no EPUB was written.'
                f' See `{report_path}` for the missing pages. Run the same command again to fetch only those pages.'
            )
            return report

        # Every page is present, so the EPUB is complete. Only drop a previous report once the EPUB exists.
        self.compose_epub(
            book_directory,
            [book_directory / page_result.path for page_result in page_results],
            [book_directory / include_result.path for include_result in include_results],
            book_title,
            authors,
            publication_year,
        )

        if os.path.exists(report_path):
            os.unlink(report_path)
            Log.info(f'Removed `{report_path}`, all pages are present now')

        return None

    @staticmethod
    def unique_failures_by_path(results: List) -> List:
        """Returns the failed results, with every path at most once.

        Several tasks can race on the same artifact path, but the report must list each missing artifact once.
        """
        seen = set()
        unique = []
        for result in results:
            if not result.ok and result.path not in seen:
                seen.add(result.path)
                unique.append(result)
        return unique

    def write_download_report(
        self,
        book_directory: Path,
        book_title: str,
        page_results: List[EpubPageResult],
        include_results: List[EpubIncludeResult],
    ) -> Dict:
        """Writes a JSON manifest describing what is on disk and what is still missing."""
        missing_pages = self.unique_failures_by_path(page_results)
        skipped_includes = self.unique_failures_by_path(include_results)
        total_pages = len({page_result.path for page_result in page_results})
        total_includes = len({include_result.path for include_result in include_results})
        report = {
            'bookTitle': book_title,
            'bookId': self.book_info.book_id,
            'generatedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'resumable': True,
            'epubWritten': False,
            'pages': {
                'total': total_pages,
                'downloaded': total_pages - len(missing_pages),
                'missing': len(missing_pages),
                'missingPages': [
                    {
                        'path': page_result.path,
                        'url': page_result.url,
                        'reason': page_result.failure_reason,
                    }
                    for page_result in missing_pages
                ],
            },
            'includes': {
                'total': total_includes,
                'downloaded': total_includes - len(skipped_includes),
                'skipped': len(skipped_includes),
                'skippedIncludes': [
                    {
                        'path': include_result.path,
                        'url': include_result.url,
                        'reason': include_result.failure_reason,
                    }
                    for include_result in skipped_includes
                ],
            },
        }

        report_path = book_directory / DOWNLOAD_REPORT_NAME
        tmp_path = report_path.with_name(report_path.name + '.part')
        os.makedirs(str(book_directory), exist_ok=True)
        with open(tmp_path, 'w', encoding='utf-8') as report_file:
            json.dump(report, report_file, indent=2, ensure_ascii=False)
            report_file.write('\n')
        os.replace(tmp_path, report_path)
        return report

    def compose_epub(
        self,
        book_directory: Path,
        epub_content_files: List[Path],
        epub_include_files: List[Path],
        book_title: str,
        authors: List[str],
        publication_year: str,
    ):
        # Write to a temporary name first, so an aborted run never leaves a truncated .epub behind.
        epub_path = str(book_directory) + '.epub'
        part_path = epub_path + '.part'
        try:
            with zipfile.ZipFile(part_path, 'w', zipfile.ZIP_DEFLATED) as epub:
                self.fill_epub(
                    epub,
                    book_directory,
                    epub_content_files,
                    epub_include_files,
                    book_title,
                    authors,
                    publication_year,
                )
            os.replace(part_path, epub_path)
        except BaseException:
            if os.path.exists(part_path):
                os.unlink(part_path)
            raise
        return None

    def fill_epub(
        self,
        epub: zipfile.ZipFile,
        book_directory: Path,
        epub_content_files: List[Path],
        epub_include_files: List[Path],
        book_title: str,
        authors: List[str],
        publication_year: str,
    ):
        # Mimetype
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

        container_xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
            <rootfiles>
                <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
        </rootfiles>
        </container>
        '''
        # META-INF/container.xml
        epub.writestr("META-INF/container.xml", self.prettify_xml(container_xml))

        # OEBPS/content.opf
        creators = ''
        for idx, author in enumerate(authors):
            creators += f'<dc:creator id="creator{idx}" opf:role="aut">{self.escape(author)}</dc:creator>'

        epub_manifest = ''
        epub_spine = ''

        # build spine and add all normal xhtml files to manifest
        for idx, epub_content_file in enumerate(epub_content_files):
            relative_path = epub_content_file.relative_to(book_directory).as_posix()
            relative_oebps_path = posixpath.relpath(relative_path, 'OEBPS')

            epub_manifest += (
                f'<item id="html{idx + 1}" href="{relative_oebps_path}" media-type="application/xhtml+xml"/>\n'
            )
            epub_spine += f'<itemref idref="html{idx + 1}" />\n'
            epub.write(str(epub_content_file), relative_path)

        # Add all includes to manifest
        mimetype_dict = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'svg': 'image/svg+xml',
            'css': 'text/css',
            'xpgt': 'application/vnd.adobe-page-template+xml',
            'xhtml': 'application/xhtml+xml',
            'otf': 'application/x-font-opentype',
            'ttf': 'application/x-font-ttf',
            'woff': 'font/woff',
            'woff2': 'font/woff2',
        }

        stylesheet_counter = 0
        image_counter = 0
        fonts_counter = 0
        html_counter = len(epub_content_files)
        for epub_include_file in epub_include_files:
            if not epub_include_file.is_file():
                Log.warning(f'Leaving out {epub_include_file}, it could not be downloaded')
                continue
            relative_path = epub_include_file.relative_to(book_directory).as_posix()
            relative_oebps_path = posixpath.relpath(relative_path, 'OEBPS')

            file_ext = str(epub_include_file).rsplit('.', maxsplit=1)[-1].lower()
            if file_ext not in mimetype_dict:
                Log.warning(f'Unknown file type {file_ext} of {epub_include_file}, adding it as binary data')
            media_type = mimetype_dict.get(file_ext, 'application/octet-stream')
            idx = 0
            file_type = 'unknown'
            if file_ext in ['css', 'xpgt']:
                file_type = 'stylesheet'
                stylesheet_counter += 1
                idx = stylesheet_counter
            elif file_ext in ['png', 'jpg', 'jpeg', 'gif', 'svg']:
                file_type = 'image'
                image_counter += 1
                idx = image_counter
            elif file_ext == 'xhtml':
                file_type = 'html'
                html_counter += 1
                idx = html_counter
                Log.warning(f'Found unexpected HTML include {epub_include_file}')
            elif file_ext in ['otf', 'ttf', 'woff', 'woff2']:
                file_type = 'fonts'
                fonts_counter += 1
                idx = fonts_counter

            epub_manifest += f'<item id="{file_type}{idx}" href="{relative_oebps_path}" media-type="{media_type}"/>\n'

            # Maybe add html files to index? I'm not sure if included html needs to be added
            # epub_spine += f'<itemref idref="html{file_type}{idx}" />'
            epub.write(str(epub_include_file), relative_path)

        epub_manifest += '<item href="toc.ncx" id="ncx" media-type="application/x-dtbncx+xml"/>'

        epub_uuid = str(uuid.UUID(hashlib.sha256(self.book_info.book_id.encode()).hexdigest()[:32]))
        # <dc:language>{self.escape(language)}</dc:language>
        content_tpl = f'''<?xml version='1.0' encoding='UTF-8'?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="id">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{self.escape(book_title)}</dc:title>
    <dc:date>{self.escape(publication_year)}</dc:date>
    <dc:identifier
        id="id"
        opf:scheme="uuid">{epub_uuid}</dc:identifier>
    {creators}
    </metadata>
    <manifest>
        {epub_manifest}
    </manifest>
    <spine toc="ncx">
        {epub_spine}
    </spine>
</package>
'''
        epub.writestr('OEBPS/content.opf', self.prettify_xml(content_tpl))

        # OEBPS/toc.ncx
        authors_display = " and ".join(authors) if len(authors) > 1 else authors[0]

        all_sections = self.book_info.pages_info_json.get('sections', [])
        for entry in all_sections:
            self.fix_paths_in_structure(entry)

        nav_points = '\n'.join(self.build_nav_points(entry) for entry in all_sections)

        toc_tpl = f'''<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="en-US">
    <head>
        <meta name="dtb:totalPageCount" content="0"/>
    </head>
    <docTitle>
        <text>{book_title}</text>
    </docTitle>
    <docAuthor>
        <text>{authors_display}</text>
    </docAuthor>
    <navMap>
    {nav_points}
    </navMap>
</ncx>
'''
        epub.writestr('OEBPS/toc.ncx', self.prettify_xml(toc_tpl))

    @staticmethod
    def fix_paths_in_structure(nav_dic) -> str:
        artifact_id = nav_dic.get('artifactId')
        split = artifact_id.split('/', 1)
        nav_dic['artifactId'] = split[0].replace("epub", "EPUB") + "/" + split[1]

        for entry in nav_dic.get('children', []) or []:
            Ebsco2Downloader.fix_paths_in_structure(entry)

    @staticmethod
    def build_nav_points(nav_dic) -> str:
        children_xml = "\n".join(
            Ebsco2Downloader.build_nav_points(entry) for entry in nav_dic.get('children', []) or []
        )

        return f'''
        <navPoint id="{nav_dic.get('id')}">
            <navLabel>
                <text>{nav_dic.get('name')}</text>
            </navLabel>
            <content src="{posixpath.relpath(nav_dic.get('artifactId'), "OEBPS")}"/>
            {children_xml}
        </navPoint>
        '''
