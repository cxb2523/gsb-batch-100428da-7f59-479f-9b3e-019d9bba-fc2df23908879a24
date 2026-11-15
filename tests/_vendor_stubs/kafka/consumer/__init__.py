"""Minimal stand-in for ``kafka.KafkaConsumer`` (test-only stub).

The real behaviour (poll/seek/commit/position) is supplied by the in-memory
fakes defined inside the retry tests; this base only records construction
arguments so tests can assert on the consumer configuration (e.g. that
auto-commit is disabled).
"""


class KafkaConsumer(object):
    DEFAULT_CONFIG = {
        "bootstrap_servers": "localhost",
        "client_id": "kafka-python-test-stub",
        "group_id": None,
        "enable_auto_commit": False,
        "auto_offset_reset": "earliest",
    }

    def __init__(self, *topics, **configs):
        self.topics = topics
        self.configs = dict(self.DEFAULT_CONFIG)
        self.configs.update(configs)

    def poll(self, timeout_ms=0, max_records=None, update_offsets=True):
        return {}

    def seek(self, partition, offset):
        return None

    def commit(self, offsets=None, timeout_ms=None):
        return None

    def position(self, partition):
        return 0

    def close(self, autocommit=True):
        return None
