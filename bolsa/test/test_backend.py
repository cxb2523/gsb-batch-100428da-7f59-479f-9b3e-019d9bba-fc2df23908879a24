from bolsa.backend import B3AsyncBackend
from bolsa.models import (
    Broker,
    BrokerAccount,
    BrokerAccountAssetsExtractResult,
    BrokerAccountParseExtraData,
    BrokerParseExtraData
)
from bolsa.responses import GetBrokerAccountAssetExtractResponse

ASSETS_HTML = f"""
<html><body>
<table id="{GetBrokerAccountAssetExtractResponse.ASSETS_TABLE_ID}">
  <tbody>
    <tr>
      <td>05/06/2020</td>
      <td>C</td>
      <td>Mercado a Vista</td>
      <td>ignored</td>
      <td>AZUL4</td>
      <td>AZUL PN N2</td>
      <td>2</td>
      <td>21,09</td>
      <td>42,18</td>
      <td>1</td>
    </tr>
  </tbody>
</table>
</body></html>
"""

NO_ASSETS_HTML = '<html><body><div>no table here</div></body></html>'


class FakeResponse:

    def __init__(self, html):
        self._html = html

    async def text(self):
        return self._html


class FakeB3HttpClient:
    """ In-memory stub replacing B3HttpClient; no real B3 login or network.

    ``failures`` maps (broker_value, account_id) to the exception that the
    assets extract request must raise for that pair.
    """

    def __init__(self, responses=None, failures=None):
        self.responses = responses if responses is not None else {}
        self.failures = failures if failures is not None else {}
        self.login_calls = 0
        self.extract_calls = []

    async def login(self):
        self.login_calls += 1
        return ''

    async def get_broker_account_portfolio_assets_extract(
        self,
        account_id,
        broker_value,
        broker_parse_extra_data,
        account_parse_extra_data
    ):
        self.extract_calls.append((broker_value, account_id))
        failure = self.failures.get((broker_value, account_id))
        if failure is not None:
            raise failure
        return FakeResponse(self.responses[(broker_value, account_id)])


def _make_backend(fake_http_client):
    # Bypass __init__ so no aiohttp connector/session (and therefore no real
    # B3 login or network access) is created.
    backend = B3AsyncBackend.__new__(B3AsyncBackend)
    backend._connector = None
    backend.username = 'fake-user'
    backend.password = 'fake-password'
    backend.captcha_service = None
    backend.__dict__['_http_client'] = fake_http_client
    return backend


def _make_broker(broker_value, account_ids):
    return Broker(
        value=broker_value,
        name=f'broker-{broker_value}',
        parse_extra_data=BrokerParseExtraData(
            start_date='01/01/2020',
            end_date='31/01/2020'
        ),
        accounts=[
            BrokerAccount(
                id=account_id,
                parse_extra_data=BrokerAccountParseExtraData(
                    view_state='vs',
                    view_state_generator='vsg',
                    event_validation='ev'
                )
            )
            for account_id in account_ids
        ]
    )


class TestGetBrokersAccountPortfolioAssetsExtract:

    async def test_all_accounts_success_returns_one_result_per_account(self):
        brokers = [
            _make_broker('90', ['1001', '1002']),
            _make_broker('386', ['2001'])
        ]
        responses = {
            ('90', '1001'): ASSETS_HTML,
            ('90', '1002'): NO_ASSETS_HTML,
            ('386', '2001'): ASSETS_HTML
        }
        fake_client = FakeB3HttpClient(responses=responses)
        backend = _make_backend(fake_client)

        results = (
            await backend.get_brokers_account_portfolio_assets_extract(
                brokers
            )
        )

        assert len(results) == 3
        assert all(
            isinstance(result, BrokerAccountAssetsExtractResult)
            for result in results
        )

        by_key = {
            (result.broker_value, result.account_id): result
            for result in results
        }
        assert set(by_key.keys()) == {
            ('90', '1001'),
            ('90', '1002'),
            ('386', '2001')
        }

        assert by_key[('90', '1001')].error is None
        assert len(by_key[('90', '1001')].assets) == 1
        assert by_key[('90', '1001')].assets[0].raw_negotiation_code == (
            'AZUL4'
        )

        # An account with no negotiated assets still succeeds with an empty
        # list rather than an error.
        assert by_key[('90', '1002')].error is None
        assert by_key[('90', '1002')].assets == []

        assert by_key[('386', '2001')].error is None
        assert len(by_key[('386', '2001')].assets) == 1

        # Every (broker, account) pair was actually requested.
        assert sorted(fake_client.extract_calls) == sorted(responses.keys())

    async def test_single_account_failure_keeps_other_accounts_results(self):
        brokers = [
            _make_broker('90', ['1001', '1002']),
            _make_broker('386', ['2001'])
        ]
        responses = {
            ('90', '1001'): ASSETS_HTML,
            ('90', '1002'): ASSETS_HTML,
            ('386', '2001'): ASSETS_HTML
        }
        fake_client = FakeB3HttpClient(
            responses=responses,
            failures={
                ('90', '1002'): RuntimeError('boom: request timed out')
            }
        )
        backend = _make_backend(fake_client)

        results = (
            await backend.get_brokers_account_portfolio_assets_extract(
                brokers
            )
        )

        # The failed account does not remove any of the other results.
        assert len(results) == 3

        by_key = {
            (result.broker_value, result.account_id): result
            for result in results
        }

        failed = by_key[('90', '1002')]
        assert failed.assets == []
        assert failed.error == 'boom: request timed out'

        succeeded_1 = by_key[('90', '1001')]
        assert succeeded_1.error is None
        assert len(succeeded_1.assets) == 1

        succeeded_2 = by_key[('386', '2001')]
        assert succeeded_2.error is None
        assert len(succeeded_2.assets) == 1

        # The failing account was still attempted.
        assert ('90', '1002') in fake_client.extract_calls

    async def test_broker_without_accounts_is_skipped_without_error(self):
        brokers = [
            _make_broker('90', ['1001']),
            _make_broker('386', [])
        ]
        responses = {('90', '1001'): ASSETS_HTML}
        fake_client = FakeB3HttpClient(responses=responses)
        backend = _make_backend(fake_client)

        results = (
            await backend.get_brokers_account_portfolio_assets_extract(
                brokers
            )
        )

        assert len(results) == 1
        result = results[0]
        assert result.broker_value == '90'
        assert result.account_id == '1001'
        assert result.error is None
        assert len(result.assets) == 1

        # No request was ever made for the broker without accounts.
        assert fake_client.extract_calls == [('90', '1001')]

    async def test_no_accounts_at_all_returns_empty_list(self):
        brokers = [
            _make_broker('90', []),
            _make_broker('386', [])
        ]
        fake_client = FakeB3HttpClient()
        backend = _make_backend(fake_client)

        results = (
            await backend.get_brokers_account_portfolio_assets_extract(
                brokers
            )
        )

        assert results == []
        assert fake_client.extract_calls == []
