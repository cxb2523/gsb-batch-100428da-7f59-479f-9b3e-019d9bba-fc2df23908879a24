"""Acceptance tests for the user_automation batch collection.

Runnable with the standard library only (no Airflow / kafka-python / network
needed):

    python -m unittest discover -s tests -v

The DAG module imports Airflow lazily and tolerates its absence, so the
collection logic can be exercised entirely through fakes.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dags")))

import kafka_stream as ks  # noqa: E402


def make_raw_user(username="alice", email="alice@example.com", **overrides):
    user = {
        "name": {"first": "Alice", "last": "Smith"},
        "gender": "female",
        "location": {
            "street": {"number": 12, "name": "Main St"},
            "city": "Townville",
            "state": "State",
            "country": "Country",
            "postcode": 90210,
        },
        "email": email,
        "login": {"username": username},
        "dob": {"date": "1990-01-02T03:04:05.000Z"},
        "registered": {"date": "2015-06-07T08:09:10.000Z"},
        "phone": "555-1234",
        "picture": {"medium": "https://example.com/alice.jpg"},
    }
    return {"results": [user], "info": {"seed": "x"}}


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else make_raw_user()

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeTimeout(Exception):
    """Stands in for requests.exceptions.Timeout / socket.timeout."""


class FakeConnectionError(Exception):
    """Stands in for requests.exceptions.ConnectionError."""


class FakeSession:
    """Programmable session: each .get() pops the next scripted outcome."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeProducer:
    def __init__(self, send_error_on=None):
        self.messages = []
        self.send_error_on = set(send_error_on or [])
        self.flushed = False
        self.closed = False
        self._next_index = 0

    def send(self, topic, value):
        index = self._next_index
        self._next_index += 1
        if index in self.send_error_on:
            raise RuntimeError("simulated send failure #{0}".format(index))
        self.messages.append((topic, value))

    def flush(self):
        self.flushed = True

    def close(self):
        self.closed = True


class FakeClock:
    """Advances a fixed step every time it is read, so the window is finite."""

    def __init__(self, step_seconds=10.0):
        self.t = 0.0
        self.step = step_seconds

    def __call__(self):
        current = self.t
        self.t += self.step
        return current


def run(outcomes, *, tmpdir, threshold=ks.DEFAULT_KAFKA_FAILURE_THRESHOLD,
        producer_factory=None, window=60, clock_step=None, clock=None,
        request_timeout=10.0, batch_id="batch-test"):
    session = FakeSession(outcomes)
    if producer_factory is None:
        producer_factory = lambda servers: FakeProducer()
    # Size the clock so the window closes exactly after the scripted outcomes
    # would be consumed. Tests that abort early simply stop sooner.
    if clock is None:
        step = clock_step if clock_step is not None else window / len(outcomes)
        clock = FakeClock(step)
    return ks.run_batch(
        batch_id=batch_id,
        window_seconds=window,
        session=session,
        producer_factory=producer_factory,
        bootstrap_servers=["fake:9092"],
        failure_threshold=threshold,
        pending_dir=tmpdir,
        clock=clock,
        request_timeout=request_timeout,
    ), session


def expected_message_count():
    return len(ks.MESSAGE_FIELDS)


class AllSuccessTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ks_pending_")

    def test_all_success_metrics_and_topic(self):
        outcomes = [FakeResponse(payload=make_raw_user("u{0}".format(i),
                                                       "u{0}@example.com".format(i)))
                    for i in range(5)]
        summary, session = run(outcomes, tmpdir=self.tmpdir)

        self.assertEqual(summary["attempts"], 5)
        self.assertEqual(summary["sent"], 5)
        self.assertEqual(summary["skipped"], 0)
        self.assertEqual(summary["pending"], 0)
        self.assertEqual(summary["outcome"], "completed")
        self.assertEqual(summary["pending_file"], "")
        for reason in ks.SKIP_REASONS:
            self.assertEqual(summary["skip_" + reason], 0)
        # timeout was actually applied to every request
        self.assertTrue(session.calls)
        self.assertTrue(all(call_timeout == 10.0 for _, call_timeout in session.calls))
        # no pending file should exist when nothing is pending
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_sent_message_keeps_field_structure_and_topic(self):
        outcomes = [FakeResponse(payload=make_raw_user("bob", "bob@example.com"))]
        captured = {}

        def factory(servers):
            fake = FakeProducer()
            captured["producer"] = fake
            return fake

        session = FakeSession(outcomes)
        result = ks.run_batch(
            batch_id="batch-fields",
            window_seconds=60,
            session=session,
            producer_factory=factory,
            bootstrap_servers=["fake:9092"],
            pending_dir=self.tmpdir,
            clock=FakeClock(),
        )
        self.assertEqual(result["sent"], 1)
        topic, raw_value = captured["producer"].messages[0]
        self.assertEqual(topic, "users_created")
        value = json.loads(raw_value.decode("utf-8"))
        self.assertEqual(set(value.keys()), set(ks.MESSAGE_FIELDS))
        self.assertEqual(len(value), expected_message_count())
        self.assertEqual(value["username"], "bob")
        self.assertEqual(value["email"], "bob@example.com")
        self.assertIsInstance(value["id"], str)
        self.assertEqual(
            value["address"], "12 Main St, Townville, State, Country"
        )


class MixedFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ks_pending_")

    def test_timeout_500_missing_fields_and_duplicates_continue_with_reasons(self):
        valid_a = make_raw_user("carol", "carol@example.com")
        valid_b = make_raw_user("dave", "dave@example.com")
        missing_results = {"info": {}}
        missing_field = make_raw_user("erin", "erin@example.com")
        del missing_field["results"][0]["login"]["username"]

        outcomes = [
            FakeTimeout("boom"),                                   # timeout skip
            FakeResponse(status_code=500, payload={}),            # http_error skip
            FakeResponse(payload=missing_results),                # invalid_payload
            FakeResponse(payload=missing_field),                  # invalid_payload
            FakeResponse(payload=valid_a),                        # sent (1)
            FakeResponse(payload=valid_a),                        # duplicate
            FakeResponse(payload=valid_b),                        # sent (2)
        ]
        summary, _ = run(outcomes, tmpdir=self.tmpdir)

        self.assertEqual(summary["outcome"], "completed")
        self.assertEqual(summary["attempts"], 7)
        self.assertEqual(summary["sent"], 2)
        self.assertEqual(summary["skipped"], 5)
        self.assertEqual(summary["pending"], 0)
        self.assertEqual(summary["skip_timeout"], 1)
        self.assertEqual(summary["skip_http_error"], 1)
        self.assertEqual(summary["skip_invalid_payload"], 2)
        self.assertEqual(summary["skip_duplicate"], 1)
        self.assertEqual(summary["skip_kafka_send"], 0)
        self.assertEqual(summary["skip_request_error"], 0)

    def test_non_2xx_family_and_json_decode_are_skips(self):
        outcomes = [
            FakeResponse(status_code=404, payload={}),
            FakeResponse(payload=ValueError("not json")),
            FakeResponse(payload=make_raw_user("frank", "frank@example.com")),
        ]
        summary, _ = run(outcomes, tmpdir=self.tmpdir)
        self.assertEqual(summary["attempts"], 3)
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(summary["skip_http_error"], 1)
        self.assertEqual(summary["skip_invalid_payload"], 1)

    def test_duplicate_on_email_only_or_username_only(self):
        base = make_raw_user("gina", "gina@example.com")
        same_email = make_raw_user("gina2", "gina@example.com")
        same_username = make_raw_user("gina", "other@example.com")
        outcomes = [
            FakeResponse(payload=base),
            FakeResponse(payload=same_email),
            FakeResponse(payload=same_username),
        ]
        summary, _ = run(outcomes, tmpdir=self.tmpdir)
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(summary["skip_duplicate"], 2)

    def test_request_error_distinct_from_timeout(self):
        outcomes = [
            FakeConnectionError("connection refused"),
            FakeResponse(payload=make_raw_user("hank", "hank@example.com")),
        ]
        summary, _ = run(outcomes, tmpdir=self.tmpdir)
        self.assertEqual(summary["skip_request_error"], 1)
        self.assertEqual(summary["skip_timeout"], 0)
        self.assertEqual(summary["sent"], 1)

    def test_loop_continues_after_every_skip_kind(self):
        # Every response fails but the window still drives a clean finish.
        outcomes = [FakeTimeout(), FakeResponse(status_code=503),
                    FakeResponse(payload={"results": []})]
        summary, _ = run(outcomes, tmpdir=self.tmpdir)
        self.assertEqual(summary["outcome"], "completed")
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(summary["attempts"], 3)
        self.assertEqual(summary["skipped"], 3)
        self.assertEqual(summary["pending"], 0)


class KafkaProducerCreateFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ks_pending_")

    def test_producer_creation_failure_aborts_and_writes_pending(self):
        def failing_factory(servers):
            raise RuntimeError("broker unreachable")

        outcomes = [
            FakeResponse(payload=make_raw_user("ivan", "ivan@example.com")),
            FakeResponse(payload=make_raw_user("judy", "judy@example.com")),
        ]
        # Threshold 1: first producer creation error ends the run immediately,
        # so only one record is collected before the early abort.
        summary, session = run(
            outcomes, tmpdir=self.tmpdir, threshold=1,
            producer_factory=failing_factory,
        )

        self.assertEqual(summary["outcome"], "aborted_kafka_unavailable")
        self.assertEqual(summary["attempts"], 1)
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(summary["pending"], 1)
        self.assertTrue(summary["pending_file"].endswith(".jsonl"))
        # no more requests after abort
        self.assertEqual(len(session.calls), 1)

        with open(summary["pending_file"], "r", encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line]
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(set(record.keys()), set(ks.MESSAGE_FIELDS))
        self.assertEqual(record["username"], "ivan")
        self.assertEqual(record["email"], "ivan@example.com")

    def test_creation_failures_accumulate_until_threshold(self):
        calls = {"n": 0}

        def flaky_factory(servers):
            calls["n"] += 1
            raise RuntimeError("still down")

        outcomes = [
            FakeResponse(payload=make_raw_user("k{0}".format(i),
                                               "k{0}@example.com".format(i)))
            for i in range(10)
        ]
        summary, _ = run(outcomes, tmpdir=self.tmpdir, threshold=3,
                         producer_factory=flaky_factory)

        self.assertEqual(summary["outcome"], "aborted_kafka_unavailable")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(summary["attempts"], 3)
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(summary["pending"], 3)
        # pending file holds all three buffered messages in order
        with open(summary["pending_file"], "r", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual([r["username"] for r in records], ["k0", "k1", "k2"])
        # skipped stays 0: buffered-but-unsent records are pending, not skips
        self.assertEqual(summary["skipped"], 0)


class KafkaSendFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ks_pending_")

    def test_send_failure_below_threshold_is_skip_and_summary_still_emitted(self):
        def factory(servers):
            return FakeProducer(send_error_on={0})  # first send raises, rest OK

        outcomes = [
            FakeResponse(payload=make_raw_user("leo", "leo@example.com")),
            FakeResponse(payload=make_raw_user("mia", "mia@example.com")),
        ]
        summary, _ = run(outcomes, tmpdir=self.tmpdir, threshold=3,
                         producer_factory=factory)

        self.assertEqual(summary["outcome"], "completed")
        self.assertEqual(summary["attempts"], 2)
        self.assertEqual(summary["skip_kafka_send"], 1)
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(summary["pending"], 0)
        self.assertEqual(summary["pending_file"], "")
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_consecutive_send_failures_reach_threshold_and_pending_written(self):
        def factory(servers):
            return FakeProducer(send_error_on={0, 1, 2})

        outcomes = [
            FakeResponse(payload=make_raw_user("n{0}".format(i),
                                               "n{0}@example.com".format(i)))
            for i in range(6)
        ]
        summary, _ = run(outcomes, tmpdir=self.tmpdir, threshold=3,
                         producer_factory=factory)

        self.assertEqual(summary["outcome"], "aborted_kafka_unavailable")
        # First two failures are skips; the third consecutive failure trips the
        # threshold and that record is kept for replay instead of being skipped.
        self.assertEqual(summary["skip_kafka_send"], 2)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["sent"], 0)
        with open(summary["pending_file"], "r", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["username"], "n2")

    def test_successful_send_resets_consecutive_failure_counter(self):
        def factory(servers):
            return FakeProducer(send_error_on={0, 1})  # two fails, then success

        outcomes = [
            FakeResponse(payload=make_raw_user("o{0}".format(i),
                                               "o{0}@example.com".format(i)))
            for i in range(4)
        ]
        summary, _ = run(outcomes, tmpdir=self.tmpdir, threshold=3,
                         producer_factory=factory)
        self.assertEqual(summary["outcome"], "completed")
        self.assertEqual(summary["skip_kafka_send"], 2)
        self.assertEqual(summary["sent"], 2)
        self.assertEqual(summary["pending"], 0)


class PendingAndDedupScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ks_pending_")

    def test_pending_file_is_replayable_raw_messages(self):
        def failing_factory(servers):
            raise RuntimeError("down")

        outcomes = [FakeResponse(payload=make_raw_user("paul", "paul@example.com"))]
        summary, _ = run(outcomes, tmpdir=self.tmpdir, threshold=1,
                         producer_factory=failing_factory, batch_id="batch-replay")

        self.assertIn("batch-replay", summary["pending_file"])
        with open(summary["pending_file"], "r", encoding="utf-8") as handle:
            raw = handle.read().strip().splitlines()
        record = json.loads(raw[0])
        # exact same structure as a Kafka value would have had
        self.assertEqual(sorted(record.keys()), sorted(ks.MESSAGE_FIELDS))

    def test_successful_records_never_enter_pending_file(self):
        def factory(servers):
            return FakeProducer()

        outcomes = [
            FakeResponse(payload=make_raw_user("quinn", "quinn@example.com")),
        ]
        summary, _ = run(outcomes, tmpdir=self.tmpdir, producer_factory=factory)
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(summary["pending"], 0)
        self.assertEqual(os.listdir(self.tmpdir), [])

    def test_dedup_is_scoped_per_batch(self):
        shared = make_raw_user("ruth", "ruth@example.com")

        summary1, _ = run([FakeResponse(payload=shared)], tmpdir=self.tmpdir,
                          batch_id="batch-1")
        summary2, _ = run([FakeResponse(payload=shared)], tmpdir=self.tmpdir,
                          batch_id="batch-2")

        self.assertEqual(summary1["sent"], 1)
        self.assertEqual(summary2["sent"], 1)
        self.assertEqual(summary2["skip_duplicate"], 0)

    def test_pending_dir_created_when_missing(self):
        nested = os.path.join(self.tmpdir, "deeper", "pending")

        def failing_factory(servers):
            raise RuntimeError("down")

        outcomes = [FakeResponse(payload=make_raw_user("sam", "sam@example.com"))]
        summary, _ = run(outcomes, tmpdir=nested, threshold=1,
                         producer_factory=failing_factory)
        self.assertTrue(os.path.isfile(summary["pending_file"]))


class SummaryAndWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ks_pending_")

    def test_summary_log_line_is_stable_and_parseable(self):
        import logging

        outcomes = [
            FakeTimeout(),
            FakeResponse(payload=make_raw_user("tina", "tina@example.com")),
        ]
        with self.assertLogs("user_automation", level="INFO") as logs:
            summary, _ = run(outcomes, tmpdir=self.tmpdir, batch_id="batch-log")

        summary_lines = [line for line in logs.output if "BATCH_SUMMARY" in line]
        self.assertEqual(len(summary_lines), 1)
        payload = summary_lines[0].split("BATCH_SUMMARY", 1)[1].strip()
        parsed = json.loads(payload)  # stable, parseable format
        self.assertEqual(parsed["batch_id"], "batch-log")
        for key in ("batch_id", "sent", "skipped", "pending", "attempts",
                    "outcome", "skip_timeout", "skip_request_error",
                    "skip_http_error", "skip_invalid_payload",
                    "skip_duplicate", "skip_kafka_send"):
            self.assertIn(key, parsed)
        self.assertEqual(parsed["attempts"], summary["attempts"])

    def test_window_ends_loop_even_with_more_scripted_responses(self):
        # Clock steps 20s inside a 60s window -> exactly 3 iterations.
        outcomes = [
            FakeResponse(payload=make_raw_user("u{0}".format(i),
                                               "u{0}@example.com".format(i)))
            for i in range(10)
        ]
        summary, session = run(outcomes, tmpdir=self.tmpdir, window=60,
                               clock_step=20.0)
        self.assertEqual(summary["attempts"], 3)
        self.assertEqual(summary["sent"], 3)
        self.assertEqual(len(session.calls), 3)

    def test_window_seconds_recorded_in_summary(self):
        outcomes = [FakeResponse(payload=make_raw_user("vince", "vince@example.com"))]
        summary, _ = run(outcomes, tmpdir=self.tmpdir, window=60)
        self.assertEqual(summary["window_seconds"], 60)

    def test_custom_timeout_is_forwarded(self):
        outcomes = [FakeResponse(payload=make_raw_user("wendy", "wendy@example.com"))]
        summary, session = run(outcomes, tmpdir=self.tmpdir, request_timeout=3.5)
        self.assertEqual(session.calls[0][1], 3.5)

    def test_zero_postcode_and_street_number_are_valid(self):
        payload = make_raw_user("xander", "xander@example.com")
        payload["results"][0]["location"]["postcode"] = 0
        payload["results"][0]["location"]["street"]["number"] = 0
        user, detail = ks.extract_user(payload)
        self.assertIsNotNone(user)
        self.assertIsNone(detail)
        message = ks.format_data(user)
        self.assertIn("0 ", message["address"])

    def test_extract_user_rejects_missing_results_and_fields(self):
        self.assertIsNotNone(ks.extract_user({})[1])
        self.assertIsNotNone(ks.extract_user({"results": []})[1])
        broken = make_raw_user("yara", "yara@example.com")
        del broken["results"][0]["email"]
        self.assertIsNotNone(ks.extract_user(broken)[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
