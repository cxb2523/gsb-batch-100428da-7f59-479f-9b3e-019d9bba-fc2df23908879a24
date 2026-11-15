"""Minimal, test-only stand-in for the ``pika`` package.

Only the symbols imported by the project's AMQP modules are provided. RabbitMQ
behaviour is never exercised by the Kafka retry tests; when the real ``pika``
package is installed this directory is not placed on ``sys.path`` (see
tests/conftest.py).
"""


class URLParameters(object):
    def __init__(self, url):
        self.url = url


class BasicProperties(object):
    def __init__(
        self,
        content_type=None,
        content_encoding=None,
        headers=None,
        delivery_mode=None,
        priority=None,
        correlation_id=None,
        reply_to=None,
        expiration=None,
        message_id=None,
        timestamp=None,
        type=None,
        user_id=None,
        app_id=None,
        cluster_id=None,
    ):
        self.content_type = content_type
        self.content_encoding = content_encoding
        self.headers = headers
        self.delivery_mode = delivery_mode
        self.priority = priority
        self.correlation_id = correlation_id
        self.reply_to = reply_to
        self.expiration = expiration
        self.message_id = message_id
        self.timestamp = timestamp
        self.type = type
        self.user_id = user_id
        self.app_id = app_id
        self.cluster_id = cluster_id
