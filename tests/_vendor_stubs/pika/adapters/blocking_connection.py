"""Test-only stand-in for ``pika.adapters.blocking_connection``."""


class BlockingConnection(object):
    def __init__(self, parameters=None):
        self.parameters = parameters

    def channel(self):
        raise RuntimeError(
            "pika is stubbed in this test environment; no real broker channel "
            "can be opened. Patch scrapy_distributed.amqp_utils.connection "
            "helpers in tests that need a RabbitQueue."
        )

    def close(self):
        return None
