"""Minimal stand-in for ``kafka.KafkaProducer`` (test-only stub)."""


class KafkaProducer(object):
    def __init__(self, **configs):
        self.configs = configs
        self.sent = []

    def send(self, topic, value=None, key=None, headers=None, partition=None, timestamp_ms=None):
        self.sent.append((topic, partition, key, value, headers))
        return None

    def flush(self, timeout=None):
        return None

    def close(self, timeout=None):
        return None
