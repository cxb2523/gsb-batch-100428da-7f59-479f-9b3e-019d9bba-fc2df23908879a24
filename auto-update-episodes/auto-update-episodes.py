import os
import re
import time
import configparser
from datetime import date
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

SCRIPT_DIR = os.path.dirname(__file__)
config = configparser.ConfigParser()
config.read(os.path.join(SCRIPT_DIR, 'config.ini'), encoding='utf-8')

TMDB_USERNAME = config.get('TMDB', 'USERNAME')
TMDB_PASSWORD = config.get('TMDB', 'PASSWORD')
EPISODE_URL = config.get('SHOW', 'EPISODE_URL')
LANGUAGE_CODE = config.get('SHOW', 'LANGUAGE_CODE')
DATA_FILE = os.path.join(SCRIPT_DIR, 'episodes.txt')

SUBMIT_TIMEOUT = 20

# Selectors that may carry validation / error messages on the edit page.
ERROR_SELECTORS = [
    (By.CSS_SELECTOR, '.error_explanation'),
    (By.CSS_SELECTOR, '.alert-danger'),
    (By.CSS_SELECTOR, '.alert.error'),
    (By.CSS_SELECTOR, '.field_with_errors'),
    (By.CSS_SELECTOR, 'span.error'),
    (By.CSS_SELECTOR, '.error-message'),
]
# Selectors that indicate the update was accepted.
SUCCESS_SELECTORS = [
    (By.CSS_SELECTOR, '.alert-success'),
    (By.CSS_SELECTOR, '.alert.success'),
    (By.CSS_SELECTOR, '.flash.notice'),
]


def parse_air_date(raw):
    """Parse an air date in YYYY/M/D, YYYY-M-D or M/D/YYYY form.

    Returns a (year, month, day) tuple. Raises ValueError if the text is
    not a recognizable or valid calendar date.
    """
    text = raw.strip()
    m = re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
        if not m:
            raise ValueError(f'unrecognized date format: {raw!r}')
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    date(year, month, day)  # raises ValueError for invalid dates
    return year, month, day


def format_air_date(ymd, language_code):
    """Format a (year, month, day) tuple the way the site expects it.

    Chinese UI uses YYYY/M/D, English UI uses M/D/YYYY; anything else falls
    back to the YYYY-MM-DD form TMDB also accepts.
    """
    year, month, day = ymd
    lang = (language_code or '').lower()
    if lang.startswith('zh'):
        return f'{year}/{month}/{day}'
    if lang.startswith('en'):
        return f'{month}/{day}/{year}'
    return f'{year}-{month:02d}-{day:02d}'


def dates_equal(raw_a, raw_b):
    """Compare two date strings by calendar value when both parse."""
    try:
        return parse_air_date(raw_a) == parse_air_date(raw_b)
    except ValueError:
        return raw_a.strip() == raw_b.strip()


def login(driver):
    driver.get('https://www.themoviedb.org/login')
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, 'auth_username')))
    driver.find_element(By.ID, 'auth_username').send_keys(TMDB_USERNAME)
    driver.find_element(By.ID, 'auth_password').send_keys(TMDB_PASSWORD)
    driver.find_element(By.ID, 'auth_login_button').click()

    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.XPATH, '//span[@class="avatar"]/a/img[@class="avatar"]'))
    )


def handle_cookie_popup(driver):
    try:
        cookie_banner = WebDriverWait(driver, 2).until(
            EC.presence_of_element_located((By.ID, "onetrust-banner-sdk"))
        )
        if cookie_banner.is_displayed():
            driver.find_element(By.CLASS_NAME, "onetrust-close-btn-handler").click()
            time.sleep(1)
    except Exception:
        pass


