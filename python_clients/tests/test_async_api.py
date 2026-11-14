import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

import client_tools.api as api
import client_tools.get_from_github as get_from_github

from tests.fake_service import FakeOzonService, get_free_port


CLIENTS_DIR = Path(__file__).resolve().parents[1]


def run(coro):
    """Запускает корутину в собственном цикле событий (без pytest-asyncio)."""

    return asyncio.run(coro)


def with_service(func):
    """Поднимает локальный фейковый сервис на время теста."""

    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        async def main():
            srv = FakeOzonService()
            await srv.start()
            try:
                return await func(srv, *args, **kwargs)
            finally:
                await srv.stop()

        return run(main())

    return wrapper


def test_returns_data_without_polling_when_server_answers_immediately():

    @with_service
    async def scenario(service):
        service.token_responses = 0

        answer = await api.request_with_polling(
            {'search_string': 'самолет'}, service_url=service.url)

        assert json.loads(answer)[0]['sku'] == 1
        assert len(service.requests) == 1
        # Клиент обязан запросить асинхронный режим у сервера.
        assert json.loads(service.request_bodies[0])['asynchronously'] is True

    scenario()


def test_polls_until_data_and_resolves_url_once(monkeypatch):

    @with_service
    async def scenario(service):
        service.token_responses = 3
        calls = []

        async def fake_get_from_github():
            calls.append(1)
            return service.url

        monkeypatch.setattr(get_from_github, 'get_from_github', fake_get_from_github)

        answer = await api.request_with_polling(
            {'search_string': 'самолет'},
            poll_interval=0.01,
            poll_timeout=2.0)

        assert json.loads(answer)[0]['sku'] == 1
        # Первый запрос + 3 опроса; на четвёртом сервер вернул данные.
        assert len(service.requests) == 4
        # Адрес сервиса получен ровно один раз и переиспользован при опросах.
        assert len(calls) == 1

    scenario()


def test_github_url_is_not_refetched_during_polling(monkeypatch):

    @with_service
    async def scenario(service):
        service.token_responses = 2
        calls = []

        async def fake_get_from_github():
            calls.append(1)
            return service.url

        monkeypatch.setattr(get_from_github, 'get_from_github', fake_get_from_github)

        await api.request_with_polling(
            {'search_string': 'самолет'}, poll_interval=0.01, poll_timeout=2.0)

        assert len(calls) == 1

    scenario()


def test_default_poll_timeout_covers_more_than_a_minute():
    # README: одиночное соединение может оборваться уже "больше минуты".
    assert api.POLL_TIMEOUT_SEC > 60
    assert api.POLL_INTERVAL_SEC > 0


def test_poll_settings_can_be_overridden_by_env(monkeypatch):

    @with_service
    async def scenario(service):
        service.token_responses = 1
        monkeypatch.setenv('OZON_POLL_INTERVAL', '0.01')
        monkeypatch.setenv('OZON_POLL_TIMEOUT', '2.0')

        answer = await api.request_with_polling(
            {'search_string': 'самолет'}, service_url=service.url)

        assert json.loads(answer)[0]['sku'] == 1
        assert len(service.requests) == 2

    scenario()


def test_timeout_reports_reason_and_never_returns_token():

    @with_service
    async def scenario(service):
        service.always_token = True
        service.token = 'token-never-ready'

        with pytest.raises(api.PollTimeoutError) as exc_info:
            await api.request_with_polling(
                {'search_string': 'самолет'},
                service_url=service.url,
                poll_interval=0.01,
                poll_timeout=0.05)

        msg = str(exc_info.value)
        # Понятная причина, без подмены результата полученным токеном.
        assert 'ожидания' in msg
        assert 'token-never-ready' not in msg

    scenario()


def test_error_json_from_service_is_reported_not_treated_as_data():

    @with_service
    async def scenario(service):
        service.token_responses = 0
        service.final_data = json.dumps(
            {'ошибка': 'неверный параметр'}, ensure_ascii=False)

        with pytest.raises(api.ServiceResponseError) as exc_info:
            await api.request_with_polling(
                {'search_string': 'самолет'}, service_url=service.url)

        assert 'неверный параметр' in str(exc_info.value)

    scenario()


def test_connection_error_has_clear_reason():
    # Несуществующий хост: ошибка разрешения имени - это ошибка подключения.
    with pytest.raises((api.ServiceConnectionError, api.RequestTimeoutError)) as exc_info:
        run(api.request_with_polling(
            {'search_string': 'самолет'},
            service_url='http://ozon-service.invalid:8080/',
            poll_interval=0.01,
            poll_timeout=1.0))

    msg = str(exc_info.value)
    assert 'подключ' in msg or 'ожидания' in msg
    assert 'poll_token' not in msg


