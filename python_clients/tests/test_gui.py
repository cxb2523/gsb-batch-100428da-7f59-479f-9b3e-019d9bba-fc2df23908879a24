"""Тесты GUI-части: прогресс/блокировка формы и различение успеха и ошибки.

Тесты используют offscreen-режим Qt и пропускаются, если PySide6/qasync
не установлены (например, в минимальном окружении только для CLI).
"""

import asyncio
import json
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6')
pytest.importorskip('qasync')

from PySide6.QtWidgets import QApplication  # noqa: E402

import client_gui.main as gui  # noqa: E402
from tests.fake_service import FakeOzonService  # noqa: E402


@pytest.fixture(scope='module')
def qt_app():
    app = QApplication.instance() or QApplication(['test'])
    return app


def _make_window(qt_app, service_url, poll_timeout=''):
    os.environ['OZON_SERVICE_URL'] = service_url

    window = gui.MyWindow()
    window.e_poll_timeout.setText(str(poll_timeout))
    return window


def test_success_keeps_form_enabled_and_shows_result_tab(qt_app):

    async def scenario():
        srv = FakeOzonService(token_responses=2)
        await srv.start()
        old_interval = gui.client_tools.api.POLL_INTERVAL_SEC
        gui.client_tools.api.POLL_INTERVAL_SEC = 0.2
        try:
            window = _make_window(qt_app, srv.url, poll_timeout=5)
            window.show()

            task = asyncio.ensure_future(window.on_ok_clicked())

            # Пока идёт ожидание: форма заблокирована, прогресс виден.
            await asyncio.sleep(0.05)
            assert task.done() is False
            assert window.form_group.isEnabled() is False
            assert window.button_ok.isEnabled() is False
            assert window.progress_bar.isVisible() is True
            assert window.progress_bar.isTextVisible() is True

            await task

            assert window.form_group.isEnabled() is True
            assert window.button_ok.isEnabled() is True
            assert window.progress_bar.isVisible() is False

            captions = [window.tab_widget.tabText(i)
                        for i in range(window.tab_widget.count())]
            assert any(c.startswith('Результат') for c in captions)
        finally:
            gui.client_tools.api.POLL_INTERVAL_SEC = old_interval
            await srv.stop()

    asyncio.run(scenario())


def test_timeout_is_shown_as_separate_state(qt_app):

    async def scenario():
        srv = FakeOzonService(always_token=True, token='token-never-ready')
        await srv.start()
        try:
            window = _make_window(qt_app, srv.url, poll_timeout=0.2)

            await window.on_ok_clicked()

            assert window.form_group.isEnabled() is True
            assert window.progress_bar.isVisible() is False

            captions = [window.tab_widget.tabText(i)
                        for i in range(window.tab_widget.count())]
            assert any(c.startswith('Таймаут') for c in captions)
        finally:
            await srv.stop()

    asyncio.run(scenario())


def test_server_error_is_shown_as_error_state(qt_app):

    async def scenario():
        srv = FakeOzonService(token_responses=0)
        srv.final_data = json.dumps({'ошибка': 'неверный параметр'},
                                   ensure_ascii=False)
        await srv.start()
        try:
            window = _make_window(qt_app, srv.url, poll_timeout=5)

            await window.on_ok_clicked()

            captions = [window.tab_widget.tabText(i)
                        for i in range(window.tab_widget.count())]
            assert any(c.startswith('Ошибка') for c in captions)
            assert window.form_group.isEnabled() is True
        finally:
            await srv.stop()

    asyncio.run(scenario())


def test_empty_poll_timeout_field_uses_default(qt_app):
    window = gui.MyWindow()
    window.e_poll_timeout.setText('')
    assert window.poll_timeout_value() is None

    window.e_poll_timeout.setText('120')
    assert window.poll_timeout_value() == 120.0
