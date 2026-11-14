from unittest.mock import MagicMock, patch

import pytest
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from fastrpa.core.elements import DEFAULT_TIMEOUT, FormElement
from fastrpa.exceptions import FormException


FORM_XPATH = '//form'
SUCCESS_XPATH = '//div[@id="success_message"]'


class FakeClock:
    """Deterministic replacement for monotonic()/sleep() used by the wait."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make_webdriver(
    current_url: str = 'https://www.site.com/form',
    page_source: str = '<html><body>form page</body></html>',
) -> MagicMock:
    webdriver = MagicMock()
    webdriver.current_url = current_url
    webdriver.page_source = page_source
    webdriver.find_element.return_value = MagicMock()
    return webdriver


def raise_except_for_form(by, xpath):
    if xpath == FORM_XPATH:
        return MagicMock()
    raise NoSuchElementException()


def make_form(webdriver: MagicMock, timeout: float = 10) -> FormElement:
    return FormElement(FORM_XPATH, webdriver, timeout)


def find_element_calls_for(webdriver: MagicMock, xpath: str):
    return [
        call
        for call in webdriver.find_element.call_args_list
        if call.args == (By.XPATH, xpath)
    ]


def test_form_uses_default_timeout():
    form = FormElement(FORM_XPATH, make_webdriver())
    assert form.timeout == DEFAULT_TIMEOUT


def test_submit_without_success_condition_returns_immediately():
    webdriver = make_webdriver()
    form = make_form(webdriver)

    with patch('fastrpa.core.elements.sleep') as sleep_mock:
        form.submit()

    webdriver.find_element.assert_called_with(By.XPATH, FORM_XPATH)
    webdriver.find_element.return_value.submit.assert_called_once()
    sleep_mock.assert_not_called()


def test_submit_with_button_without_success_condition_returns_immediately():
    webdriver = make_webdriver()
    form = make_form(webdriver)

    with patch('fastrpa.core.elements.ActionChains'):
        form.submit('//button[@id="send"]')

    assert find_element_calls_for(webdriver, '//button[@id="send"]')


def test_submit_succeeds_when_redirect_url_matches():
    webdriver = make_webdriver(current_url='https://www.site.com/success')
    form = make_form(webdriver)
    form.set_success_condition(redirect_url='https://www.site.com/success')

    form.submit()


def test_submit_succeeds_when_element_is_found_on_first_check():
    webdriver = make_webdriver()
    form = make_form(webdriver)
    form.set_success_condition(elements_to_find=[SUCCESS_XPATH])

    form.submit()

    assert find_element_calls_for(webdriver, SUCCESS_XPATH)


def test_submit_retries_element_condition_until_it_appears():
    webdriver = make_webdriver()
    success_element = MagicMock()
    attempts = {'count': 0}

    def find_element(by, xpath):
        if xpath == SUCCESS_XPATH:
            attempts['count'] += 1
            if attempts['count'] < 3:
                raise NoSuchElementException()
            return success_element
        return MagicMock()

    webdriver.find_element.side_effect = find_element
    form = make_form(webdriver)
    form.set_success_condition(elements_to_find=[SUCCESS_XPATH])

    with patch('fastrpa.core.elements.sleep') as sleep_mock:
        form.submit()

    assert attempts['count'] == 3
    assert sleep_mock.call_count == 2


def test_submit_retries_text_condition_against_page_source():
    webdriver = make_webdriver(page_source='<html>loading...</html>')
    form = make_form(webdriver)
    form.set_success_condition(text_to_find=['Success!'])

    def fake_sleep(seconds):
        webdriver.page_source = '<html>Success!</html>'

    with patch('fastrpa.core.elements.sleep', side_effect=fake_sleep):
        form.submit()

    assert 'Success!' in webdriver.page_source


def test_submit_succeeds_with_all_conditions_satisfied():
    webdriver = make_webdriver(
        current_url='https://www.site.com/success',
        page_source='<html>Success!</html>',
    )
    form = make_form(webdriver)
    form.set_success_condition(
        redirect_url='https://www.site.com/success',
        elements_to_find=[SUCCESS_XPATH],
        text_to_find=['Success!'],
    )

    form.submit()


@pytest.mark.parametrize(
    (
        'kwargs',
        'expected_condition',
        'expected_value',
    ),
    [
        (
            {'redirect_url': 'https://www.site.com/success'},
            'redirect_url',
            'https://www.site.com/success',
        ),
        (
            {'elements_to_find': [SUCCESS_XPATH]},
            'elements_to_find',
            SUCCESS_XPATH,
        ),
        (
            {'text_to_find': ['Success!']},
            'text_to_find',
            'Success!',
        ),
    ],
)
def test_submit_raises_form_exception_when_condition_times_out(
    kwargs, expected_condition, expected_value
):
    webdriver = make_webdriver()
    webdriver.find_element.side_effect = raise_except_for_form
    clock = FakeClock()
    form = make_form(webdriver, timeout=10)
    form.set_success_condition(**kwargs)

    with (
        patch('fastrpa.core.elements.monotonic', side_effect=clock.time),
        patch('fastrpa.core.elements.sleep', side_effect=clock.sleep),
        pytest.raises(FormException) as exc_info,
    ):
        form.submit()

    assert exc_info.value.condition == expected_condition
    assert exc_info.value.expected_value == expected_value


def test_element_condition_is_retried_throughout_the_timeout_window():
    webdriver = make_webdriver()
    webdriver.find_element.side_effect = raise_except_for_form
    clock = FakeClock()
    form = make_form(webdriver, timeout=10)
    form.set_success_condition(elements_to_find=[SUCCESS_XPATH])

    with (
        patch('fastrpa.core.elements.monotonic', side_effect=clock.time),
        patch('fastrpa.core.elements.sleep', side_effect=clock.sleep),
        pytest.raises(FormException) as exc_info,
    ):
        form.submit()

    # 21 checks across the 10s window with a 0.5s poll interval:
    # the condition was retried instead of aborting on the first failure.
    assert len(find_element_calls_for(webdriver, SUCCESS_XPATH)) == 21
    assert exc_info.value.condition == 'elements_to_find'
    assert exc_info.value.expected_value == SUCCESS_XPATH


def test_success_conditions_are_isolated_between_form_instances():
    webdriver = make_webdriver(
        current_url='https://www.site.com/form',
        page_source='<html>Done</html>',
    )
    clock = FakeClock()

    first_form = make_form(webdriver, timeout=1)
    first_form.set_success_condition(
        redirect_url='https://www.site.com/success'
    )

    second_form = make_form(webdriver, timeout=1)
    second_form.set_success_condition(text_to_find=['Done'])

    # Conditions live on the instances, never on the shared class.
    assert not hasattr(FormElement, '_success_redirect_url')
    assert not hasattr(FormElement, '_success_elements_to_find')
    assert not hasattr(FormElement, '_success_text_to_find')
    assert first_form._success_redirect_url == ('https://www.site.com/success')
    assert first_form._success_text_to_find == []
    assert second_form._success_redirect_url is None
    assert second_form._success_text_to_find == ['Done']

    # The second form only evaluates its own text condition and succeeds.
    with (
        patch('fastrpa.core.elements.monotonic', side_effect=clock.time),
        patch('fastrpa.core.elements.sleep', side_effect=clock.sleep),
    ):
        second_form.submit()

    # The first form still only knows its own redirect condition and fails.
    clock.now = 0.0
    with (
        patch('fastrpa.core.elements.monotonic', side_effect=clock.time),
        patch('fastrpa.core.elements.sleep', side_effect=clock.sleep),
        pytest.raises(FormException) as exc_info,
    ):
        first_form.submit()

    assert exc_info.value.condition == 'redirect_url'
    assert exc_info.value.expected_value == ('https://www.site.com/success')


def test_set_success_condition_copies_caller_lists():
    webdriver = make_webdriver()
    form = make_form(webdriver)
    elements = [SUCCESS_XPATH]
    texts = ['Success!']

    form.set_success_condition(
        elements_to_find=elements,
        text_to_find=texts,
    )
    elements.append('//other')
    texts.append('Other')

    assert form._success_elements_to_find == [SUCCESS_XPATH]
    assert form._success_text_to_find == ['Success!']
