import requests
import os
import re
import time
import sys
from colorama import Fore, Style
from config_processing import read_config

sys.path.append('service')
CDX_API = "http://web.archive.org/cdx/search/cdx"

# Outcome of a whole download batch.
STATUS_NO_SNAPSHOTS = 'no_snapshots'
STATUS_ALL_FAILED = 'all_failed'
STATUS_PARTIAL_SUCCESS = 'partial_success'
STATUS_SUCCESS = 'success'
STATUS_CDX_ERROR = 'cdx_error'
STATUS_ERROR = 'error'
STATUS_NOT_REQUESTED = 'not_requested'

# Failure categories that callers can rely on. Parse and write failures share a
# single category so that reports can distinguish transport problems (HTTP /
# timeout) from problems while processing or storing the response.
FAILURE_HTTP_ERROR = 'http_error'
FAILURE_TIMEOUT = 'timeout'
FAILURE_PARSE_ERROR = 'parse_error'


class SnapshotDownloadResult(dict):
    """Structured ``download_snapshot`` result.

    Behaves like a regular dict (so it can be embedded in the data array passed
    to the report renderer and serialized as JSON) and also exposes the same
    fields as attributes for convenience, e.g. ``result.downloaded_files``.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    @property
    def downloaded(self):
        return self.get('downloaded_files', [])

    @property
    def failed_entries(self):
        return self.get('failed', [])

    @property
    def total(self):
        return self.get('total_candidates', 0)


def get_values_from_config():
    try:
        config_values = read_config()
        retries = int(config_values['wayback_retries_amount'])
        pause_between_requests = int(config_values['wayback_requests_pause'])
    except Exception as exc:
        print(Fore.RED + f"Unable to read snapshotting config ({exc}); using defaults" + Style.RESET_ALL)
        return 3, 2
    return retries, pause_between_requests


def get_snapshots(url, from_date, to_date):
    params = {
        "url": url,
        "from": from_date,
        "to": to_date,
        "output": "json",
        "fl": "timestamp,original,mime",
        "filter": "statuscode:200",
        "collapse": "digest"
    }
    print(Fore.GREEN + f"Sending request to Wayback CDX API for {url}, period: {from_date} - {to_date}..." + Style.RESET_ALL)
    response = requests.get(CDX_API, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    return data[1:]


def inject_base_tag(html_text: str, base_url: str) -> str:
    base_tag = f'<base href="{base_url}">'
    if re.search(r'<head[^>]*>', html_text, re.IGNORECASE):
        return re.sub(r'(<head[^>]*>)', r'\1' + base_tag, html_text, count=1, flags=re.IGNORECASE)
    elif re.search(r'<html[^>]*>', html_text, re.IGNORECASE):
        return re.sub(r'(<html[^>]*>)', r'\1<head>' + base_tag + '</head>', html_text, count=1, flags=re.IGNORECASE)
    else:
        return base_tag + html_text


def build_archive_url(timestamp, original_url):
    return f"https://web.archive.org/web/{timestamp}id_/{original_url}"


def is_html_snapshot(entry, short_domain):
    """Whether a CDX row points to an HTML page that should be downloaded."""
    if not isinstance(entry, (list, tuple)) or len(entry) < 2:
        return False
    original_url = entry[1]
    if not isinstance(original_url, str) or not original_url:
        return False
    return (
        original_url.endswith(".html")
        or original_url.endswith("/")
        or original_url == short_domain
    )


def categorize_failure(exc):
    """Map an exception to one of the stable failure categories plus a message."""
    if isinstance(exc, requests.exceptions.Timeout):
        return FAILURE_TIMEOUT, _exception_text(exc, 'Request timed out')
    if isinstance(exc, requests.exceptions.HTTPError):
        status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
        if status_code:
            return FAILURE_HTTP_ERROR, f"HTTP {status_code}: {exc}"
        return FAILURE_HTTP_ERROR, _exception_text(exc, 'HTTP error')
    if isinstance(exc, requests.exceptions.RequestException):
        return FAILURE_HTTP_ERROR, f"{type(exc).__name__}: {exc}"
    return FAILURE_PARSE_ERROR, _exception_text(exc, 'Failed to parse or save the snapshot')


def _exception_text(exc, fallback):
    text = str(exc).strip()
    if not text:
        return f"{fallback} ({type(exc).__name__})"
    return f"{type(exc).__name__}: {text}"


def snapshot_enum(snapshot_storage_folder, timestamp, original_url, index):
    """Download a single archived snapshot.

    Never raises for a single snapshot: returns a dict describing either the
    saved file or the failure (with its category).
    """
    retries, _ = get_values_from_config()
    retries = max(1, int(retries))
    archive_url = build_archive_url(timestamp, original_url)
    filename = f"{index}_{timestamp}.html"
    filepath = os.path.join(snapshot_storage_folder, filename)
    last_reason_type = FAILURE_PARSE_ERROR
    last_reason = 'Unknown error'
    attempts = 0

    for attempt in range(1, retries + 1):
        attempts = attempt
        try:
            response = requests.get(archive_url, timeout=15)
            response.raise_for_status()
            html_content = inject_base_tag(response.text, original_url)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)
            print(Fore.GREEN + f"[{index}] Downloaded: " + Fore.LIGHTCYAN_EX + Style.BRIGHT + f"{archive_url}" + Style.RESET_ALL)
            return {
                'success': True,
                'filename': filename,
                'index': index,
                'timestamp': timestamp,
                'original_url': original_url,
                'archive_url': archive_url,
                'attempts': attempt,
            }
        except Exception as exc:
            # A failure while writing could leave a partial file behind; remove it
            # so the report folder only contains complete snapshots.
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except OSError:
                pass
            last_reason_type, last_reason = categorize_failure(exc)
            if attempt < retries:
                print(Fore.RED + f"[{index}] Attempt {attempt}/{retries} failed for {archive_url} ({last_reason_type}). Retrying..." + Style.RESET_ALL)
                time.sleep(2)
            else:
                print(Fore.RED + f"[{index}] Attempt {attempt}/{retries} failed for {archive_url} ({last_reason_type})." + Style.RESET_ALL)

    print(Fore.RED + f"[{index}] Failed to download after {attempts} attempts: {archive_url}" + Style.RESET_ALL)
    return {
        'success': False,
        'filename': None,
        'index': index,
        'timestamp': timestamp,
        'original_url': original_url,
        'archive_url': archive_url,
        'reason_type': last_reason_type,
        'reason': last_reason,
        'attempts': attempts,
    }


def _empty_result(snapshot_storage_folder, short_domain, from_date, end_date):
    return SnapshotDownloadResult(
        status=STATUS_NO_SNAPSHOTS,
        domain=short_domain,
        from_date=from_date,
        to_date=end_date,
        total_candidates=0,
        unique_candidates=0,
        duplicate_count=0,
        duplicates=[],
        downloaded_files=[],
        failed=[],
        downloaded_count=0,
        failed_count=0,
        snapshot_folder=snapshot_storage_folder,
        summary='No HTML snapshots were available for download.',
        error=None,
    )


def snapshot_error_result(message, domain=None, from_date=None, to_date=None, snapshot_folder=None):
    """Result used when snapshotting itself could not be started or crashed."""
    return SnapshotDownloadResult(
        status=STATUS_ERROR,
        domain=domain,
        from_date=from_date,
        to_date=to_date,
        total_candidates=0,
        unique_candidates=0,
        duplicate_count=0,
        duplicates=[],
        downloaded_files=[],
        failed=[],
        downloaded_count=0,
        failed_count=0,
        snapshot_folder=snapshot_folder,
        summary=f"Snapshot download could not be completed: {message}",
        error=message,
    )


def snapshot_not_requested_result(domain=None):
    """Placeholder result for scans that did not enable Wayback snapshotting."""
    return SnapshotDownloadResult(
        status=STATUS_NOT_REQUESTED,
        domain=domain,
        from_date=None,
        to_date=None,
        total_candidates=0,
        unique_candidates=0,
        duplicate_count=0,
        duplicates=[],
        downloaded_files=[],
        failed=[],
        downloaded_count=0,
        failed_count=0,
        snapshot_folder=None,
        summary='Wayback snapshotting was not requested for this scan.',
        error=None,
    )


def _build_summary(result):
    status = result['status']
    downloaded = result['downloaded_count']
    failed = result['failed_count']
    unique = result['unique_candidates']

    if status == STATUS_NO_SNAPSHOTS:
        summary = 'No HTML snapshots were available for download.'
    elif status == STATUS_CDX_ERROR:
        summary = f"Wayback CDX query failed: {result.get('error')}"
    elif status == STATUS_ERROR:
        summary = f"Snapshot download could not be completed: {result.get('error')}"
    elif status == STATUS_ALL_FAILED:
        summary = f"All {failed} snapshot download(s) failed."
    elif status == STATUS_PARTIAL_SUCCESS:
        summary = f"{downloaded} of {unique} snapshot(s) downloaded; {failed} failed."
    else:
        summary = f"All {downloaded} snapshot(s) downloaded successfully."

    if result['duplicate_count']:
        summary += f" {result['duplicate_count']} duplicate timestamp(s) were skipped."
    return summary


def download_snapshot(short_domain, from_date, end_date, report_folder):
    """Download every HTML snapshot returned by the Wayback CDX API.

    Always returns a :class:`SnapshotDownloadResult` (never ``None``). A failure
    for one snapshot does not stop the rest of the batch; the result carries the
    downloaded file names, the failed entries with their reason category and the
    number of candidate/duplicate entries.
    """
    _, pause_between_requests = get_values_from_config()
    pause_between_requests = max(0, int(pause_between_requests))
    snapshot_storage_folder = os.path.join(report_folder, 'wayback_snapshots')
    os.makedirs(snapshot_storage_folder, exist_ok=True)

    result = _empty_result(snapshot_storage_folder, short_domain, from_date, end_date)

    try:
        snapshots = get_snapshots(short_domain, from_date, end_date)
    except Exception as exc:
        result['status'] = STATUS_CDX_ERROR
        result['error'] = f"{type(exc).__name__}: {exc}"
        result['summary'] = _build_summary(result)
        print(Fore.RED + f"Unable to query Wayback CDX API: {result['error']}" + Style.RESET_ALL)
        return result

    if not snapshots:
        snapshots = []

    html_snapshots = [entry for entry in snapshots if is_html_snapshot(entry, short_domain)]
    result['total_candidates'] = len(html_snapshots)
    print(Fore.GREEN + "Total snapshots found:" + Style.RESET_ALL + Fore.LIGHTCYAN_EX + Style.BRIGHT + f" {len(snapshots)}" + Style.RESET_ALL)
    print(Fore.GREEN + "HTML snapshots to download:" + Style.RESET_ALL + Fore.LIGHTCYAN_EX + Style.BRIGHT + f" {len(html_snapshots)}\n" + Style.RESET_ALL)

    # A timestamp maps to a single file name, so duplicated CDX rows are counted
    # but downloaded only once.
    unique_candidates = []
    seen_timestamps = set()
    for entry in html_snapshots:
        timestamp = str(entry[0])
        original_url = entry[1]
        if timestamp in seen_timestamps:
            result['duplicates'].append({'timestamp': timestamp, 'original_url': original_url})
            continue
        seen_timestamps.add(timestamp)
        unique_candidates.append((timestamp, original_url))

    result['unique_candidates'] = len(unique_candidates)
    result['duplicate_count'] = len(result['duplicates'])

    if not unique_candidates:
        result['status'] = STATUS_NO_SNAPSHOTS
        result['summary'] = _build_summary(result)
        print(Fore.RED + "No HTML snapshots available for download." + Style.RESET_ALL)
        return result

    for index, (timestamp, original_url) in enumerate(unique_candidates, start=1):
        outcome = snapshot_enum(snapshot_storage_folder, timestamp, original_url, index)
        if outcome['success']:
            result['downloaded_files'].append(outcome['filename'])
        else:
            result['failed'].append({
                'timestamp': outcome['timestamp'],
                'original_url': outcome['original_url'],
                'url': outcome['original_url'],
                'archive_url': outcome['archive_url'],
                'reason_type': outcome['reason_type'],
                'reason': outcome['reason'],
                'attempts': outcome['attempts'],
            })
        if pause_between_requests and index < len(unique_candidates):
            time.sleep(pause_between_requests)

    result['downloaded_count'] = len(result['downloaded_files'])
    result['failed_count'] = len(result['failed'])
    if not result['downloaded_files']:
        result['status'] = STATUS_ALL_FAILED
    elif result['failed']:
        result['status'] = STATUS_PARTIAL_SUCCESS
    else:
        result['status'] = STATUS_SUCCESS
    result['summary'] = _build_summary(result)

    color = Fore.GREEN if result['status'] in (STATUS_SUCCESS, STATUS_PARTIAL_SUCCESS) else Fore.RED
    print(color + f"\n{result['summary']}" + Style.RESET_ALL)
    print(Fore.GREEN + "Finished downloading HTML snapshots" + Style.RESET_ALL)
    return result
