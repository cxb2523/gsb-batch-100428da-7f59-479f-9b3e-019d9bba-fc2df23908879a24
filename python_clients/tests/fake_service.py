import gzip
import json
import socket

from aiohttp import web


class FakeOzonService:
    """Локальный фейковый сервер, повторяющий асинхронный протокол сервиса.

    По умолчанию: на первый POST отвечает ``{"poll_token": ...}``, затем
    ``token_responses`` раз подряд тоже токеном, а после — готовыми данными.
    """

    def __init__(
            self,
            token='token-abc-123',
            token_responses=3,
            final_data=None,
            always_token=False,
            status=200,
            error_body='service error'):

        self.token = token
        self.token_responses = token_responses
        self.always_token = always_token
        self.status = status
        self.error_body = error_body

        if final_data is None:
            final_data = json.dumps(
                [{"sku": 1, "title": "Товар", "price": 100}],
                ensure_ascii=False)
        self.final_data = final_data

        self.requests = []
        self.request_bodies = []
        self.port = None
        self._runner = None
        self._site = None

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}/'

    async def start(self):
        app = web.Application()
        app.router.add_post('/', self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, '127.0.0.1', 0)
        await self._site.start()
        self.port = self._site._server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()

    def _decode(self, body):
        try:
            return gzip.decompress(body).decode('utf-8')
        except Exception:
            return body.decode('utf-8', errors='ignore')

    async def _handle(self, request):
        body = await request.read()
        self.request_bodies.append(self._decode(body))
        self.requests.append(request.raw_path)

        if self.status != 200:
            return web.Response(status=self.status, text=self.error_body)

        index = len(self.requests) - 1

        if not self.always_token and index >= self.token_responses:
            return web.Response(body=gzip.compress(self.final_data.encode('utf-8')))

        return web.Response(body=gzip.compress(
            json.dumps({'poll_token': self.token}).encode('utf-8')))


def get_free_port():
    """Порт, на котором заведомо никто не слушает (для теста обрыва связи)."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port
