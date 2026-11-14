import asyncio
import json
import os
import time

import aiohttp

import coms.coms
import coms.content_packer
import client_tools.get_from_github


# Интервал между опросами готовности результата (как описано в README).
POLL_INTERVAL_SEC = 10.0

# Общий лимит ожидания результата по poll_token.
# Должен с запасом перекрывать случаи "дольше минуты", когда одиночное
# соединение обрывается. По умолчанию - 10 минут.
POLL_TIMEOUT_SEC = 10 * 60.0

# Таймаут одного HTTP-запроса. При опросе он дополнительно ограничивается
# оставшимся общим временем ожидания.
TIMEOUT_FOR_AIOHTTP = 60 * 5

POLL_TOKEN_FIELD = 'poll_token'

# Поля, которыми сервис сообщает об ошибке в JSON-ответе.
ERROR_FIELDS = ('ошибка', 'error')

# Переопределение настроек через переменные окружения (удобно для утилит
# командной строки и автотестов, без правки кода).
ENV_POLL_INTERVAL = ('OZON_POLL_INTERVAL', 'OZON_POLL_INTERVAL_SEC')
ENV_POLL_TIMEOUT = ('OZON_POLL_TIMEOUT', 'OZON_POLL_TIMEOUT_SEC', 'OZON_ASYNC_TIMEOUT')
ENV_SERVICE_URL = 'OZON_SERVICE_URL'


class ApiError(Exception):
    """Ошибка обращения к сервису, с понятным пользователю описанием."""


class PollTimeoutError(ApiError):
    """За отведённое время сервер так и не вернул результат."""


class ServiceResponseError(ApiError):
    """Сервер ответил HTTP-ошибкой или JSON-ом с описанием ошибки."""


class ServiceConnectionError(ApiError):
    """Не удалось установить соединение с сервером."""


class RequestTimeoutError(ApiError):
    """Один HTTP-запрос не уложился в отведённое ему время."""


# Псевдоним на случай использования более длинного имени.
PollingTimeoutError = PollTimeoutError


def _env_float(names, default):
    for name in names:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            continue
        try:
            return float(raw)
        except ValueError:
            coms.coms.logmsg(f'Некорректное значение переменной окружения {name}: {raw!r}')
    return default


def _resolve_poll_interval(poll_interval):
    if poll_interval is None:
        return _env_float(ENV_POLL_INTERVAL, POLL_INTERVAL_SEC)
    return float(poll_interval)


def _resolve_poll_timeout(poll_timeout):
    if poll_timeout is None:
        return _env_float(ENV_POLL_TIMEOUT, POLL_TIMEOUT_SEC)
    return float(poll_timeout)


def _prepare_request(request, use_async=True):
    """Превращает запрос в текст JSON и добавляет asynchronously, если нужно."""

    if isinstance(request, dict):
        data = dict(request)
        if use_async and 'asynchronously' not in data:
            data['asynchronously'] = True
        return json.dumps(data, indent=4, ensure_ascii=False, sort_keys=False)

    if isinstance(request, str):
        if use_async:
            try:
                data = json.loads(request)
            except ValueError:
                data = None
            if isinstance(data, dict) and 'asynchronously' not in data:
                data['asynchronously'] = True
                return json.dumps(data, indent=4, ensure_ascii=False, sort_keys=False)
        return request

    raise ApiError(f'Неподдерживаемый тип запроса: {type(request).__name__}')


def _decode_answer(body):
    """Распаковывает ответ, а при неудаче пробует прочитать его как текст."""

    if not body:
        raise ApiError('Сервер вернул пустой ответ.')

    try:
        _, answer = coms.content_packer.decompress_message(body)
        return answer
    except Exception:
        pass

    text = body.decode('utf-8-sig', errors='ignore')
    if not text.strip():
        raise ApiError('Не удалось прочитать ответ сервера: пустой или неизвестный формат.')
    return text


