"""Minimal stand-ins for ``kafka.admin`` (test-only stub)."""


class NewTopic(object):
    def __init__(self, name, num_partitions=1, replication_factor=1, replica_assignments=None, topic_configs=None):
        self.name = name
        self.num_partitions = num_partitions
        self.replication_factor = replication_factor
        self.replica_assignments = replica_assignments
        self.topic_configs = topic_configs or {}


class KafkaAdminClient(object):
    def __init__(self, **configs):
        self.configs = configs

    def create_topics(self, new_topics=None, timeout_ms=None, validate_only=False):
        return None

    def close(self):
        return None
