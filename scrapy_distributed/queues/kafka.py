#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import logging
from scrapy.http.request import Request
from scrapy.utils.misc import load_object

try:
    from scrapy.utils.reqser import request_to_dict
except ImportError:
    # scrapy.utils.reqser was removed in Scrapy 2.6+; Request.to_dict() was
    # introduced in the same release so this fallback is always safe for Scrapy >= 2.6.
    def request_to_dict(request, spider=None):
        return request.to_dict(spider=spider)
from w3lib.util import to_unicode
from scrapy_distributed.queues.common import BytesDump, keys_string, get_method

from kafka.admin import KafkaAdminClient, NewTopic
from kafka import KafkaProducer
from kafka import KafkaConsumer
from kafka.structs import TopicPartition, OffsetAndMetadata
from scrapy_distributed.queues import IQueue
from scrapy_distributed.common.queue_config import KafkaQueueConfig
import time


logger = logging.getLogger(__name__)

# Default cap on redelivery attempts for a message that cannot be restored
# into a Scrapy Request. After this many failures the message is skipped (its
# offset is committed) so a poison message cannot block the partition forever.
DEFAULT_MAX_RETRIES = 3


def _try_operation(function):
    """Wrap unary method by reconnect procedure"""

    def wrapper(self, *args, **kwargs):
        retries = 0
        last_exception = None
        while retries < 10:
            try:
                return function(self, *args, **kwargs)
            except Exception as e:
                retries += 1
                last_exception = e
                msg = "Function %s failed. Reconnecting... (%d times)" % (
                    str(function),
                    retries,
                )
                logger.error(msg)
                self.connect()
                time.sleep((retries - 1) * 5)
        if last_exception:
            raise last_exception
        return None

    return wrapper


