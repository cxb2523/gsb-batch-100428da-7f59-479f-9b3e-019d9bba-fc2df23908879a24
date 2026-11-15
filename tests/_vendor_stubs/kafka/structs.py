"""Value types mirroring kafka-python's ``kafka.structs``."""


class TopicPartition(object):
    """A namespace for a topic name and partition number."""

    def __init__(self, topic, partition):
        self.topic = topic
        self.partition = partition

    def __eq__(self, other):
        return (
            isinstance(other, TopicPartition)
            and self.topic == other.topic
            and self.partition == other.partition
        )

    def __hash__(self):
        return hash((self.topic, self.partition))

    def __repr__(self):
        return "TopicPartition(topic=%r, partition=%r)" % (
            self.topic,
            self.partition,
        )


class OffsetAndMetadata(object):
    """Committed offset plus opaque metadata string.

    Signature mirrors kafka-python: leader_epoch is the third positional
    argument and metadata the second.
    """

    def __init__(self, offset, metadata=None, leader_epoch=-1):
        self.offset = offset
        self.metadata = metadata
        self.leader_epoch = leader_epoch

    def __repr__(self):
        return "OffsetAndMetadata(offset=%r, metadata=%r)" % (
            self.offset,
            self.metadata,
        )


class ConsumerRecord(object):
    """A single consumed message, mirroring kafka-python's record shape."""

    def __init__(self, topic, partition, offset, value, key=None, headers=None):
        self.topic = topic
        self.partition = partition
        self.offset = offset
        self.value = value
        self.key = key
        self.headers = headers or []
        self.timestamp = None
        self.timestamp_type = None
        self.checksum = None
        self.serialized_key_size = -1
        self.serialized_header_size = -1
        self.serialized_value_size = -1 if value is None else len(value)

    def __repr__(self):
        return "ConsumerRecord(topic=%r, partition=%r, offset=%r)" % (
            self.topic,
            self.partition,
            self.offset,
        )
