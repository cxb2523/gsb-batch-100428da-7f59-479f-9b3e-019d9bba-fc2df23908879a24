"""Minimal, test-only stand-in for the ``kafka-python`` package.

Only the symbols imported by ``scrapy_distributed`` are provided. These classes
deliberately do NOT implement a broker: the retry tests inject their own
in-memory fakes by patching the names in ``scrapy_distributed.queues.kafka``.
When the real ``kafka-python`` package is installed, this directory is never
placed on ``sys.path`` (see tests/conftest.py).
"""
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.producer import KafkaProducer
from kafka.consumer import KafkaConsumer
from kafka.structs import TopicPartition, OffsetAndMetadata

__all__ = [
    "KafkaAdminClient",
    "NewTopic",
    "KafkaProducer",
    "KafkaConsumer",
    "TopicPartition",
    "OffsetAndMetadata",
]