class KafkaQueue(IQueue):
    """Per-spider FIFO queue"""

    def __init__(
        self,
        connection_conf,
        name,
        num_partitions = 10,
        replication_factor = 1,
        arguments=None,
        max_retries=DEFAULT_MAX_RETRIES,
    ):
        """Initialize per-spider Kafka queue.

        Parameters:
            connection_conf -- Kafka connection_conf
            key -- rabbitmq routing key
            max_retries -- number of redelivery attempts for a message that
                fails to be restored into a Scrapy Request before it is skipped
                and counted as failed (default: DEFAULT_MAX_RETRIES)
        """
        self.connection_conf = connection_conf
        self.name = name
        self.topic = f"{name}.spider.queue"
        self.num_partitions = num_partitions
        self.replication_factor = replication_factor
        self.arguments = arguments
        self.max_retries = max_retries
        self.admin_client = None
        self.producer = None
        self.consumer = None
        # Retry bookkeeping for unparseable messages, keyed by
        # (topic, partition, offset) -> number of failed attempts seen so far.
        self._retry_counts = {}
        # Number of messages given up on after exhausting max_retries.
        self.failed_count = 0
        # Set by every pop() call: True when the latest poll yielded a message
        # that could not be turned into a Request (either redelivered for a
        # later retry or skipped after exhausting retries). Allows the
        # scheduler to distinguish "no message available" from a parse failure
        # without changing the pop() return contract used by RabbitQueue.
        self.last_pop_failed = False
        self.connect()

    @classmethod
    def from_queue_conf(cls, connection_conf, queue_conf: KafkaQueueConfig):
        return cls(
            connection_conf,
            name=queue_conf.topic,
            num_partitions=queue_conf.num_partitions,
            replication_factor=queue_conf.replication_factor,
            arguments=queue_conf.arguments,
            max_retries=queue_conf.max_retries,
        )

    def __len__(self):
        """Return the length of the queue"""
        return 1

    @_try_operation
    def pop(self, scheduler):
        """Pop a message.

        The fetched message body is restored into a Scrapy Request *before*
        its offset is committed. On failure the offset is left uncommitted and
        the consumer is seeked back to the message so the next poll() returns
        it again. A single message is redelivered at most ``max_retries``
        times; once that cap is reached the message is skipped by committing
        its offset and counted in ``failed_count`` so the queue can keep
        consuming the messages behind it.
        """
        self.last_pop_failed = False
        msg = None
        try:
            poll_msg = self.consumer.poll(timeout_ms=500, update_offsets=True, max_records=1)
            for msgs in poll_msg.values():
                for candidate in msgs:
                    msg = candidate
                    break
                break
        except Exception:
            logger.exception("kafaka queue, pop exception")
            return None
        if msg is None or msg.value is None:
            return None
        try:
            request = self._make_request(msg.value, scheduler)
        except Exception as e:
            return self._handle_failed_message(msg, e)
        # The message was restored successfully: only now is it safe to
        # advance the committed offset.
        self._commit_message(msg)
        self._retry_counts.pop(self._message_key(msg), None)
        return request

    def _message_key(self, msg):
        return (msg.topic, msg.partition, msg.offset)

    def _commit_message(self, msg):
        """Commit the offset right after ``msg`` (next offset to consume)."""
        partition = TopicPartition(msg.topic, msg.partition)
        offsets = {partition: OffsetAndMetadata(msg.offset + 1, None)}
        self.consumer.commit(offsets=offsets)

    def _handle_failed_message(self, msg, error):
        """Apply the bounded-redelivery policy to an unparseable message."""
        key = self._message_key(msg)
        attempts = self._retry_counts.get(key, 0) + 1
        self._retry_counts[key] = attempts
        if attempts <= self.max_retries:
            logger.error(
                "Failed to restore request from kafka message %s (attempt %d/%d); "
                "offset will not be committed and the message will be redelivered: %s",
                key, attempts, self.max_retries + 1, error,
            )
            # Rewind so the next poll() sees the same message again.
            partition = TopicPartition(msg.topic, msg.partition)
            self.consumer.seek(partition, msg.offset)
            self.last_pop_failed = True
            return None
        logger.error(
            "Giving up on kafka message %s after %d failed attempts; skipping it "
            "and committing its offset so the queue can continue: %s",
            key, attempts, error,
        )
        # Skip the poison message: committing past it unblocks the partition.
        self._commit_message(msg)
        self._retry_counts.pop(key, None)
        self.failed_count += 1
        self.last_pop_failed = True
        return None

    def _make_request(self, body, scheduler):
        return self._request_from_dict(
            json.loads(body.decode()), scheduler.spider
        )

    @_try_operation
    def push(self, request, scheduler, headers=None, partition=0):
        """Push a message"""
        body: str = json.dumps(
            keys_string(self._request_to_dict(request, scheduler.spider)),
            cls=BytesDump,
        )
        logger.debug(f"push message, body: {body}")
        self.producer.send(self.topic, body.encode(), partition=partition)

    def connect(self):
        """Make a connection"""
        logger.info(f"connect kafka: {self.connection_conf}")
        if self.admin_client:
            try:
                self.admin_client.close()
            except:
                pass
        self.admin_client = KafkaAdminClient(bootstrap_servers=self.connection_conf)
        topic_list = [NewTopic(name=self.topic,
                               num_partitions=self.num_partitions,
                               replication_factor=self.replication_factor)]
        try:
            self.admin_client.create_topics(new_topics=topic_list, validate_only=False)
        except Exception as e:
            logger.error(e)

        if self.producer:
            self.producer.close()
        self.producer = KafkaProducer(bootstrap_servers=self.connection_conf)
        if self.consumer:
            self.consumer.close()
        # Auto-commit is disabled on purpose: an offset may only be committed
        # after its message has been successfully restored into a Request,
        # otherwise a parse failure would silently drop the message. All other
        # consumer settings (including the default offset reset policy) are
        # left unchanged from the previous implementation.
        self.consumer = KafkaConsumer(
            self.topic,
            group_id=f"{self.topic}.spider.consumer",
            bootstrap_servers=self.connection_conf,
            enable_auto_commit=False,
        )

    def close(self):
        """Close channel"""
        logger.error(f"close Kafka connection")
        self.admin_client.close()
        self.producer.close()
        self.consumer.close()

    def clear(self):
        """Clear queue/stack"""
        # self.channel.queue_purge(self.name)

    @classmethod
    def _request_from_dict(cls, d, spider=None):
        """Create Request object from a dict.

        If a spider is given, it will try to resolve the callbacks looking at the
        spider for methods with the same name.
        """
        cb = d.get("callback", None)
        if cb and spider:
            cb = get_method(spider, cb)
        eb = d.get("errback", None)
        if eb and spider:
            eb = get_method(spider, eb)
        request_cls = load_object(d.get("_class", None)) if "_class" in d else Request
        return request_cls(
            url=to_unicode(d.get("url", None)),
            callback=cb,
            errback=eb,
            method=d.get("method", None),
            headers=d.get("headers", None),
            body=d.get("body", None),
            cookies=d.get("cookies", None),
            meta=d.get("meta", None),
            encoding=d.get("_encoding", "utf-8"),
            priority=d.get("priority", 0),
            dont_filter=d.get("dont_filter", True),
            flags=d.get("flags", None),
        )

    @classmethod
    def _request_to_dict(cls, request, spider=None):
        d = request_to_dict(request, spider)
        new_dict = dict()
        for key, value in d.items():
            if value:
                new_dict[key] = value
        logger.debug(f"request_to_dict: {d}")
        return new_dict


__all__ = ["KafkaQueue"]
