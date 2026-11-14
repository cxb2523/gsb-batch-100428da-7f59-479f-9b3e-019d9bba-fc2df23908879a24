import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from cninfo_api import CNInfoAPI


class _FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} Client Error')

    def iter_content(self, chunk_size=64 * 1024):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    """伪造的 session：依次返回预设响应或抛出预设异常，记录调用次数。"""

    def __init__(self, responses=None, exc=None):
        self.responses = list(responses or [])
        self.exc = exc
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        index = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[index]


class CNInfoDownloadTest(unittest.TestCase):
    def test_rejects_html_error_page_and_keeps_no_fake_pdf(self):
        api = CNInfoAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'report.pdf'
            fake = _FakeResponse(b'<html>error</html>')
            with patch('cninfo_api.REQUEST_INTERVAL', 0):
                with patch.object(api.session, 'get', return_value=fake):
                    ok = api.download_pdf('finalpage/2025/x.PDF', str(save_path))
            self.assertFalse(ok)
            self.assertFalse(save_path.exists())
            self.assertFalse(list(Path(temp_dir).glob('*.part')))

    def test_html_error_page_is_classified_as_not_pdf(self):
        api = CNInfoAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'report.pdf'
            session = _FakeSession(responses=[_FakeResponse(b'<html>error</html>')])
            error_info = {}
            with patch('cninfo_api.REQUEST_INTERVAL', 0):
                with patch.object(api.session, 'get', new=session.get):
                    ok = api.download_pdf(
                        'finalpage/2025/x.PDF',
                        str(save_path),
                        error_info=error_info,
                    )
            self.assertFalse(ok)
            self.assertEqual(error_info['category'], 'not_pdf')
            self.assertIn('PDF', error_info['message'])
            self.assertFalse(save_path.exists())
            self.assertFalse(list(Path(temp_dir).glob('*.part')))

    def test_network_failure_reports_category_and_leaves_no_files(self):
        api = CNInfoAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'report.pdf'
            session = _FakeSession(exc=requests.ConnectionError('连接被拒绝'))
            error_info = {}
            with patch('cninfo_api.REQUEST_INTERVAL', 0):
                with patch.object(api.session, 'get', new=session.get):
                    ok = api.download_pdf(
                        'finalpage/2025/x.PDF',
                        str(save_path),
                        error_info=error_info,
                    )
            self.assertFalse(ok)
            self.assertEqual(error_info['category'], 'network')
            self.assertIn('连接被拒绝', error_info['message'])
            self.assertGreater(session.calls, 1)
            self.assertFalse(save_path.exists())
            self.assertFalse(list(Path(temp_dir).glob('*.part')))

    def test_http_error_is_classified_as_http(self):
        api = CNInfoAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'report.pdf'
            session = _FakeSession(
                responses=[_FakeResponse(b'', status_code=500)],
            )
            error_info = {}
            with patch('cninfo_api.REQUEST_INTERVAL', 0):
                with patch.object(api.session, 'get', new=session.get):
                    ok = api.download_pdf(
                        'finalpage/2025/x.PDF',
                        str(save_path),
                        error_info=error_info,
                    )
            self.assertFalse(ok)
            self.assertEqual(error_info['category'], 'http')
            self.assertIn('500', error_info['message'])

    def test_writes_valid_pdf_atomically_and_skips_second_download(self):
        api = CNInfoAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'report.pdf'
            fake = _FakeResponse(b'%PDF-1.7\ncontent')
            with patch.object(api.session, 'get', return_value=fake) as mocked:
                self.assertTrue(api.download_pdf('finalpage/2025/x.PDF', str(save_path)))
                self.assertTrue(save_path.read_bytes().startswith(b'%PDF-'))
                self.assertTrue(api.download_pdf('finalpage/2025/x.PDF', str(save_path)))
                self.assertEqual(mocked.call_count, 1)

    def test_success_does_not_touch_error_info(self):
        api = CNInfoAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / 'report.pdf'
            fake = _FakeResponse(b'%PDF-1.7\ncontent')
            error_info = {}
            with patch.object(api.session, 'get', return_value=fake):
                self.assertTrue(
                    api.download_pdf(
                        'finalpage/2025/x.PDF',
                        str(save_path),
                        error_info=error_info,
                    )
                )
            self.assertEqual(error_info, {})


class DefaultSourceTest(unittest.TestCase):
    def test_cli_defaults_to_neeq(self):
        import main

        with patch('sys.argv', ['main.py']):
            args = main.parse_args()
        self.assertEqual(args.source, 'neeq')


class CNInfoReportYearTest(unittest.TestCase):
    def test_process_announcements_uses_inferred_report_year(self):
        import main
        from data_exporter import DataExporter

        announcement = {
            'secCode': '001234',
            'secName': '测试公司',
            'announcementTitle': '测试公司：2024年年度报告',
            'announcementDate': '2025-04-20',
            'adjunctUrl': 'finalpage/x.PDF',
            'adjunctType': 'PDF',
        }
        args = SimpleNamespace(year=2025)
        captured = {}

        def fake_download(_url, save_path, error_info=None):
            captured['pdf'] = save_path
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            Path(save_path).write_bytes(b'%PDF-1.7\n')
            return True

        def fake_parse(_path):
            return {'balance_sheet': None, 'income_statement': None, 'cash_flow': None}

        def fake_export(_self, reports, stock_code, stock_name, year):
            captured['year'] = year

        with tempfile.TemporaryDirectory() as temp_dir:
            exporter = DataExporter()
            exporter.pdf_dir = str(Path(temp_dir) / 'pdf')
            exporter.csv_dir = str(Path(temp_dir) / 'csv')
            api = SimpleNamespace(download_pdf=fake_download)
            parser = SimpleNamespace(parse_pdf=fake_parse)
            logger = SimpleNamespace(
                info=lambda *a, **k: None,
                warning=lambda *a, **k: None,
            )
            with patch.object(DataExporter, 'export_all_reports', fake_export):
                with patch.object(main, 'STOP_FILE', str(Path(temp_dir) / 'STOP.txt')):
                    main.process_announcements(
                        [announcement],
                        args,
                        api,
                        parser,
                        exporter,
                        logger,
                        source='cninfo',
                    )
        self.assertEqual(captured['year'], 2024)
        self.assertIn('2024', captured['pdf'])


if __name__ == '__main__':
    unittest.main()
