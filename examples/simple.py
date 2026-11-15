import asyncio
import logging

from bolsa import B3AsyncBackend

logging.basicConfig(
    format=(
        '%(asctime)s.%(msecs)03d %(levelname)s {%(module)s} [%(funcName)s] '
        '%(message)s'
    ),
    datefmt='%Y-%m-%d,%H:%M:%S',
    level=logging.DEBUG
)


async def main():
    from datetime import datetime
    start_datetime = datetime.now()
    logging.info(f'Starting... {start_datetime}')
    b3_httpclient = B3AsyncBackend(
        username='SEU CPF/CNPJ',
        password='SUA SENHA',
        captcha_service=None  # captcha_service is not required yet
    )
    brokers = await b3_httpclient.get_brokers_with_accounts()
    assets_extract = (
        await b3_httpclient.get_brokers_account_portfolio_assets_extract(
            brokers=brokers
        )
    )

    print(
        f'Assets extract collected for {len(assets_extract)} '
        f'broker/account pair(s):'
    )
    for result in assets_extract:
        if result.error:
            print(
                f'  broker {result.broker_value} / account '
                f'{result.account_id}: ERROR - {result.error}'
            )
        else:
            print(
                f'  broker {result.broker_value} / account '
                f'{result.account_id}: {len(result.assets)} asset(s)'
            )
            for asset in result.assets:
                print(f'    - {asset}')

    await b3_httpclient.session_close()
    await b3_httpclient.connection_close()

    logging.info(f'Finish script... {datetime.now() - start_datetime}')

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