def test_closed_port_has_clear_reason_and_no_token():
    port = get_free_port()

    with pytest.raises(api.ApiError) as exc_info:
        run(api.request_with_polling(
            {'search_string': 'самолет'},
            service_url=f'http://127.0.0.1:{port}/',
            poll_interval=0.01,
            poll_timeout=1.0))

    msg = str(exc_info.value)
    assert 'ожидания' in msg or 'подключ' in msg
    assert 'token' not in msg.lower()


def test_http_error_has_status_and_body():

    async def scenario():
        srv = FakeOzonService(status=500, error_body='boom')
        await srv.start()
        try:
            with pytest.raises(api.ServiceResponseError) as exc_info:
                await api.request_with_polling(
                    {'search_string': 'самолет'}, service_url=srv.url)
        finally:
            await srv.stop()

        msg = str(exc_info.value)
        assert '500' in msg
        assert 'boom' in msg

    run(scenario())


def test_make_request_compat_returns_error_text_not_token():

    @with_service
    async def scenario(service):
        service.always_token = True
        service.token = 'token-never-ready'

        result = await api.make_request(
            {'search_string': 'самолет'},
            service_url=service.url,
            poll_interval=0.01,
            poll_timeout=0.05)

        assert 'token-never-ready' not in result
        assert '===' in result

    scenario()


#*******************************************************************
# Сквозные проверки CLI: пишет либо бизнес-данные, либо JSON с ошибкой.


def _write_params(path, dest, poll_timeout):
    path.write_text(json.dumps({
        'dest_filename': str(dest),
        'search_string': 'самолет',
        'expected_count_items': 1,
        'poll_timeout': poll_timeout,
        'poll_interval': 0.01,
    }, ensure_ascii=False), encoding='utf-8')


def _run_cli(params_path, service_url):
    """Запускает CLI в отдельном процессе, не блокируя фейковый сервер."""

    env = dict(os.environ)
    env['OZON_SERVICE_URL'] = service_url
    env['PYTHONIOENCODING'] = 'utf-8'

    async def run():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(CLIENTS_DIR / 'scrape_ozon_cli.py'), str(params_path),
            cwd=str(CLIENTS_DIR),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        return (proc.returncode,
                stdout.decode('utf-8', 'ignore'),
                stderr.decode('utf-8', 'ignore'))

    return run()


def test_cli_writes_business_data_on_success():

    @with_service
    async def scenario(service):
        service.token_responses = 2

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'result.json'
            params = Path(tmp) / 'params.json'
            _write_params(params, dest, poll_timeout=10)

            code, out, err = await _run_cli(params, service.url)

            assert code == 0, (out, err)
            content = dest.read_text(encoding='utf-8')
            data = json.loads(content)
            assert data[0]['sku'] == 1
            assert 'poll_token' not in content

    scenario()


def test_cli_writes_error_json_on_timeout():

    @with_service
    async def scenario(service):
        service.always_token = True
        service.token = 'token-never-ready'

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'result.json'
            params = Path(tmp) / 'params.json'
            _write_params(params, dest, poll_timeout=0.2)

            code, out, err = await _run_cli(params, service.url)

            assert code != 0, (out, err)
            content = dest.read_text(encoding='utf-8')
            data = json.loads(content)

            assert 'ошибка' in data
            assert 'ожидания' in data['ошибка']
            assert 'token-never-ready' not in content

    scenario()


def test_cli_writes_error_json_on_server_error():

    @with_service
    async def scenario(service):
        service.token_responses = 0
        service.final_data = json.dumps(
            {'ошибка': 'неверный параметр'}, ensure_ascii=False)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'result.json'
            params = Path(tmp) / 'params.json'
            _write_params(params, dest, poll_timeout=10)

            code, out, err = await _run_cli(params, service.url)

            assert code != 0, (out, err)
            data = json.loads(dest.read_text(encoding='utf-8'))

            assert 'ошибка' in data
            assert 'неверный параметр' in data['ошибка']

    scenario()


def test_cli_does_not_send_poll_settings_to_service():
    """Клиентские настройки не должны уходить на сервер."""

    @with_service
    async def scenario(service):
        service.token_responses = 1

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'result.json'
            params = Path(tmp) / 'params.json'
            _write_params(params, dest, poll_timeout=10)

            code, out, err = await _run_cli(params, service.url)
            assert code == 0, (out, err)

        assert service.request_bodies
        for body in service.request_bodies:
            sent = json.loads(body)
            assert 'poll_timeout' not in sent
            assert 'poll_interval' not in sent
            assert 'dest_filename' not in sent
            assert sent['asynchronously'] is True
