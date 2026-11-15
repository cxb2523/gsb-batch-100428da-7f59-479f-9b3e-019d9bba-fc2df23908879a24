#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Retry / redelivery behaviour of KafkaQueue.

These tests use in-memory mocks of kafka-python's KafkaConsumer / KafkaProducer
/ KafkaAdminClient. No real Kafka broker is required and the tests do not rely
on network access.
"""
import json

from types import SimpleNamespace
from unittest.mock import patch

import scrapy
from kafka.structs import TopicPartition, ConsumerRecord

from scrapy_distributed.common.queue_config import KafkaQueueConfig
from scrapy_distributed.queues import kafka as kafka_module
from scrapy_distributed.queues.kafka import KafkaQueue


class FakeStats(object):
    """Minimal stand-in for scrapy's StatsCollector (inc/get values)."""

    def __init__(self):
        self._values = {}

    def inc_value(self, key, count=1, start=0, spider=None):
        self._values[key] = self._values.get(key, start) + count

    def get_value(self, key, default=None, spider=None):
        return self._values.get(key, default)


TOPIC_NAME = "testspider"
TOPIC = f"{TOPIC_NAME}.spider.queue"
PARTITION = 0


class ExampleSpider(scrapy.Spider):
    name = "testspider"

    def parse(self, response):
        pass


def make_record(offset, value):
    return ConsumerRecord(topic=TOPIC, partition=PARTITION, offset=offset, value=value)


def valid_body(url="http://example.com/1", callback="parse"):
    return json.dumps({"url": url, "callback": callback}).encode()


class FakeAdminClient(object):
    def __init__(self, **configs):
        self.configs = configs
        self.created_topics = []

    def create_topics(self, new_topics=None, timeout_ms=None, validate_only=False):
        self.created_topics.extend(new_topics or [])

    def close(self):
        pass


class FakeProducer(object):
    def __init__(self, **configs):
        self.configs = configs
        self.sent = []

    def send(self, topic, value=None, key=None, headers=None, partition=None, timestamp_ms=None):
        self.sent.append({"topic": topic, "value": value, "partition": partition})

    def close(self):
        pass


class FakeConsumer(object):
    """In-memory consumer modelling the subset of kafka-python semantics used.

    * ``poll`` returns at most one record from the current partition position.
    * ``seek`` rewinds the position so a redelivered message shows up again.
    * ``commit`` stores the committed offsets; nothing is auto-committed.
    """

    def __init__(self, *topics, **configs):
        self.topics = topics
        self.configs = configs
        self._records = []
        self._tp = TopicPartition(TOPIC, PARTITION)
        self._position = 0
        self.committed = {}
        self.poll_calls = 0
        # Mirror the kafka-python default so callers (and tests) can always
        # inspect enable_auto_commit even when it is passed explicitly.
        self.configs.setdefault("enable_auto_commit", True)

    def seed(self, records):
        self._records = list(records)

    def poll(self, timeout_ms=0, max_records=None, update_offsets=True):
        self.poll_calls += 1
        if self._position >= len(self._records):
            return {}
        record = self._records[self._position]
        self._position += 1
        return {self._tp: [record]}

    def seek(self, partition, offset):
        assert partition == self._tp
        self._position = offset

    def commit(self, offsets=None, timeout_ms=None):
        if offsets is None:
            return
        for tp, meta in offsets.items():
            self.committed[tp] = meta.offset

    def position(self, partition):
        return self._position

    def close(self, autocommit=True):
        pass


def make_kafka_queue(records=(), max_retries=3):
    """Build a KafkaQueue wired to in-memory fakes (no broker contact)."""
    fake_admin = FakeAdminClient()
    fake_producer = FakeProducer()
    fake_consumer = FakeConsumer()
    fake_consumer.seed(list(records))

    def consumer_factory(*topics, **configs):
        # Keep a record of the configuration the queue actually requested.
        fake_consumer.configs.update(configs)
        return fake_consumer

    with patch.object(kafka_module, "KafkaAdminClient", return_value=fake_admin), \
         patch.object(kafka_module, "KafkaProducer", return_value=fake_producer), \
         patch.object(kafka_module, "KafkaConsumer", side_effect=consumer_factory):
        queue = KafkaQueue(
            connection_conf="localhost:9092",
            name=TOPIC_NAME,
            max_retries=max_retries,
        )

    return queue, SimpleNamespace(
        admin=fake_admin, producer=fake_producer, consumer=fake_consumer
    )


def make_scheduler():
    return SimpleNamespace(spider=ExampleSpider())


