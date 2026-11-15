import os
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(TESTS_DIR)

for _name in (
    'service',
    'snapshotting',
    'datagather_modules',
    'pagesearch',
    'dorking',
    'apis',
    'reporting_modules',
):
    _path = os.path.join(BACKEND_DIR, _name)
    if _path not in sys.path:
        sys.path.insert(0, _path)

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

from wayback_fixtures import write_snapshot_config  # noqa: E402


@pytest.fixture
def wayback_env(tmp_path, monkeypatch):
    """Run snapshot code from a temporary cwd with a deterministic config."""
    monkeypatch.chdir(tmp_path)
    write_snapshot_config(tmp_path, retries=2, pause=0)
    monkeypatch.setattr(
        'archive_snapshotting.read_config',
        lambda: {
            'wayback_retries_amount': '2',
            'wayback_requests_pause': '0',
            'installed_browser': 'firefox',
        },
    )
    monkeypatch.setattr('archive_snapshotting.time.sleep', lambda *_: None)

    report_folder = tmp_path / 'report_examplecom_(01-01-2024, 00h00m00s)'
    report_folder.mkdir()
    return {'tmp_path': tmp_path, 'report_folder': str(report_folder)}
