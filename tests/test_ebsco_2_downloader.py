import json
import zipfile

import pypdf
import pytest
from fake_ebsco import BOOK_ID, BOOK_TITLE, USER_ID, XHTML_PAGE
from lxml import etree

from ebsco_dl.ebsco_2_downloader import DOWNLOAD_REPORT_NAME, Ebsco2Downloader
from ebsco_dl.utils import PathTools


def book_dir(workdir):
    return PathTools.path_of_book(str(workdir / 'books'), BOOK_TITLE)


def epub_path(workdir):
    return book_dir(workdir).with_name(book_dir(workdir).name + '.epub')


def pdf_path(workdir):
    return book_dir(workdir).with_name(book_dir(workdir).name + '.pdf')


def report_path(workdir):
    return book_dir(workdir) / DOWNLOAD_REPORT_NAME


def read_report(workdir):
    return json.loads(report_path(workdir).read_text(encoding='utf-8'))


def add_pages(server, count):
    """Extends an epub fake with further chapters, useful to have several pages fail at the same time."""
    for idx in range(len(server.page_ids) + 1, count + 1):
        page_id = f'EPUB/xhtml/ch{idx}.xhtml'
        server.page_ids.append(page_id)
        server.artifacts[page_id] = XHTML_PAGE.format(title=f'Chapter {idx}').encode()
        server.sections[0]['children'].append(
            {'id': f'sec{idx}', 'name': f'Chapter {idx}', 'artifactId': f'epub/xhtml/ch{idx}.xhtml'}
        )


class TestUrlParsing:
    def test_reads_ids_and_format_from_viewer_url(self, workdir):
        downloader = Ebsco2Downloader(
            str(workdir), f'https://research.ebsco.com/c/{USER_ID}/ebook-viewer/pdf/{BOOK_ID}?x=1', False
        )
        assert downloader.book_info.user_id == USER_ID
        assert downloader.book_info.book_format == 'pdf'
        assert downloader.book_info.book_id == BOOK_ID

    def test_rejects_unknown_url(self, workdir):
        with pytest.raises(NotImplementedError):
            Ebsco2Downloader(str(workdir), 'https://example.com/some/book', False)


def test_extract_artifact_ids_drops_anchors_duplicates_and_preview_limit():
    pages = [
        {'artifactId': 'a.xhtml', 'id': 'p1'},
        {'artifactId': 'a.xhtml#anchor', 'id': 'p1'},
        {'artifactId': 'b.xhtml', 'id': 'p2'},
        {'artifactId': 'previewlimit', 'id': 'previewlimit'},
    ]
    assert Ebsco2Downloader.extract_artifact_ids(pages) == (['a.xhtml', 'b.xhtml'], ['p1', 'p2'])


class TestSession:
    def test_missing_cookies_file_is_reported(self, fake_epub, make_downloader, capsys):
        make_downloader(fake_epub)
        assert 'No cookies file found' in capsys.readouterr().out

    def test_login_redirect_names_target_without_query(self, fake_epub, make_downloader):
        with pytest.raises(RuntimeError, match='redirected to: 127.0.0.1:[0-9]+/login') as error:
            make_downloader(fake_epub).run()
        assert 'secret' not in str(error.value)

    def test_book_info_that_is_no_json_gives_clear_error(self, fake_epub, make_downloader, cookies_file):
        from aiohttp import web

        fake_epub.book_info_override = web.Response(text='<html>Login</html>', content_type='text/html')
        with pytest.raises(RuntimeError, match='book info.*text/html.*instead of JSON'):
            make_downloader(fake_epub).run()

    def test_missing_checkout_token_gives_clear_error(self, fake_epub, make_downloader, cookies_file):
        fake_epub.page_turn = {'error': 'no license available'}
        with pytest.raises(RuntimeError, match='checkout token.*no license available'):
            make_downloader(fake_epub).run()


