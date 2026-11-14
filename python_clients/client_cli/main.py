
import sys
import json

import client_cli.params
import coms.coms

import client_tools.api

import asyncio


def final_error(dest_file, msg):

    coms.coms.logmsg(msg)

    s = json.dumps( {"ошибка": msg}, indent=4, ensure_ascii=False, sort_keys=False)

    with open(dest_file, 'w', encoding='utf-8') as f:
        f.write(s)

    sys.exit(1)


def request_from_params(params):
    """Копия параметров без клиентских настроек, которые серверу не нужны."""

    request = dict(params)

    for k in ('dest_filename', 'poll_timeout', 'poll_interval',
              'poll_timeout_sec', 'poll_interval_sec'):
        request.pop(k, None)

    return request


async def async_main() -> None:

    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')

    params = client_cli.params.read_params_from_file()

    dest_file = params.get('dest_filename') if params else None

    if not params:
        final_error(dest_file or coms.coms.get_filename_for_store('result.json'),
                    'входные параметры не определены')
        sys.exit(1)
        return

    try:
        answer = await client_tools.api.make_request_checked(
            request_from_params(params),
            poll_timeout=params.get('poll_timeout'),
            poll_interval=params.get('poll_interval'))
    except Exception as e:
        final_error(dest_file, coms.coms.format_error(e))
        sys.exit(1)
        return

    with open(dest_file, 'w', encoding='utf-8') as f:
        f.write(answer)


def main():

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(async_main())

