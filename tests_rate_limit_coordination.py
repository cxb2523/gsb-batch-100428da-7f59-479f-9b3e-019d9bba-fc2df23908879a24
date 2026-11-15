#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Tests for the coordination of host based rate limits and task level backoff.

These tests run without a MariaDB instance and without any network access:
* A fake cursor keeps the queue and the rate limit table as Python objects
  and mimics the stored procedures / functions used by exoskeleton.
  Time is controlled by the fake, so backoff windows can be skipped
  without sleeping.
* Fake HTTP responses verify how exoskeleton reacts to status codes
  like 429 (rate limit) or 500 (temporary error).

Covered behavior:
* normal retry after a temporary error (increasing delay, counted tries)
* a rate limit on a host blocks *all* queued tasks of that host
* exceeding queue_max_retries turns a temporary error into a permanent one
* tasks of a host are fetched again after the backoff window ended
* the bot waits out a rate limit instead of stopping with an "empty" queue
* no uncaught exceptions for deleted or nonexistent tasks
~~~~~~~~~~~~~~~~~~~~~
Source: https://github.com/RuedigerVoigt/exoskeleton
(c) 2019-2021 Rüdiger Voigt:
Released under the Apache License 2.0
"""

import datetime
import hashlib
import logging
import pathlib
from unittest import mock

import pytest

from exoskeleton import actions
from exoskeleton import error_manager
from exoskeleton import exo_url
from exoskeleton import queue_manager
from exoskeleton import statistics_manager

logging.basicConfig(level=logging.WARNING)

# Error type IDs as defined in the errorType table of the database schema:
PERMANENT_ERROR_IDS = {1, 3, 400, 401, 402, 403, 404, 405, 406,
                       407, 410, 414, 451, 501}
TEMPORARY_ERROR_IDS = {0, 2, 4, 5, 408, 429, 500, 502, 503, 504,
                       509, 529, 598}


def _sha256(text: str) -> str:
    "Hash a string the same way the database does (SHA2(text, 256))."
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class FakeCursor:
    """Simulate the queue and rateLimits tables plus the stored procedures
       and SQL functions exoskeleton uses to coordinate host based rate
       limits and task level backoff."""

    def __init__(self) -> None:
        # Controllable clock: tests advance it to skip backoff windows.
        self.now = datetime.datetime(2026, 1, 1, 12, 0, 0)
        self.queue: dict = {}
        self.rate_limits: dict = {}
        self.host_stats: list = []
        self._result: list = []
        self._added_counter = 0

    def advance(self, seconds: float) -> None:
        "Move the fake clock forward."
        self.now += datetime.timedelta(seconds=seconds)

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # pymysql cursor interface
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())
        if normalized == 'CALL next_queue_object_SP();':
            self._result = self._next_queue_object()
        elif normalized == 'SELECT num_items_with_temporary_errors();':
            self._result = [(sum(1 for r in self.queue.values()
                                 if r['causesError'] in TEMPORARY_ERROR_IDS),)]
        elif normalized == 'SELECT num_items_with_permanent_error();':
            self._result = [(sum(1 for r in self.queue.values()
                                 if r['causesError'] in PERMANENT_ERROR_IDS),)]
        elif normalized == 'SELECT num_tasks_in_queue_without_error();':
            self._result = [(sum(1 for r in self.queue.values()
                                 if r['causesError'] is None),)]
        elif normalized == 'SELECT num_tasks_with_active_rate_limit();':
            self._result = [(self._num_tasks_with_active_rate_limit(),)]
        elif normalized.startswith('SELECT id FROM fileMaster'):
            # no file has been processed in these tests
            self._result = []
        elif normalized.startswith('SELECT id FROM queue WHERE urlHash'):
            url, action = params
            self._result = [
                (r['id'],) for r in self._sorted_queue()
                if r['urlHash'] == _sha256(str(url)) and r['action'] == action]
        elif normalized.startswith('SELECT noContactUntil FROM rateLimits'):
            entry = self.rate_limits.get(_sha256(params[0]))
            if entry and entry['noContactUntil'] > self.now:
                self._result = [(entry['noContactUntil'],)]
            else:
                self._result = []
        else:
            raise ValueError(f'FakeCursor: unexpected SQL: {normalized}')
        return 1

    def callproc(self, procname, args=()):
        if procname == 'add_to_queue_SP':
            uuid, action, url, fqdn, prettify = args
            self._added_counter += 1
            self.queue[uuid] = {
                'id': uuid,
                'action': action,
                'url': str(url),
                'urlHash': _sha256(str(url)),
                'fqdnHash': _sha256(str(fqdn)),
                'prettifyHtml': prettify,
                'addedToQueue': self._added_counter,
                'causesError': None,
                'numTries': 0,
                'delayUntil': None}
        elif procname == 'delete_from_queue_SP':
            self.queue.pop(args[0], None)
        elif procname == 'increment_num_tries_SP':
            row = self.queue.get(args[0])
            if row is None:
                # SELECT on a missing id returns an empty result set
                self._result = []
                return
            row['numTries'] += 1
            self._result = [(row['numTries'],)]
            return
        elif procname == 'add_crawl_delay_SP':
            uuid, wait_seconds, error_type = args
            row = self.queue.get(uuid)
            if row is not None:
                url_hash = row['urlHash']
                new_delay = self.now + datetime.timedelta(seconds=wait_seconds)
                for item in self.queue.values():
                    if item['urlHash'] == url_hash:
                        item['delayUntil'] = new_delay
                row['causesError'] = error_type
        elif procname == 'mark_permanent_error_SP':
            row = self.queue.get(args[0])
            if row is not None:
                row['causesError'] = args[1]
                row['delayUntil'] = None
        elif procname == 'add_rate_limit_SP':
            fqdn, wait_seconds = args
            self.rate_limits[_sha256(fqdn)] = {
                'fqdn': fqdn,
                'noContactUntil':
                    self.now + datetime.timedelta(seconds=wait_seconds)}
        elif procname == 'forget_specific_rate_limit_SP':
            self.rate_limits.pop(_sha256(args[0]), None)
        elif procname == 'forget_all_rate_limits_SP':
            self.rate_limits.clear()
        elif procname == 'update_host_stats_SP':
            self.host_stats.append(args)
        else:
            raise ValueError(f'FakeCursor: unexpected procedure: {procname}')
        self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return tuple(self._result)

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # internals mimicking the SQL logic
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    def _sorted_queue(self) -> list:
        return sorted(self.queue.values(), key=lambda r: r['addedToQueue'])

    def _active_rate_limit_hashes(self) -> set:
        return {fqdn_hash for fqdn_hash, entry in self.rate_limits.items()
                if entry['noContactUntil'] > self.now}

    def _next_queue_object(self) -> list:
        "Same selection logic as the stored procedure next_queue_object_SP."
        blocked_hosts = self._active_rate_limit_hashes()
        for row in self._sorted_queue():
            error = row['causesError']
            if error is not None and error not in TEMPORARY_ERROR_IDS:
                continue
            if row['fqdnHash'] in blocked_hosts:
                continue
            if row['delayUntil'] is not None and row['delayUntil'] >= self.now:
                continue
            if row['action'] not in (1, 2, 3, 4):
                continue
            return [(row['id'], row['action'], row['url'],
                     row['urlHash'], row['prettifyHtml'])]
        return []

    def _num_tasks_with_active_rate_limit(self) -> int:
        "Same logic as the SQL function num_tasks_with_active_rate_limit."
        blocked_hosts = self._active_rate_limit_hashes()
        return sum(1 for r in self.queue.values()
                   if (r['causesError'] is None or
                       r['causesError'] not in PERMANENT_ERROR_IDS)
                   and r['fqdnHash'] in blocked_hosts)


class FakeDbConnection:
    "Stand-in for database_connection.DatabaseConnection."
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor = cursor
        self.db_name = 'fake'

    def get_cursor(self) -> FakeCursor:
        return self.cursor


def build_framework(max_retries: int = 3,
                    rate_limit_wait: int = 60,
                    stop_if_queue_empty: bool = True):
    """Wire a QueueManager, a StatisticsManager and a CrawlingErrorManager
       (all real objects) to a fake database."""
    cursor = FakeCursor()
    db = FakeDbConnection(cursor)
    stats = statistics_manager.StatisticsManager(db)
    errorhandling = error_manager.CrawlingErrorManager(
        db, max_retries, rate_limit_wait)
    blocklist = mock.MagicMock()
    blocklist.check_blocklist.return_value = False
    time_mgr = mock.MagicMock()
    actions_obj = mock.MagicMock()
    notify = mock.MagicMock()
    labels = mock.MagicMock()
    queue = queue_manager.QueueManager(
        db, blocklist, time_mgr, stats, actions_obj, notify, labels,
        {'stop_if_queue_empty': stop_if_queue_empty, 'queue_revisit': 10})
    return cursor, queue, stats, errorhandling


# #############################################################################
# TASK LEVEL BACKOFF (temporary errors)
# #############################################################################

def test_temporary_error_normal_retry_cycle():
    cursor, queue, stats, errorhandling = build_framework(max_retries=3)
    uuid = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/p1'), 2)
    assert uuid is not None
    # the new task is available:
    next_task = queue.get_next_task()
    assert next_task is not None and next_task[0] == uuid
    # a temporary error (like HTTP 500) adds a delay and counts the try:
    errorhandling.add_crawl_delay(uuid, 500)
    row = cursor.queue[uuid]
    assert row['numTries'] == 1
    assert row['causesError'] == 500
    first_delay = error_manager.CrawlingErrorManager.DELAY_TRIES[0]
    assert row['delayUntil'] == cursor.now + datetime.timedelta(
        seconds=first_delay)
    # while the delay is active the task is not fetched ...
    assert queue.get_next_task() is None
    # ... but it stays in the queue as a temporary error:
    assert stats.num_tasks_w_temporary_errors() == 1
    assert stats.num_tasks_w_permanent_errors() == 0
    # once the delay is over, the task is fetched again:
    cursor.advance(first_delay + 1)
    next_task = queue.get_next_task()
    assert next_task is not None and next_task[0] == uuid


def test_temporary_error_delay_increases_with_tries():
    cursor, queue, stats, errorhandling = build_framework(max_retries=5)
    uuid = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/p2'), 2)
    delay_tries = error_manager.CrawlingErrorManager.DELAY_TRIES
    for num_try in (1, 2, 3):
        errorhandling.add_crawl_delay(uuid, 500)
        row = cursor.queue[uuid]
        assert row['numTries'] == num_try
        assert row['causesError'] == 500
        assert row['delayUntil'] == cursor.now + datetime.timedelta(
            seconds=delay_tries[num_try - 1])
        # below queue_max_retries: not a permanent error
        assert stats.num_tasks_w_permanent_errors() == 0


def test_exceeding_max_retries_marks_permanent_error():
    cursor, queue, stats, errorhandling = build_framework(max_retries=3)
    uuid = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/p3'), 2)
    # the first two failures stay temporary:
    for expected_tries in (1, 2):
        errorhandling.add_crawl_delay(uuid, 500)
        row = cursor.queue[uuid]
        assert row['numTries'] == expected_tries
        assert row['causesError'] == 500
        assert row['delayUntil'] is not None
    # the third failure reaches queue_max_retries => permanent error:
    errorhandling.add_crawl_delay(uuid, 500)
    row = cursor.queue[uuid]
    assert row['numTries'] == 3
    # error type 3 = 'exceeded number of retries' (permanent)
    assert row['causesError'] == 3
    assert row['delayUntil'] is None
    # a permanent error is not fetched anymore and shows up in the stats:
    assert queue.get_next_task() is None
    assert stats.num_tasks_w_permanent_errors() == 1
    assert stats.num_tasks_w_temporary_errors() == 0
    # even if the counter overshoots the limit, the task stays permanent
    # instead of being retried forever:
    errorhandling.add_crawl_delay(uuid, 500)
    assert cursor.queue[uuid]['causesError'] == 3


def test_no_exception_for_deleted_or_unknown_tasks():
    cursor, queue, stats, errorhandling = build_framework()
    # unknown uuid (never has been in the queue):
    errorhandling.add_crawl_delay('unknown-uuid', 500)
    errorhandling.mark_permanent_error('unknown-uuid', 3)
    # task that has been deleted from the queue:
    uuid = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/gone'), 2)
    queue.delete_from_queue(uuid)
    errorhandling.add_crawl_delay(uuid, 500)
    errorhandling.mark_permanent_error(uuid, 3)


# #############################################################################
# HOST LEVEL RATE LIMIT
# #############################################################################

def test_rate_limit_blocks_all_tasks_of_same_host():
    cursor, queue, stats, errorhandling = build_framework(rate_limit_wait=60)
    uuid_1 = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/a'), 2)
    uuid_2 = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/b'), 2)
    uuid_other = queue.add_to_queue(
        exo_url.ExoUrl('https://other.example.org/c'), 2)

    errorhandling.add_rate_limit('www.example.com')

    # the backoff window is observable in the database state:
    assert errorhandling.rate_limit_active('www.example.com') is True
    assert errorhandling.get_rate_limit_window('www.example.com') == (
        cursor.now + datetime.timedelta(seconds=60))
    assert errorhandling.rate_limit_active('other.example.org') is False
    assert errorhandling.get_rate_limit_window('other.example.org') is None
    # both tasks of the rate limited host are blocked ...
    assert stats.num_tasks_w_rate_limit() == 2
    # ... and cannot be fetched, while other hosts are served:
    next_task = queue.get_next_task()
    assert next_task is not None and next_task[0] == uuid_other
    assert next_task[0] not in (uuid_1, uuid_2)


def test_tasks_fetched_again_after_rate_limit_window():
    cursor, queue, stats, errorhandling = build_framework(rate_limit_wait=60)
    uuid = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/a'), 2)
    errorhandling.add_rate_limit('www.example.com')
    # within the backoff window the task is not fetched:
    assert queue.get_next_task() is None
    assert stats.num_tasks_w_rate_limit() == 1
    # after the window the task is available again:
    cursor.advance(61)
    assert errorhandling.rate_limit_active('www.example.com') is False
    assert stats.num_tasks_w_rate_limit() == 0
    next_task = queue.get_next_task()
    assert next_task is not None and next_task[0] == uuid


def test_process_queue_waits_out_rate_limit_instead_of_stopping():
    """With stop_if_queue_empty the bot must not stop while tasks are only
       blocked by an active rate limit. It has to wait for the window to
       end and then process the task."""
    cursor, queue, stats, errorhandling = build_framework(
        rate_limit_wait=25, stop_if_queue_empty=True)
    uuid = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/x'), 2)
    errorhandling.add_rate_limit('www.example.com')

    def fake_get_object(queue_id, action_type, url, prettify_html=False):
        # simulate a successful request: the task leaves the queue
        queue.delete_from_queue(queue_id)

    queue.actions.get_object.side_effect = fake_get_object

    with mock.patch('time.sleep', side_effect=lambda s: cursor.advance(s)):
        queue.process_queue()

    # the task was processed after the backoff window ended:
    queue.actions.get_object.assert_called_once()
    assert queue.actions.get_object.call_args[0][0] == uuid
    assert uuid not in cursor.queue
    queue.notify.send_msg_finish.assert_called_once()


def test_queue_stats_make_rate_limit_observable():
    cursor, queue, stats, errorhandling = build_framework(rate_limit_wait=60)
    queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/a'), 2)
    queue.add_to_queue(exo_url.ExoUrl('https://other.example.org/b'), 2)
    errorhandling.add_rate_limit('www.example.com')
    queue_stats = stats.queue_stats()
    assert queue_stats['tasks_without_error'] == 2
    assert queue_stats['tasks_blocked_by_rate_limit'] == 1
    assert queue_stats['tasks_with_temp_errors'] == 0
    assert queue_stats['tasks_with_permanent_errors'] == 0


# #############################################################################
# REACTION TO (FAKE) HTTP RESPONSES
# #############################################################################

class _FakeResponse:
    "Minimal stand-in for requests.Response."
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers = {'content-type': 'text/html'}
        self.text = 'fake content'


class _FakeGetter(actions.GetObjectBaseClass):
    "Returns a fake response instead of doing a real HTTP request."
    def __init__(self, objects, queue_id, url, response):
        self._fake_response = response
        super().__init__(objects, queue_id, url, False)

    def handle_action(self):
        return self._fake_response

    def store_result(self, response, strip_code=False):
        pass


def _action_objects(cursor, errorhandling, stats) -> dict:
    return {
        'db_connection': FakeDbConnection(cursor),
        'stats_manager_object': stats,
        'file_manager_object': mock.MagicMock(),
        'time_manager_object': mock.MagicMock(),
        'crawling_error_manager_object': errorhandling,
        'user_agent': 'fake-bot',
        'connection_timeout': 5}


def test_429_response_rate_limits_whole_host():
    cursor, queue, stats, errorhandling = build_framework(rate_limit_wait=60)
    uuid_1 = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/a'), 2)
    uuid_2 = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/b'), 2)
    objects = _action_objects(cursor, errorhandling, stats)

    _FakeGetter(objects, uuid_1,
                exo_url.ExoUrl('https://www.example.com/a'),
                _FakeResponse(429))

    # the host is on the rate limit list:
    assert errorhandling.rate_limit_active('www.example.com')
    # the task itself is not marked as erroneous ...
    assert cursor.queue[uuid_1]['causesError'] is None
    assert cursor.queue[uuid_1]['numTries'] == 0
    # ... but no task of that host is fetched while the window is active:
    assert queue.get_next_task() is None
    assert stats.num_tasks_w_rate_limit() == 2
    # after the window both tasks are available again:
    cursor.advance(61)
    assert queue.get_next_task() is not None


def test_5xx_response_adds_crawl_delay():
    cursor, queue, stats, errorhandling = build_framework(max_retries=3)
    uuid = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/x'), 2)
    objects = _action_objects(cursor, errorhandling, stats)

    _FakeGetter(objects, uuid,
                exo_url.ExoUrl('https://www.example.com/x'),
                _FakeResponse(500))

    row = cursor.queue[uuid]
    assert row['numTries'] == 1
    assert row['causesError'] == 500
    assert row['delayUntil'] is not None
    assert stats.num_tasks_w_temporary_errors() == 1


def test_permanent_error_response_marked_immediately():
    cursor, queue, stats, errorhandling = build_framework()
    uuid = queue.add_to_queue(exo_url.ExoUrl('https://www.example.com/404'), 2)
    objects = _action_objects(cursor, errorhandling, stats)

    _FakeGetter(objects, uuid,
                exo_url.ExoUrl('https://www.example.com/404'),
                _FakeResponse(404))

    row = cursor.queue[uuid]
    assert row['causesError'] == 404
    assert row['delayUntil'] is None
    assert stats.num_tasks_w_permanent_errors() == 1


def test_error_code_sets_unchanged():
    "The classification of permanent / temporary HTTP codes must not change."
    # 429 is handled separately as a rate limit and belongs to neither set:
    assert 429 not in actions.GetObjectBaseClass.HTTP_PERMANENT_ERRORS
    assert 429 not in actions.GetObjectBaseClass.HTTP_TEMP_ERRORS
    for code in (400, 401, 402, 403, 404, 405, 406, 407, 410, 451, 501):
        assert code in actions.GetObjectBaseClass.HTTP_PERMANENT_ERRORS
    for code in (408, 500, 502, 503, 504, 509, 529, 598):
        assert code in actions.GetObjectBaseClass.HTTP_TEMP_ERRORS


# #############################################################################
# UNCHANGED BEHAVIOR: add_to_queue deduplication and labels
# #############################################################################

def test_add_to_queue_dedup_and_labels_unchanged():
    cursor, queue, stats, errorhandling = build_framework()
    url = exo_url.ExoUrl('https://www.example.com/dedup')
    uuid_1 = queue.add_to_queue(url, 2,
                                labels_master={'master_label'},
                                labels_version={'version_label'})
    assert uuid_1 is not None
    queue.labels.assign_labels_to_master.assert_called_once()
    queue.labels.assign_labels_to_uuid.assert_called_once()
    # the exact same task is not added twice:
    assert queue.add_to_queue(url, 2) is None
    assert len(cursor.queue) == 1
    # the same URL with another action is a different task:
    uuid_2 = queue.add_to_queue(url, 4)
    assert uuid_2 is not None
    # forcing a new version adds the same task again:
    uuid_3 = queue.add_to_queue(url, 2, force_new_version=True)
    assert uuid_3 is not None
    assert len(cursor.queue) == 3


# #############################################################################
# REGRESSION GUARDS FOR THE SQL SCHEMA
# #############################################################################

def test_sql_schema_rate_limit_count_handles_null():
    """num_tasks_with_active_rate_limit must count tasks whose causesError
       IS NULL (NULL NOT IN (...) would silently drop them)."""
    sql_path = (pathlib.Path(__file__).parent / 'Database-Scripts' /
                'Generate-Database-Schema-MariaDB.sql')
    sql = sql_path.read_text(encoding='utf-8')
    start = sql.index('CREATE FUNCTION num_tasks_with_active_rate_limit')
    end = sql.index('CREATE FUNCTION', start + 1)
    function_body = sql[start:end]
    assert 'causesError IS NULL' in function_body


def test_sql_schema_crawl_delay_uses_seconds():
    """add_crawl_delay_SP must interpret the wait time as seconds
       (SEC_TO_TIME), not as a HHMMSS number."""
    sql_path = (pathlib.Path(__file__).parent / 'Database-Scripts' /
                'Generate-Database-Schema-MariaDB.sql')
    sql = sql_path.read_text(encoding='utf-8')
    start = sql.index('CREATE PROCEDURE add_crawl_delay_SP')
    end = sql.index('END $$', start)
    procedure_body = sql[start:end]
    assert 'SEC_TO_TIME(wait_time_p)' in procedure_body
