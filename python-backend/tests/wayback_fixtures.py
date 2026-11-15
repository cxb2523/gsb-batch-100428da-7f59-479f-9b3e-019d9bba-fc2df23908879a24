"""Local fixtures that replace the HTTP layer used by archive_snapshotting.

No test in this package touches the real Wayback API: ``FakeWaybackSession``
serves a CDX response and per-snapshot responses from in-memory dictionaries.
"""

import os
import sys

import requests

SNAPSHOTTING_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.dirname(SNAPSHOTTING_DIR)

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if os.path.join(BACKEND_DIR, 'service') not in sys.path:
    sys.path.insert(0, os.path.join(BACKEND_DIR, 'service'))
if SNAPSHOTTING_DIR not in sys.path:
    sys.path.insert(0, SNAPSHOTTING_DIR)


class FakeResponse:
    def __init__(self, status_code=200, text='', json_payload=None, json_error=None,
                 text_error=None):
        self.status_code = status_code
        self._text = text
        self._json_payload = json_payload
        self._json_error = json_error
        self._text_error = text_error

    @property
    def text(self):
        if self._text_error is not None:
            raise self._text_error
        return self._text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Client Error",
                response=self,
            )

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._json_payload


class FakeWaybackSession:
    """Stands in for ``requests.get`` inside archive_snapshotting.

    ``cdx_response`` is returned for the CDX API call. ``snapshot_responses``
    maps ``(timestamp, original_url)`` to a FakeResponse or to an Exception
    instance (raised), which lets tests simulate HTTP errors, timeouts and
    unparsable payloads without any network access.
    """

    def __init__(self, cdx_response=None, snapshot_responses=None, default_snapshot_response=None):
        self.cdx_response = cdx_response
        self.snapshot_responses = snapshot_responses or {}
        self.default_snapshot_response = default_snapshot_response
        self.calls = []

    def __call__(self, url, params=None, timeout=None, **kwargs):
        self.calls.append({'url': url, 'params': params, 'timeout': timeout})
        if 'cdx/search/cdx' in url:
            return self._resolve(self.cdx_response, is_cdx=True)
        for (timestamp, original_url), response in self.snapshot_responses.items():
            if f"/web/{timestamp}id_/{original_url}" in url:
                return self._resolve(response)
        if self.default_snapshot_response is not None:
            return self._resolve(self.default_snapshot_response)
        return FakeResponse(status_code=404, text='not archived')

    @staticmethod
    def _resolve(response, is_cdx=False):
        if isinstance(response, BaseException):
            raise response
        if response is None:
            if is_cdx:
                return FakeResponse(json_payload=[['timestamp', 'original', 'mime']])
            return FakeResponse(status_code=404, text='not archived')
        return response

    @property
    def snapshot_call_count(self):
        return len([c for c in self.calls if 'cdx/search/cdx' not in c['url']])


def install_fake_requests(monkeypatch, session):
    """Route ``archive_snapshotting.requests.get`` through ``session``."""
    import archive_snapshotting

    monkeypatch.setattr(archive_snapshotting.requests, 'get', session)
    return session


def write_snapshot_config(tmp_path, retries=2, pause=0):
    """Create a minimal config.ini so archive_snapshotting reads a fixed config."""
    service_dir = tmp_path / 'service'
    service_dir.mkdir(parents=True, exist_ok=True)
    config_path = service_dir / 'config.ini'
    config_path.write_text(
        "[SNAPSHOTTING]\n"
        "installed_browser = firefox\n"
        "opera_browser_path = None\n"
        f"wayback_retries = {retries}\n"
        f"wayback_req_pause = {pause}\n",
        encoding='utf-8',
    )
    return config_path


def cdx_payload(rows):
    """Build a CDX JSON payload: header row followed by data rows."""
    return [['timestamp', 'original', 'mime']] + [list(row) for row in rows]
