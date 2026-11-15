import asyncio
import logging
from functools import cached_property

import aiohttp

from bolsa.connector import B3HttpClientConnector
from bolsa.http_client import B3HttpClient
from bolsa.models import BrokerAccountAssetsExtractResult
from bolsa.responses import (
    GetBrokerAccountAssetExtractResponse,
    GetBrokerAccountResponse,
    GetBrokersResponse
)

logger = logging.getLogger(__name__)

POOL_CONNECTOR = B3HttpClientConnector()


class B3AsyncBackend():

    def __init__(self, username, password, captcha_service):
        self._connector = None
        self.username = username
        self.password = password
        self.captcha_service = captcha_service

    @cached_property
    def _session(self):
        logger.info(f'Creating session for username: {self.username}')
        self._connector = POOL_CONNECTOR.get_connector()
        return aiohttp.ClientSession(
            connector=self._connector,
            connector_owner=False
        )

    @cached_property
    def _http_client(self):
        return B3HttpClient(
            username=self.username,
            password=self.password,
            session=self._session,
            captcha_service=self.captcha_service
        )

    async def session_close(self):
        await self._session.close()

    async def connection_close(self):
        if self._connector is not None:
            await self._connector.close()

    async def get_brokers(self):
        response = await self._http_client.get_brokers()
        response_class = GetBrokersResponse(response)

        return await response_class.data()

    async def get_broker_accounts(self, broker):
        response = await self._http_client.get_broker_accounts(broker)
        response_class = GetBrokerAccountResponse(
            response=response,
            broker=broker
        )

        return await response_class.data()

    async def get_brokers_with_accounts(self):
        brokers = await self.get_brokers()
        brokers_account_routine = [
            asyncio.create_task(
                self.get_broker_accounts(broker)
            )
            for broker in brokers
        ]

        return await asyncio.gather(*brokers_account_routine)

    async def get_broker_account_portfolio_assets_extract(
        self,
        account_id,
        broker_value,
        broker_parse_extra_data,
        account_parse_extra_data
    ):
        response = await self._http_client.get_broker_account_portfolio_assets_extract(  # NOQA
            account_id,
            broker_value,
            broker_parse_extra_data,
            account_parse_extra_data
        )
        response_class = GetBrokerAccountAssetExtractResponse(
            response=response,
            broker_value=broker_value
        )

        return await response_class.data()

    async def _get_broker_account_portfolio_assets_extract_safe(
        self,
        broker,
        account
    ):
        """ Request one (broker, account) pair without ever raising.

        Any error is converted into a result with an empty assets list and
        the error message, so concurrent account tasks cannot cancel each
        other or make the whole batch fail.
        """
        try:
            assets = await self.get_broker_account_portfolio_assets_extract(
                account_id=account.id,
                broker_value=broker.value,
                broker_parse_extra_data=broker.parse_extra_data,
                account_parse_extra_data=account.parse_extra_data
            )
            return BrokerAccountAssetsExtractResult(
                broker_value=broker.value,
                account_id=account.id,
                assets=assets
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                'Failed to get assets extract - '
                f'broker_value: {broker.value} account_id: {account.id}'
            )
            return BrokerAccountAssetsExtractResult(
                broker_value=broker.value,
                account_id=account.id,
                assets=[],
                error=str(exc)
            )

    async def get_brokers_account_portfolio_assets_extract(self, brokers):
        # One task per (broker, account) pair. Brokers without any account
        # are simply skipped, without raising.
        brokers_account_assets_extract_routine = [
            asyncio.create_task(
                self._get_broker_account_portfolio_assets_extract_safe(
                    broker=broker,
                    account=account
                )
            )
            for broker in brokers
            for account in broker.accounts
        ]

        # return_exceptions=True ensures a task error never cancels the other
        # account tasks; the safe wrapper already maps errors to results, so
        # no exception should reach here anyway.
        results = await asyncio.gather(
            *brokers_account_assets_extract_routine,
            return_exceptions=True
        )

        return [
            result for result in results
            if isinstance(result, BrokerAccountAssetsExtractResult)
        ]