class TestEpub:
    def test_builds_epub_from_multi_line_xhtml(self, fake_epub, make_downloader, cookies_file, workdir):
        make_downloader(fake_epub).run()

        with zipfile.ZipFile(epub_path(workdir)) as epub:
            names = epub.namelist()
            assert names[0] == 'mimetype'
            assert epub.getinfo('mimetype').compress_type == zipfile.ZIP_STORED
            assert len(names) == len(set(names))
            for expected in [
                'EPUB/xhtml/ch1.xhtml',
                'EPUB/xhtml/ch2.xhtml',
                'EPUB/css/style.css',
                'EPUB/images/figure.png',
                'EPUB/fonts/book.woff',
                'OEBPS/content.opf',
                'OEBPS/toc.ncx',
            ]:
                assert expected in names
            content_opf = etree.fromstring(epub.read('OEBPS/content.opf'))
            toc_ncx = etree.fromstring(epub.read('OEBPS/toc.ncx'))

        opf = {'opf': 'http://www.idpf.org/2007/opf', 'dc': 'http://purl.org/dc/elements/1.1/'}
        assert content_opf.findtext('.//dc:title', namespaces=opf) == BOOK_TITLE
        assert [creator.text for creator in content_opf.findall('.//dc:creator', namespaces=opf)] == [
            'Ada Lovelace',
            'Alan Turing',
        ]
        spine = [item.get('idref') for item in content_opf.findall('.//opf:itemref', namespaces=opf)]
        assert spine == ['html1', 'html2']

        ncx = {'ncx': 'http://www.daisy.org/z3986/2005/ncx/'}
        nav_sources = [content.get('src') for content in toc_ncx.iterfind('.//ncx:content', namespaces=ncx)]
        assert nav_sources == ['../EPUB/xhtml/ch1.xhtml', '../EPUB/xhtml/ch2.xhtml']

    def test_leaves_out_missing_include_and_loads_css_only_once(
        self, fake_epub, make_downloader, cookies_file, workdir
    ):
        make_downloader(fake_epub).run()

        with zipfile.ZipFile(epub_path(workdir)) as epub:
            assert 'EPUB/images/missing.png' not in epub.namelist()
        assert fake_epub.request_counts['EPUB/css/style.css'] == 1

    def test_retries_page_that_hangs_or_is_busy(self, fake_epub, make_downloader, cookies_file, workdir):
        fake_epub.failures['EPUB/xhtml/ch1.xhtml'] = ['hang', 503]
        fake_epub.failures['EPUB/fonts/book.woff'] = [429]

        make_downloader(fake_epub).run()

        assert fake_epub.request_counts['EPUB/xhtml/ch1.xhtml'] == 3
        with zipfile.ZipFile(epub_path(workdir)) as epub:
            assert b'Chapter 1' in epub.read('EPUB/xhtml/ch1.xhtml')
            assert 'EPUB/fonts/book.woff' in epub.namelist()

    def test_page_that_keeps_failing_reports_instead_of_aborting(
        self, fake_epub, make_downloader, cookies_file, workdir
    ):
        fake_epub.failures['EPUB/xhtml/ch2.xhtml'] = [503, 503, 503]

        result = make_downloader(fake_epub).run()

        assert result['pages']['missing'] == 1
        assert not epub_path(workdir).exists()
        # The page that worked stays on disk, so a rerun does not download it again.
        assert (book_dir(workdir) / 'EPUB/xhtml/ch1.xhtml').is_file()

    def test_missing_page_is_written_to_a_json_report(self, fake_epub, make_downloader, cookies_file, workdir):
        fake_epub.persistent_failures['EPUB/xhtml/ch2.xhtml'] = 503

        make_downloader(fake_epub).run()

        report = read_report(workdir)
        assert report['pages']['total'] == 2
        assert report['pages']['downloaded'] == 1
        assert report['pages']['missing'] == 1
        assert report['resumable'] is True
        assert report['epubWritten'] is False
        assert report['pages']['missingPages'] == [
            {
                'path': 'EPUB/xhtml/ch2.xhtml',
                'url': fake_epub.base_url + '/edge/v1/artifact/EPUB%2Fxhtml%2Fch2.xhtml/checkout-token-1',
                'reason': '503 Service Unavailable',
            }
        ]
        assert not epub_path(workdir).exists()

    def test_hanging_page_is_reported_as_missing(self, fake_epub, make_downloader, cookies_file, workdir):
        fake_epub.persistent_failures['EPUB/xhtml/ch2.xhtml'] = 'hang'

        make_downloader(fake_epub).run()

        report = read_report(workdir)
        assert [page['path'] for page in report['pages']['missingPages']] == ['EPUB/xhtml/ch2.xhtml']
        assert report['pages']['missingPages'][0]['reason']
        assert not epub_path(workdir).exists()

    def test_report_separates_missing_pages_from_skipped_includes(
        self, fake_epub, make_downloader, cookies_file, workdir
    ):
        fake_epub.persistent_failures['EPUB/xhtml/ch2.xhtml'] = 503
        fake_epub.persistent_failures['EPUB/fonts/book.woff'] = 503

        result = make_downloader(fake_epub).run()

        report = read_report(workdir)
        assert [page['path'] for page in report['pages']['missingPages']] == ['EPUB/xhtml/ch2.xhtml']
        # Includes stay best effort: a broken font is reported, but never counted as a missing page.
        skipped = {include['path']: include['reason'] for include in report['includes']['skippedIncludes']}
        assert skipped['EPUB/fonts/book.woff'] == '503 Service Unavailable'
        assert skipped['EPUB/images/missing.png'] == '404 Not Found'
        assert report['includes']['skipped'] == 2
        assert result['includes']['skipped'] == 2
        assert not epub_path(workdir).exists()

    def test_rerun_only_fetches_the_missing_page_and_removes_the_report(
        self, fake_epub, make_downloader, cookies_file, workdir
    ):
        fake_epub.persistent_failures['EPUB/xhtml/ch2.xhtml'] = 503
        make_downloader(fake_epub).run()
        first_run_counts = dict(fake_epub.request_counts)
        assert report_path(workdir).is_file()

        del fake_epub.persistent_failures['EPUB/xhtml/ch2.xhtml']
        result = make_downloader(fake_epub).run()

        assert result is None
        assert fake_epub.request_counts['EPUB/xhtml/ch1.xhtml'] == first_run_counts['EPUB/xhtml/ch1.xhtml']
        assert fake_epub.request_counts['EPUB/fonts/book.woff'] == first_run_counts['EPUB/fonts/book.woff']
        assert fake_epub.request_counts['EPUB/css/style.css'] == first_run_counts['EPUB/css/style.css']
        assert fake_epub.request_counts['EPUB/xhtml/ch2.xhtml'] == first_run_counts['EPUB/xhtml/ch2.xhtml'] + 1
        assert not report_path(workdir).exists()
        with zipfile.ZipFile(epub_path(workdir)) as epub:
            assert b'Chapter 1' in epub.read('EPUB/xhtml/ch1.xhtml')
            assert b'Chapter 2' in epub.read('EPUB/xhtml/ch2.xhtml')
            assert 'EPUB/fonts/book.woff' in epub.namelist()

    def test_successful_run_writes_no_report(self, fake_epub, make_downloader, cookies_file, workdir):
        assert make_downloader(fake_epub).run() is None
        assert epub_path(workdir).is_file()
        assert not report_path(workdir).exists()

    def test_skipped_include_alone_still_produces_the_epub_without_report(
        self, fake_epub, make_downloader, cookies_file, workdir
    ):
        # missing.png is always a 404, so the include gap exists although every page worked.
        assert make_downloader(fake_epub).run() is None

        with zipfile.ZipFile(epub_path(workdir)) as epub:
            assert 'EPUB/images/missing.png' not in epub.namelist()
        assert not report_path(workdir).exists()

    def test_every_page_failure_appears_once_in_the_report(self, fake_epub, make_downloader, cookies_file, workdir):
        add_pages(fake_epub, 4)
        fake_epub.persistent_failures['EPUB/xhtml/ch2.xhtml'] = 503
        fake_epub.persistent_failures['EPUB/xhtml/ch4.xhtml'] = 'hang'

        make_downloader(fake_epub).run()

        report = read_report(workdir)
        missing = [page['path'] for page in report['pages']['missingPages']]
        assert sorted(missing) == ['EPUB/xhtml/ch2.xhtml', 'EPUB/xhtml/ch4.xhtml']
        assert len(missing) == len(set(missing))
        assert report['pages']['total'] == 4
        assert report['pages']['downloaded'] == 2

    def test_cached_page_counts_as_downloaded_and_keeps_its_includes(
        self, fake_epub, make_downloader, cookies_file, workdir
    ):
        pages_dir = book_dir(workdir) / 'EPUB/xhtml'
        pages_dir.mkdir(parents=True)
        (pages_dir / 'ch1.xhtml').write_text(XHTML_PAGE.format(title='Chapter 1'), encoding='utf-8')

        fake_epub.persistent_failures['EPUB/xhtml/ch2.xhtml'] = 503
        make_downloader(fake_epub).run()

        assert 'EPUB/xhtml/ch1.xhtml' not in fake_epub.request_counts
        report = read_report(workdir)
        assert report['pages']['downloaded'] == 1
        # The includes of the cached page are still known, so they are downloaded for the next run.
        assert fake_epub.request_counts['EPUB/images/figure.png'] == 1

    def test_broken_cached_page_is_downloaded_again(self, fake_epub, make_downloader, cookies_file, workdir):
        pages_dir = book_dir(workdir) / 'EPUB/xhtml'
        pages_dir.mkdir(parents=True)
        (pages_dir / 'ch1.xhtml').write_text('<html>truncated', encoding='utf-8')

        make_downloader(fake_epub).run()

        assert fake_epub.request_counts['EPUB/xhtml/ch1.xhtml'] == 1
        assert b'Chapter 1' in (pages_dir / 'ch1.xhtml').read_bytes()