class TestKafkaQueueConstruction:
    def test_consumer_has_auto_commit_disabled(self):
        _, fakes = make_kafka_queue()
        assert fakes.consumer.configs["enable_auto_commit"] is False

    def test_topic_creation_and_producer_are_unchanged(self):
        queue, fakes = make_kafka_queue()
        # Topic is still declared via the admin client with the same name.
        assert len(fakes.admin.created_topics) == 1
        assert fakes.admin.created_topics[0].name == queue.topic
        # push() still publishes through the producer to the same topic.
        spider = ExampleSpider()
        request = scrapy.Request("http://example.com/push", callback=spider.parse)
        queue.push(request, SimpleNamespace(spider=spider))
        assert len(fakes.producer.sent) == 1
        assert fakes.producer.sent[0]["topic"] == queue.topic

    def test_max_retries_comes_from_queue_conf(self):
        conf = KafkaQueueConfig(topic=TOPIC_NAME, max_retries=2)
        with patch.object(kafka_module, "KafkaAdminClient", return_value=FakeAdminClient()), \
             patch.object(kafka_module, "KafkaProducer", return_value=FakeProducer()), \
             patch.object(kafka_module, "KafkaConsumer", return_value=FakeConsumer()):
            queue = KafkaQueue.from_queue_conf("localhost:9092", conf)
        assert queue.max_retries == 2


class TestSuccessfulMessage:
    def test_valid_message_is_restored_and_offset_committed(self):
        record = make_record(0, valid_body())
        queue, fakes = make_kafka_queue([record])
        tp = TopicPartition(TOPIC, PARTITION)

        request = queue.pop(make_scheduler())

        assert request is not None
        assert request.url == "http://example.com/1"
        assert request.callback.__name__ == "parse"
        # Offset committed only after the successful restore.
        assert fakes.consumer.committed == {tp: 1}
        assert queue.last_pop_failed is False
        assert queue.failed_count == 0

    def test_empty_poll_returns_none_without_commit_or_failure(self):
        queue, fakes = make_kafka_queue([])

        result = queue.pop(make_scheduler())

        assert result is None
        assert fakes.consumer.committed == {}
        assert queue.last_pop_failed is False
        assert queue.failed_count == 0


class TestUnparseableMessageRetry:
    def test_invalid_json_does_not_commit_and_is_redelivered(self):
        record = make_record(0, b"{not valid json")
        queue, fakes = make_kafka_queue([record], max_retries=3)
        tp = TopicPartition(TOPIC, PARTITION)

        first = queue.pop(make_scheduler())

        assert first is None
        # Offset must NOT be committed for the failed message.
        assert fakes.consumer.committed == {}
        assert queue.last_pop_failed is True
        assert queue.failed_count == 0
        # Consumer was rewound so the next poll sees the same record again.
        assert fakes.consumer.position(tp) == 0

        # Second attempt fails again, still no commit.
        second = queue.pop(make_scheduler())
        assert second is None
        assert fakes.consumer.committed == {}
        assert queue.failed_count == 0

    def test_unknown_callback_does_not_commit_and_is_redelivered(self):
        record = make_record(0, valid_body(callback="does_not_exist"))
        queue, fakes = make_kafka_queue([record], max_retries=3)

        result = queue.pop(make_scheduler())

        assert result is None
        assert fakes.consumer.committed == {}
        assert queue.last_pop_failed is True
        assert queue.failed_count == 0

    def test_message_succeeds_after_transient_failure_then_commits(self):
        good_record = make_record(0, valid_body())
        queue, fakes = make_kafka_queue([good_record], max_retries=3)
        scheduler = make_scheduler()

        # Force one transient parse failure, then let it succeed.
        original_make_request = queue._make_request
        calls = {"n": 0}

        def flaky(body, sched):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("transient decode failure")
            return original_make_request(body, sched)

        with patch.object(queue, "_make_request", side_effect=flaky):
            first = queue.pop(scheduler)
        assert first is None
        assert fakes.consumer.committed == {}

        request = queue.pop(scheduler)
        assert request is not None
        assert request.url == "http://example.com/1"
        assert fakes.consumer.committed == {TopicPartition(TOPIC, PARTITION): 1}
        # Retry bookkeeping is cleaned up once the message succeeds.
        assert queue._retry_counts == {}


