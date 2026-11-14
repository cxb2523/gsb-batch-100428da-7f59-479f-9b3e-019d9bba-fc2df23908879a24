"""Tests for auto-update-episodes using a stub driver / fake edit pages.

These tests never touch the real TMDB site or a real browser; the stub only
stands in for the Selenium layer so the verification logic can be exercised.
"""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest

from selenium.common.exceptions import NoSuchElementException

MODULE_PATH = os.path.join(os.path.dirname(__file__), 'auto-update-episodes.py')
spec = importlib.util.spec_from_file_location('auto_update_episodes', MODULE_PATH)
aue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aue)


class FakeElement:
    def __init__(self, text='', displayed=True, transform=None, on_click=None):
        self.value = ''
        self._text = text
        self._displayed = displayed
        self._transform = transform  # simulates a field rejecting typed input
        self._on_click = on_click
        self.clicked = False

    def clear(self):
        self.value = ''

    def send_keys(self, text):
        self.value += text
        if self._transform is not None:
            self.value = self._transform(self.value)

    def get_attribute(self, name):
        return self.value if name == 'value' else None

    def click(self):
        self.clicked = True
        if self._on_click is not None:
            self._on_click()

    def is_displayed(self):
        return self._displayed

    @property
    def text(self):
        return self._text


class FakePage:
    """A fake edit page: form fields plus optional error/success markers."""

    def __init__(self, fields, errors=None, successes=None):
        self.fields = fields          # locator string -> FakeElement
        self.errors = errors or {}    # css selector -> [FakeElement]
        self.successes = successes or {}


class FakeDriver:
    def __init__(self):
        self.pages = {}          # url -> FakePage
        self.current_url = 'about:blank'
        self.page = None
        self.quit_called = False

    def add_page(self, url, page):
        self.pages[url] = page

    def get(self, url):
        self.current_url = url
        self.page = self.pages[url]

    def find_element(self, by, locator):
        if self.page is not None and locator in self.page.fields:
            return self.page.fields[locator]
        raise NoSuchElementException(f'no such element: {locator}')

    def find_elements(self, by, locator):
        if self.page is None:
            return []
        return list(self.page.errors.get(locator, [])) + \
            list(self.page.successes.get(locator, []))

    def quit(self):
        self.quit_called = True


def make_edit_page(driver, on_submit=None, date_transform=None,
                   errors=None, successes=None):
    lang = aue.LANGUAGE_CODE
    fields = {
        f'//*[@id="{lang}_name"]': FakeElement(),
        f'//*[@id="{lang}_overview"]': FakeElement(),
        'air_date': FakeElement(transform=date_transform),
        'runtime': FakeElement(),
        'submit': FakeElement(on_click=lambda: on_submit and on_submit(driver)),
    }
    return FakePage(fields, errors=errors, successes=successes)


def episode_url(number):
    return aue.EPISODE_URL.replace('/episode/1/', f'/episode/{number}/')


class DateParsingTests(unittest.TestCase):
    def test_accepts_all_documented_formats(self):
        self.assertEqual(aue.parse_air_date('2025/1/20'), (2025, 1, 20))
        self.assertEqual(aue.parse_air_date('1/20/2025'), (2025, 1, 20))
        self.assertEqual(aue.parse_air_date('2025-1-20'), (2025, 1, 20))
        self.assertEqual(aue.parse_air_date(' 2025/01/20 '), (2025, 1, 20))

    def test_rejects_invalid_dates(self):
        for bad in ('2025/13/1', '2025/2/30', 'not-a-date', '1/20/25', '', '20/1/2025'):
            with self.assertRaises(ValueError, msg=bad):
                aue.parse_air_date(bad)

    def test_format_follows_language_convention(self):
        ymd = (2025, 1, 20)
        self.assertEqual(aue.format_air_date(ymd, 'zh_TW'), '2025/1/20')
        self.assertEqual(aue.format_air_date(ymd, 'zh_CN'), '2025/1/20')
        self.assertEqual(aue.format_air_date(ymd, 'en_US'), '1/20/2025')
        self.assertEqual(aue.format_air_date(ymd, 'de_DE'), '2025-01-20')

    def test_dates_equal_compares_by_value(self):
        self.assertTrue(aue.dates_equal('2025/1/20', '2025-1-20'))
        self.assertTrue(aue.dates_equal('1/20/2025', '2025/1/20'))
        self.assertFalse(aue.dates_equal('2025/1/20', '2025/1/21'))


class UpdateEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.args = dict(topic='Episode Name', description='Some overview',
                         date_text='2025/1/20', duration='45')

    def update(self, number='5'):
        return aue.update_episode(self.driver, number, self.args['topic'],
                                  self.args['description'], self.args['date_text'],
                                  self.args['duration'])

    def test_success_when_submit_leaves_edit_page(self):
        def on_submit(driver):
            driver.current_url = episode_url('5').replace('/edit', '')
        self.driver.add_page(episode_url('5'), make_edit_page(self.driver, on_submit))
        success, reason = self.update()
        self.assertTrue(success, reason)
        page = self.driver.pages[episode_url('5')]
        self.assertTrue(page.fields['submit'].clicked)
        self.assertEqual(page.fields['air_date'].value, '2025/1/20')

    def test_success_on_success_banner_even_without_redirect(self):
        successes = {'.alert-success': [FakeElement(text='Saved')]}
        self.driver.add_page(episode_url('5'),
                             make_edit_page(self.driver, lambda d: None,
                                            successes=successes))
        success, reason = self.update()
        self.assertTrue(success, reason)

    def test_date_not_taking_effect_skips_submit(self):
        # Field rejects the typed format and stays empty.
        self.driver.add_page(episode_url('5'),
                             make_edit_page(self.driver, date_transform=lambda v: ''))
        success, reason = self.update()
        self.assertFalse(success)
        self.assertIn('air date', reason)
        self.assertFalse(self.driver.pages[episode_url('5')].fields['submit'].clicked)

    def test_field_mismatch_skips_submit(self):
        page = make_edit_page(self.driver)
        page.fields['runtime'] = FakeElement(transform=lambda v: v + '0')
        self.driver.add_page(episode_url('5'), page)
        success, reason = self.update()
        self.assertFalse(success)
        self.assertIn('runtime', reason)
        self.assertFalse(page.fields['submit'].clicked)

    def test_validation_error_after_submit_is_failure(self):
        errors = {'span.error': [FakeElement(text='Air date is invalid')]}
        self.driver.add_page(episode_url('5'),
                             make_edit_page(self.driver, lambda d: None, errors=errors))
        success, reason = self.update()
        self.assertFalse(success)
        self.assertIn('Air date is invalid', reason)

    def test_staying_on_edit_page_times_out_as_failure(self):
        old_timeout = aue.SUBMIT_TIMEOUT
        aue.SUBMIT_TIMEOUT = 1
        try:
            self.driver.add_page(episode_url('5'), make_edit_page(self.driver, lambda d: None))
            success, reason = self.update()
        finally:
            aue.SUBMIT_TIMEOUT = old_timeout
        self.assertFalse(success)
        self.assertIn('timed out', reason)

    def test_invalid_date_input_fails_before_loading_page(self):
        self.args['date_text'] = '2025/13/40'
        success, reason = self.update()
        self.assertFalse(success)
        self.assertIn('invalid air date', reason)
        self.assertEqual(self.driver.current_url, 'about:blank')

    def test_missing_edit_form_is_failure(self):
        self.driver.add_page(episode_url('5'), FakePage({}))
        old = aue.WebDriverWait
        # Avoid a real 30s wait: shrink the form-load timeout.
        aue.WebDriverWait = lambda driver, t: old(driver, 0.5)
        try:
            success, reason = self.update()
        finally:
            aue.WebDriverWait = old
        self.assertFalse(success)
        self.assertIn('edit form did not load', reason)


class MainFlowTests(unittest.TestCase):
    def run_main(self, driver, lines):
        tmp = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False,
                                          encoding='utf-8', newline='')
        try:
            tmp.writelines(lines)
            tmp.close()
            old = (aue.DATA_FILE, aue.webdriver.Chrome, aue.login,
                   aue.handle_cookie_popup, aue.SUBMIT_TIMEOUT)
            aue.DATA_FILE = tmp.name
            aue.webdriver.Chrome = lambda: driver
            aue.login = lambda d: None
            aue.handle_cookie_popup = lambda d: None
            aue.SUBMIT_TIMEOUT = 1
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    aue.main()
            finally:
                (aue.DATA_FILE, aue.webdriver.Chrome, aue.login,
                 aue.handle_cookie_popup, aue.SUBMIT_TIMEOUT) = old
            with open(tmp.name, encoding='utf-8') as f:
                remaining = f.readlines()
            return out.getvalue(), remaining
        finally:
            os.unlink(tmp.name)

    def test_only_successful_lines_are_removed(self):
        driver = FakeDriver()

        def ok_submit(d):
            d.current_url = episode_url('1').replace('/edit', '')
        driver.add_page(episode_url('1'), make_edit_page(driver, ok_submit))
        errors = {'span.error': [FakeElement(text='Overview is required')]}
        driver.add_page(episode_url('2'),
                        make_edit_page(driver, lambda d: None, errors=errors))
        # Episode 3 never leaves the edit page and shows nothing -> timeout.
        driver.add_page(episode_url('3'), make_edit_page(driver, lambda d: None))

        lines = [
            '1;2025/1/20;45;Good Episode;Overview one\n',
            '2;1/21/2025;45;Rejected Episode;Overview two\n',
            '3;2025-1-22;45;Stuck Episode;Overview three\n',
        ]
        output, remaining = self.run_main(driver, lines)

        self.assertEqual(remaining, [lines[1], lines[2]])
        self.assertIn('Successfully updated episode 1', output)
        self.assertIn('Failed to update episode 2', output)
        self.assertIn('Overview is required', output)
        self.assertIn('Failed to update episode 3', output)
        self.assertIn('timed out', output)
        self.assertIn('Total updated: 1', output)
        self.assertIn('Total failed: 2', output)
        self.assertTrue(driver.quit_called)

    def test_unparseable_line_is_kept_and_counted_as_failure(self):
        driver = FakeDriver()

        def ok_submit(d):
            d.current_url = episode_url('1').replace('/edit', '')
        driver.add_page(episode_url('1'), make_edit_page(driver, ok_submit))

        lines = ['1;2025/1/20;45;Good;Overview\n', 'broken line without fields\n']
        output, remaining = self.run_main(driver, lines)

        self.assertEqual(remaining, [lines[1]])
        self.assertIn('Total updated: 1', output)
        self.assertIn('Total failed: 1', output)


if __name__ == '__main__':
    unittest.main(verbosity=2)