def extract_poll_token(answer):
    """Возвращает poll_token из ответа или None, если это уже готовые данные.

    Ответ-объект с полем ошибки считается ошибкой сервиса, а не результатом.
    """

    if not isinstance(answer, str):
        return None

    text = answer.lstrip('﻿').strip()
    if not text.startswith('{'):
        return None

    try:
        data = json.loads(text)
    except ValueError:
        return None

    if not isinstance(data, dict):
        return None

    if POLL_TOKEN_FIELD not in data:
        for field in ERROR_FIELDS:
            if field in data:
                raise ServiceResponseError(
                    f'Сервер вернул ошибку: {data.get(field)}')
        return None

    token = data.get(POLL_TOKEN_FIELD)
    if token is None or token is False or str(token).strip() == '':
        raise ServiceResponseError(
            'Сервер вернул пустое значение poll_token, результат не получен.')

    return str(token)


async def _resolve_service_url(service_url=None):
    """Определяет адрес сервиса ровно один раз на весь запрос."""

    if service_url:
        return str(service_url).strip()

    env_url = os.environ.get(ENV_SERVICE_URL)
    if env_url and env_url.strip():
        return env_url.strip()

    url = await client_tools.get_from_github.get_from_github()

    if not url or not str(url).strip():
        raise ApiError('Не удалось получить адрес сервиса: пустой ответ GitHub.')

    return str(url).strip()


def _make_session(packed_request):
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(packed_request))
    }
    return aiohttp.ClientSession(headers=headers)


async def _post_once(session, url, packed_request, timeout_total):
    """Один POST-запрос с понятными ошибками по таймауту/соединению/статусу."""

    timeout_total = max(float(timeout_total), 0.001)

    try:
        async with session.post(
                url,
                data=packed_request,
                timeout=aiohttp.ClientTimeout(total=timeout_total)
                ) as response:

            if response.status != 200:

                error_body = await response.text()

                short_error_body = error_body[:500] if error_body else "Пустой ответ"

                raise ServiceResponseError(
                    f'Сервер вернул ошибку HTTP {response.status}: {short_error_body}')

            return await response.read()

    except ServiceResponseError:
        raise

    except asyncio.TimeoutError as e:
        raise RequestTimeoutError(
            f'Превышено время ожидания ответа сервера ({timeout_total:.1f} с): {url}') from e

    except aiohttp.ClientConnectionError as e:
        raise ServiceConnectionError(
            f'Ошибка подключения к серверу {url}: {e}') from e

    except aiohttp.ClientError as e:
        raise ServiceConnectionError(
            f'Ошибка сетевого запроса к {url}: {e}') from e

    except OSError as e:
        raise ServiceConnectionError(
            f'Ошибка подключения к серверу {url}: {e}') from e


def _poll_timeout_message(timeout, polls, interval):
    return (
        f'Превышено общее время ожидания результата ({timeout:.1f} с, '
        f'опросов: {polls}, интервал: {interval:.1f} с). '
        f'Сервер так и не вернул готовые данные: полученный poll_token '
        f'не является результатом, поэтому результат не сохранён.')