class TestPdf:
    def test_merges_parts_with_outline(self, fake_pdf, make_downloader, cookies_file, workdir):
        make_downloader(fake_pdf).run()

        reader = pypdf.PdfReader(pdf_path(workdir))
        assert len(reader.pages) == 6
        outline = reader.outline
        assert [item.title for item in outline if not isinstance(item, list)] == ['Chapter 1', 'Chapter 2']
        assert [item.title for item in outline[-1]] == ['Section 2.1']
        assert reader.get_destination_page_number(outline[-1][0]) == 4

    def test_retries_part_that_hangs_or_is_busy(self, fake_pdf, make_downloader, cookies_file, workdir, capsys):
        fake_pdf.failures['pdf/part2.pdf'] = ['hang', 503]

        make_downloader(fake_pdf).run()

        assert len(pypdf.PdfReader(pdf_path(workdir)).pages) == 6
        output = capsys.readouterr().out
        assert '(Try 1 of 3) Unable to download part2.pdf' in output
        assert '(Try 2 of 3) Unable to download part2.pdf: 503' in output

    def test_part_that_keeps_failing_aborts_without_leftovers(
        self, fake_pdf, make_downloader, cookies_file, workdir, capsys
    ):
        fake_pdf.failures['pdf/part3.pdf'] = ['hang', 'hang', 'hang']

        with pytest.raises(SystemExit):
            make_downloader(fake_pdf).run()

        assert 'Could not download pdf/part3.pdf' in capsys.readouterr().out
        assert not pdf_path(workdir).exists()
        assert sorted(path.name for path in book_dir(workdir).iterdir()) == ['part1.pdf', 'part2.pdf']

    def test_resumes_cut_download_where_it_stopped(self, fake_pdf, make_downloader, cookies_file, workdir):
        fake_pdf.supports_ranges = True
        fake_pdf.failures['pdf/part2.pdf'] = ['cut']
        half = len(fake_pdf.artifacts['pdf/part2.pdf']) // 2

        make_downloader(fake_pdf).run()

        assert fake_pdf.range_headers['pdf/part2.pdf'] == [f'bytes={half}-']
        assert (book_dir(workdir) / 'part2.pdf').read_bytes() == fake_pdf.artifacts['pdf/part2.pdf']
        assert len(pypdf.PdfReader(pdf_path(workdir)).pages) == 6

    def test_starts_over_when_server_ignores_range(self, fake_pdf, make_downloader, cookies_file, workdir):
        fake_pdf.failures['pdf/part2.pdf'] = ['cut', 'cut']

        make_downloader(fake_pdf).run()

        assert fake_pdf.request_counts['pdf/part2.pdf'] == 3
        assert (book_dir(workdir) / 'part2.pdf').read_bytes() == fake_pdf.artifacts['pdf/part2.pdf']

    def test_not_found_part_is_not_retried(self, fake_pdf, make_downloader, cookies_file, workdir):
        fake_pdf.failures['pdf/part1.pdf'] = [404]

        with pytest.raises(SystemExit):
            make_downloader(fake_pdf).run()
        assert fake_pdf.request_counts['pdf/part1.pdf'] == 1

    def test_rerun_keeps_finished_parts_and_replaces_aborted_ones(
        self, fake_pdf, make_downloader, cookies_file, workdir
    ):
        parts_dir = book_dir(workdir)
        parts_dir.mkdir(parents=True)
        (parts_dir / 'part1.pdf').write_bytes(fake_pdf.artifacts['pdf/part1.pdf'])
        (parts_dir / 'part2.pdf.part').write_bytes(b'%PDF-half written')

        make_downloader(fake_pdf).run()

        assert 'pdf/part1.pdf' not in fake_pdf.request_counts
        assert fake_pdf.request_counts['pdf/part2.pdf'] == 1
        assert not list(parts_dir.glob('*.part'))
        assert len(pypdf.PdfReader(pdf_path(workdir)).pages) == 6