def fill_and_verify(driver, language_code, topic, description, date_text, duration):
    """Fill the edit form and read every field back.

    Returns (True, '') when every field holds the intended value, otherwise
    (False, reason) and nothing is submitted.
    """
    fields = [
        (By.XPATH, f'//*[@id="{language_code}_name"]', topic, 'name'),
        (By.XPATH, f'//*[@id="{language_code}_overview"]', description, 'overview'),
        (By.ID, 'air_date', date_text, 'air date'),
        (By.ID, 'runtime', duration, 'runtime'),
    ]
    for by, locator, expected, label in fields:
        element = driver.find_element(by, locator)
        element.clear()
        element.send_keys(expected)
        actual = element.get_attribute('value') or ''
        if label == 'air date':
            matches = dates_equal(actual, expected)
        else:
            matches = actual == expected
        if not matches:
            return False, (f'{label} did not take effect '
                           f'(wrote {expected!r}, field shows {actual!r}); skipping submit')
    return True, ''


def find_visible_texts(driver, selectors):
    texts = []
    for by, selector in selectors:
        for element in driver.find_elements(by, selector):
            try:
                if element.is_displayed() and element.text.strip():
                    texts.append(element.text.strip())
            except Exception:
                continue
    return texts


def wait_for_submit_result(driver, edit_url, timeout=SUBMIT_TIMEOUT):
    """Wait until the edit page is left or a success flag appears.

    Returns (True, '') on success; (False, reason) when a validation error
    shows up, the page stays on the edit form, or the wait times out.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        errors = find_visible_texts(driver, ERROR_SELECTORS)
        if errors:
            return False, 'validation error: ' + '; '.join(errors)
        if find_visible_texts(driver, SUCCESS_SELECTORS):
            return True, ''
        if '/edit' not in driver.current_url:
            return True, ''
        time.sleep(0.5)
    return False, (f'timed out after {timeout}s waiting for the edit page to be left '
                   f'(still on {driver.current_url})')


def update_episode(driver, episode_number, topic, description, date_text, duration):
    """Update one episode. Returns (success, reason)."""
    try:
        target_date = parse_air_date(date_text)
    except ValueError as e:
        return False, f'invalid air date: {e}'
    formatted_date = format_air_date(target_date, LANGUAGE_CODE)

    episode_url = EPISODE_URL.replace('/episode/1/', f'/episode/{episode_number}/')
    try:
        driver.get(episode_url)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, f'//*[@id="{LANGUAGE_CODE}_name"]'))
        )
    except Exception as e:
        return False, f'edit form did not load: {e}'

    try:
        ok, reason = fill_and_verify(driver, LANGUAGE_CODE, topic, description,
                                     formatted_date, duration)
    except Exception as e:
        return False, f'could not fill the form: {e}'
    if not ok:
        return False, reason

    try:
        driver.find_element(By.ID, 'submit').click()
    except Exception as e:
        return False, f'could not click submit: {e}'

    try:
        return wait_for_submit_result(driver, episode_url, timeout=SUBMIT_TIMEOUT)
    except Exception as e:
        return False, f'could not confirm the submit result: {e}'


def main():
    if not os.path.exists(DATA_FILE):
        print("Data file not found.")
        return

    driver = webdriver.Chrome()
    try:
        login(driver)
        handle_cookie_popup(driver)

        with open(DATA_FILE, 'r', encoding='utf-8') as file:
            data = file.readlines()

        success_count = 0
        failure_count = 0
        remaining_data = data.copy()

        for line in data:
            if not line.strip():
                continue
            try:
                episode_number, date_text, duration, topic, description = \
                    line.strip().split(';', maxsplit=4)
            except ValueError:
                failure_count += 1
                print(f'Failed to update episode (unparseable line, kept for retry): {line.strip()!r}')
                continue

            success, reason = update_episode(driver, episode_number, topic, description,
                                             date_text, duration)
            if success:
                success_count += 1
                remaining_data.remove(line)
                with open(DATA_FILE, 'w', encoding='utf-8') as file:
                    file.writelines(remaining_data)
                print(f'Successfully updated episode {episode_number}')
            else:
                failure_count += 1
                print(f'Failed to update episode {episode_number}: {reason} (kept for retry)')

        print(f'\nTotal updated: {success_count}')
        print(f'Total failed: {failure_count}')
    finally:
        driver.quit()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f'Error: {e}')