async def request_with_polling(
        request,
        poll_interval=None,
        poll_timeout=None,
        service_url=None,
        use_async=True,
        request_timeout=None,
        on_progress=None):
    """Отправляет запрос и, если сервер вернул poll_token, опрашивает его.

    Адрес сервиса определяется один раз и затем переиспользуется для всех
    опросов. Возвращает текст готового ответа. При любой ошибке возбуждает
    ApiError (или его наследника) с понятной причиной.

    on_progress - необязательный колбэк (polls, elapsed_sec), вызывается
    после каждого опроса, чтобы GUI мог показывать ход ожидания.
    """

    interval = _resolve_poll_interval(poll_interval)
    total_timeout = _resolve_poll_timeout(poll_timeout)

    if interval < 0:
        raise ApiError(f'Некорректный интервал опроса: {interval}')

    if total_timeout < 0:
        raise ApiError(f'Некорректный лимит ожидания: {total_timeout}')

    url = await _resolve_service_url(service_url)

    # ВАЖНО: адрес уже получен и больше не перезапрашивается с GitHub.
    coms.coms.logmsg(f'подключение к {url}')

    packed_request = coms.content_packer.pack_message(
        _prepare_request(request, use_async), 'gzip')

    one_request_timeout = request_timeout or TIMEOUT_FOR_AIOHTTP

    time_start = time.perf_counter()

    # Общий лимит отсчитывается с момента отправки первого запроса.
    deadline = time.monotonic() + total_timeout

    # Первый запрос тоже не должен выходить за общий лимит ожидания.
    first_request_timeout = one_request_timeout
    if total_timeout > 0:
        first_request_timeout = min(one_request_timeout, total_timeout)

    session = _make_session(packed_request)

    try:

        try:
            answer = _decode_answer(await _post_once(
                session, url, packed_request, first_request_timeout))
        except RequestTimeoutError as e:
            raise ApiError(
                f'Превышено время ожидания ответа сервера на первый запрос '
                f'({first_request_timeout:.1f} с).') from e

        token = extract_poll_token(answer)

        if token is None:
            coms.coms.logmsg(
                f'ответ получен, размер: {len(answer)} символов, '
                f'время: {time.perf_counter() - time_start:.3f}')
            return answer

        coms.coms.logmsg(
            f'сервер вернул poll_token, ожидаем результат: '
            f'интервал {interval:.1f} с, лимит {total_timeout:.1f} с')

        polls = 0

        while True:

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PollTimeoutError(
                    _poll_timeout_message(total_timeout, polls, interval))

            await asyncio.sleep(min(interval, remaining))

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PollTimeoutError(
                    _poll_timeout_message(total_timeout, polls, interval))

            polls += 1

            poll_timeout_left = min(one_request_timeout, remaining)

            try:
                answer = _decode_answer(await _post_once(
                    session, url, packed_request, poll_timeout_left))
            except RequestTimeoutError as e:
                # Исчерпали общий лимит прямо во время запроса - это таймаут
                # ожидания, а не отдельная сетевая ошибка.
                raise PollTimeoutError(
                    _poll_timeout_message(total_timeout, polls, interval)) from e

            token = extract_poll_token(answer)

            coms.coms.logmsg(
                f'опрос {polls}: '
                f'{"результат готов" if token is None else "ещё не готов"}, '
                f'прошло {time.perf_counter() - time_start:.1f} с')

            if on_progress is not None:
                try:
                    on_progress(polls, time.perf_counter() - time_start)
                except Exception as e:
                    coms.coms.logmsg(f'ошибка в обработчике прогресса: {e}')

            if token is None:
                coms.coms.logmsg(
                    f'ответ получен, размер: {len(answer)} символов, '
                    f'опросов: {polls}, время: {time.perf_counter() - time_start:.3f}')
                return answer

    finally:
        await session.close()


async def make_request_checked(
        request,
        poll_interval=None,
        poll_timeout=None,
        service_url=None,
        use_async=True,
        request_timeout=None,
        on_progress=None):
    """Как make_request, но ошибки возбуждаются, а не возвращаются текстом.

    Нужен клиентам (CLI/GUI), которым важно отличать успех от ошибки.
    """

    try:
        return await request_with_polling(
            request,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            service_url=service_url,
            use_async=use_async,
            request_timeout=request_timeout,
            on_progress=on_progress)

    except ApiError:
        raise

    except Exception as e:
        coms.coms.logmsg(coms.coms.format_error(e))
        raise ApiError(f'Произошла ошибка: {e}') from e


async def __start_messaging(url, request):

    coms.coms.logmsg(f'подключение к {url}')

    time_start = time.perf_counter()

    req_unpacked = len(request)

    packed_request = coms.content_packer.pack_message(request, 'gzip')

    req_packed = len(packed_request)

    session = _make_session(packed_request)

    try:
        answer = _decode_answer(await _post_once(
            session, url, packed_request, TIMEOUT_FOR_AIOHTTP))

        coms.coms.logmsg(
            f'Размеры, запрос: {req_packed}/{req_unpacked}, '
            f'ответ: {len(answer)} символов, время: {time.perf_counter() - time_start:.3f}')

        return answer

    except ApiError:
        raise

    except Exception as e:
        coms.coms.logmsg(coms.coms.format_error(e))
        raise ApiError(f'Произошла ошибка: {e}') from e

    finally:
        await session.close()


async def make_request(
        request,
        poll_interval=None,
        poll_timeout=None,
        service_url=None,
        use_async=True,
        request_timeout=None,
        on_progress=None):
    """Совместимый со старым кодом вызов: при ошибке возвращает её текст."""

    try:
        return await request_with_polling(
            request,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
            service_url=service_url,
            use_async=use_async,
            request_timeout=request_timeout,
            on_progress=on_progress)

    except Exception as e:
        coms.coms.logmsg(coms.coms.format_error(e))
        return coms.coms.format_error(e)
