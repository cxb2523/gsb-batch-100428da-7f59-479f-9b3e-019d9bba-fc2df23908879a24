import asyncio

from bolsa.http_client import B3HttpClient
from bolsa.models import BrokerAccountParseExtraData, BrokerParseExtraData

LOGIN_HTML = """
<html><body>
  <input id="__VIEWSTATE" value="view-state"/>
  <input id="__VIEWSTATEGENERATOR" value="generator"/>
  <input id="__EVENTVALIDATION" value="event-validation"/>
</body></html>
"""


class FakeAsyncResponse:

    def __init__(self, html=''):
        self._html = html

    async def text(self):
        return self._html

    # Supports both ``async with session.post(...)`` (login) and
    # ``await session.post(...)`` (extract requests).
    def __await__(self):
        async def _resolve():
            return self
        return _resolve().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    """ Records calls instead of performing real HTTP requests. """

    def __init__(self, login_html):
        self.login_html = login_html
        self.get_count = 0
        self.post_count = 0

    def get(self, url):
        self.get_count += 1
        return FakeAsyncResponse(self.login_html)

    def post(self, url, data=None, headers=None):
        self.post_count += 1
        return FakeAsyncResponse('')


class TestB3HttpClientLogin:

    async def test_login_runs_only_once_under_concurrent_requests(self):
        session = FakeSession(login_html=LOGIN_HTML)
        client = B3HttpClient(
            username='fake-user',
            password='fake-password',
            session=session,
            captcha_service=None
        )
        broker_extra = BrokerParseExtraData(
            start_date='01/01/2020',
            end_date='31/01/2020'
        )
        account_extra = BrokerAccountParseExtraData(
            view_state='vs',
            view_state_generator='vsg',
            event_validation='ev'
        )

        # All tasks start logged out; the login lock must serialize them and
        # perform the login (one GET of the login page) exactly once.
        await asyncio.gather(*[
            client.get_broker_account_portfolio_assets_extract(
                account_id='1001',
                broker_value='90',
                broker_parse_extra_data=broker_extra,
                account_parse_extra_data=account_extra
            )
            for _ in range(10)
        ])

        assert session.get_count == 1
        # One login POST plus one extract POST per concurrent task.
        assert session.post_count == 11
        assert client.IS_LOGGED is True
