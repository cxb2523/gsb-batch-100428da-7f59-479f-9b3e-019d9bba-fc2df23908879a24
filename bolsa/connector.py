from aiohttp import TCPConnector

from bolsa.contrib.mixins.singleton import SingletonCreateMixin


class B3HttpClientConnector(SingletonCreateMixin):

    def __init__(self, pool_size=30):
        self.pool_size = pool_size
        self.ssl = False
        self._connector = None

    def get_connector(self):
        # Built lazily: modern aiohttp versions require a running event
        # loop when the TCPConnector is instantiated.
        if self._connector is None:
            self._connector = TCPConnector(
                limit=self.pool_size,
                ssl=self.ssl
            )
        return self._connector