class TestRetryExhaustion:
    def test_poison_message_is_skipped_after_retry_cap_and_queue_continues(self):
        poison = make_record(0, b"{broken json")
        good = make_record(1, valid_body(url="http://example.com/2"))
        queue, fakes = make_kafka_queue([poison, good], max_retries=2)
        scheduler = make_scheduler()
        tp = TopicPartition(TOPIC, PARTITION)

        # Attempts 1 and 2: redelivered, no commit.
        for _ in range(2):
            assert queue.pop(scheduler) is None
            assert fakes.consumer.committed == {}
            assert queue.failed_count == 0

        # Attempt 3 (initial attempt + 2 retries = max_retries + 1): the
        # message is skipped, its offset committed, failure counted.
        skipped = queue.pop(scheduler)
        assert skipped is None
        assert fakes.consumer.committed == {tp: 1}
        assert queue.failed_count == 1
        assert queue.last_pop_failed is True
        assert queue._retry_counts == {}

        # The next normal message is still consumable and advances the offset.
        request = queue.pop(scheduler)
        assert request is not None
        assert request.url == "http://example.com/2"
        assert fakes.consumer.committed == {tp: 2}
        assert queue.failed_count == 1

    def test_default_retry_limit_is_three(self):
        poison = make_record(0, b"{broken json")
        queue, fakes = make_kafka_queue([poison])  # default max_retries=3
        scheduler = make_scheduler()

        for _ in range(3):
            assert queue.pop(scheduler) is None
            assert fakes.consumer.committed == {}

        # Fourth processing of the same message exhausts the limit.
        assert queue.pop(scheduler) is None
        assert fakes.consumer.committed == {TopicPartition(TOPIC, PARTITION): 1}
        assert queue.failed_count == 1


class TestSchedulerStats:
    def _make_scheduler(self, queue, stats):
        """Build a DistributedQueueScheduler without running Scrapy machinery."""
        from scrapy_distributed.schedulers.common import DistributedQueueScheduler

        scheduler = DistributedQueueScheduler.__new__(DistributedQueueScheduler)
        scheduler.queue = queue
        scheduler.stats = stats
        scheduler.spider = ExampleSpider()
        scheduler.waiting = False
        scheduler.closing = False
        scheduler.dqs = None
        # Scrapy's base Scheduler.__len__ requires a len()-able memory queue.
        scheduler.mqs = []
        return scheduler

    def test_failed_pop_increments_failed_stat_without_empty_queue_log(self, caplog):
        import logging

        queue, _ = make_kafka_queue([make_record(0, b"{broken json")], max_retries=0)
        stats = FakeStats()
        scheduler = self._make_scheduler(queue, stats)

        with caplog.at_level(logging.INFO):
            result = scheduler.next_request()

        assert result is None
        assert stats.get_value("scheduler/dequeued/failed", spider=scheduler.spider) == 1
        assert stats.get_value("scheduler/dequeued/distributed-queue", default=0, spider=scheduler.spider) == 0
        # The empty-queue "Waiting for messages" log must not be emitted for a
        # parse failure.
        assert not any("Waiting for messages" in record.message for record in caplog.records)

    def test_successful_pop_increments_dequeued_stat(self):
        queue, _ = make_kafka_queue([make_record(0, valid_body())])
        stats = FakeStats()
        scheduler = self._make_scheduler(queue, stats)

        request = scheduler.next_request()

        assert request is not None
        assert request.url == "http://example.com/1"
        assert stats.get_value("scheduler/dequeued/distributed-queue", spider=scheduler.spider) == 1
        assert stats.get_value("scheduler/dequeued/failed", default=0, spider=scheduler.spider) == 0

    def test_empty_poll_keeps_original_waiting_behaviour(self):
        queue, _ = make_kafka_queue([])
        stats = FakeStats()
        scheduler = self._make_scheduler(queue, stats)

        with patch("scrapy_distributed.schedulers.common.time.sleep"):
            assert scheduler.next_request() is None

        assert scheduler.waiting is True
        assert stats.get_value("scheduler/dequeued/failed", default=0, spider=scheduler.spider) == 0

    def test_skipped_poison_message_unblocks_later_normal_message(self):
        # max_retries=0 means the first failure already exhausts the budget.
        poison = make_record(0, b"{broken json")
        good = make_record(1, valid_body(url="http://example.com/2"))
        queue, _ = make_kafka_queue([poison, good], max_retries=0)
        stats = FakeStats()
        scheduler = self._make_scheduler(queue, stats)

        # First pop: poison message is skipped and counted as failed.
        assert scheduler.next_request() is None
        assert stats.get_value("scheduler/dequeued/failed", spider=scheduler.spider) == 1
        assert queue.failed_count == 1

        # Second pop: the queue has moved on and the healthy message flows.
        request = scheduler.next_request()
        assert request is not None
        assert request.url == "http://example.com/2"
        assert stats.get_value("scheduler/dequeued/distributed-queue", spider=scheduler.spider) == 1
        # The failed counter is not touched by the successful dequeue.
        assert stats.get_value("scheduler/dequeued/failed", spider=scheduler.spider) == 1
