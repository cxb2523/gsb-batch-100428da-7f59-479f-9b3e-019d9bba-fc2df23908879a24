"""user_automation DAG: bounded, accountable batch collection from randomuser.me.

Each run collects users during a fixed 60 second window and writes them to the
``users_created`` Kafka topic.  Unlike the previous implementation every run
ends with an explicit, machine-parseable outcome:

* HTTP requests carry a timeout; non-2xx responses, payloads missing
  ``results`` or payloads missing required fields are counted as *skipped*
  records and never break the loop.
* Kafka send failures are counted as *skipped* records instead of being
  raised.
* Duplicate users inside the same batch (matching ``login.username`` or
  ``email`` against already sent records) are skipped with the ``duplicate``
  reason.  Deduplication is per batch only.
* When the producer cannot be created, or sends fail consecutively
  ``failure_threshold`` times, the run stops requesting early and every
  collected-but-not-sent record is persisted to a local pending file
  (JSON Lines, same message field structure) for later replay.  Successfully
  sent records never enter the pending file.

The topic name, message field structure and DAG schedule are unchanged.
"""

import json
import logging
import os
import time
import uuid

logger = logging.getLogger("user_automation")

TOPIC = "users_created"
DEFAULT_API_URL = "https://randomuser.me/api/"
DEFAULT_BOOTSTRAP_SERVERS = ["broker:29092"]
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_WINDOW_SECONDS = 60
DEFAULT_KAFKA_FAILURE_THRESHOLD = 3
DEFAULT_PENDING_DIR = "/opt/airflow/pending"

# Stable skip reason labels (also used as summary keys).
SKIP_TIMEOUT = "timeout"
SKIP_REQUEST_ERROR = "request_error"
SKIP_HTTP_ERROR = "http_error"
SKIP_INVALID_PAYLOAD = "invalid_payload"
SKIP_DUPLICATE = "duplicate"
SKIP_KAFKA_SEND = "kafka_send"

SKIP_REASONS = (
    SKIP_TIMEOUT,
    SKIP_REQUEST_ERROR,
    SKIP_HTTP_ERROR,
    SKIP_INVALID_PAYLOAD,
    SKIP_DUPLICATE,
    SKIP_KAFKA_SEND,
)

# Fields that must exist in the raw randomuser.me payload.
REQUIRED_PATHS = (
    ("name", "first"),
    ("name", "last"),
    ("gender",),
    ("location", "street", "number"),
    ("location", "street", "name"),
    ("location", "city"),
    ("location", "state"),
    ("location", "country"),
    ("location", "postcode"),
    ("email",),
    ("login", "username"),
    ("dob", "date"),
    ("registered", "date"),
    ("phone",),
    ("picture", "medium"),
)

# Of those, the string leaves must also be present and non-empty.  Numeric
# leaves (street number, postcode) only need to exist, so 0 stays valid.
NONEMPTY_STRING_PATHS = (
    ("name", "first"),
    ("name", "last"),
    ("gender",),
    ("location", "street", "name"),
    ("location", "city"),
    ("location", "state"),
    ("location", "country"),
    ("email",),
    ("login", "username"),
    ("dob", "date"),
    ("registered", "date"),
    ("phone",),
    ("picture", "medium"),
)

MESSAGE_FIELDS = (
    "id",
    "first_name",
    "last_name",
    "gender",
    "address",
    "post_code",
    "email",
    "username",
    "dob",
    "registered_date",
    "phone",
    "picture",
)


def _walk_path(payload, path):
    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


def extract_user(payload):
    """Validate a raw randomuser.me payload.

    Returns ``(user_dict, None)`` or ``(None, reason_detail)``.  A payload
    without ``results`` or missing any required field is rejected.
    """
    if not isinstance(payload, dict):
        return None, "missing:results"
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None, "missing:results"
    user = results[0]
    if not isinstance(user, dict):
        return None, "invalid:results[0]"

    for path in REQUIRED_PATHS:
        exists, _ = _walk_path(user, path)
        if not exists:
            return None, "missing:" + ".".join(path)

    for path in NONEMPTY_STRING_PATHS:
        _, value = _walk_path(user, path)
        if not isinstance(value, str) or value == "":
            return None, "empty:" + ".".join(path)

    return user, None


def fetch_user(session, url, timeout):
    """Fetch and validate one user.

    Always returns an outcome dict; network/HTTP/payload problems never
    propagate.  ``session`` is any object exposing
    ``get(url, timeout=...)`` (a ``requests.Session`` in production).
    """
    try:
        response = session.get(url, timeout=timeout)
    except Exception as exc:  # network level failure: timeout or other error
        exc_name = type(exc).__name__
        if "Timeout" in exc_name:  # requests Timeout / socket timeout
            return {"ok": False, "reason": SKIP_TIMEOUT, "detail": exc_name}
        return {"ok": False, "reason": SKIP_REQUEST_ERROR, "detail": exc_name}

    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int) or not 200 <= status_code < 300:
        return {
            "ok": False,
            "reason": SKIP_HTTP_ERROR,
            "detail": "status={0}".format(status_code),
        }

    try:
        payload = response.json()
    except Exception as exc:
        return {
            "ok": False,
            "reason": SKIP_INVALID_PAYLOAD,
            "detail": "json_decode:" + type(exc).__name__,
        }

    user, detail = extract_user(payload)
    if user is None:
        return {"ok": False, "reason": SKIP_INVALID_PAYLOAD, "detail": detail}
    return {"ok": True, "user": user}


def format_data(res, id_factory=uuid.uuid4):
    """Build the downstream Kafka message (field structure unchanged)."""
    location = res["location"]
    data = {}
    data["id"] = str(id_factory())
    data["first_name"] = res["name"]["first"]
    data["last_name"] = res["name"]["last"]
    data["gender"] = res["gender"]
    data["address"] = "{0} {1}, {2}, {3}, {4}".format(
        location["street"]["number"],
        location["street"]["name"],
        location["city"],
        location["state"],
        location["country"],
    )
    data["post_code"] = location["postcode"]
    data["email"] = res["email"]
    data["username"] = res["login"]["username"]
    data["dob"] = res["dob"]["date"]
    data["registered_date"] = res["registered"]["date"]
    data["phone"] = res["phone"]
    data["picture"] = res["picture"]["medium"]
    return data


def message_identities(message):
    """Identity keys used for in-batch deduplication."""
    return {
        "username:" + str(message["username"]),
        "email:" + str(message["email"]),
    }


def default_producer_factory(bootstrap_servers):
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=bootstrap_servers, max_block_ms=5000
    )


def write_pending_file(batch_id, messages, pending_dir):
    """Persist unsent messages as JSON Lines, one raw message per line.

    Each line uses the exact same field structure as the Kafka value.
    Returns the file path, or ``None`` when there is nothing to write.
    """
    if not messages:
        return None

    os.makedirs(pending_dir, exist_ok=True)
    file_path = os.path.join(pending_dir, "users_created_pending_{0}.jsonl".format(batch_id))
    with open(file_path, "w", encoding="utf-8") as handle:
        for message in messages:
            handle.write(json.dumps(message, ensure_ascii=False))
            handle.write("\n")
    return file_path


def _new_batch_id():
    return "{0}-{1}".format(
        time.strftime("%Y%m%dT%H%M%S", time.gmtime()), uuid.uuid4().hex[:8]
    )


def run_batch(
    batch_id=None,
    window_seconds=DEFAULT_WINDOW_SECONDS,
    *,
    session=None,
    producer_factory=default_producer_factory,
    bootstrap_servers=None,
    api_url=DEFAULT_API_URL,
    request_timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
    failure_threshold=DEFAULT_KAFKA_FAILURE_THRESHOLD,
    pending_dir=DEFAULT_PENDING_DIR,
    clock=time.monotonic,
    id_factory=uuid.uuid4,
):
    """Run one bounded collection batch and return its summary dict.

    All collaborators are injectable so the whole batch is testable with
    fake HTTP responses and a fake Kafka producer.
    """
    batch_id = batch_id or _new_batch_id()
    if session is None:
        import requests

        session = requests.Session()
    bootstrap_servers = bootstrap_servers or DEFAULT_BOOTSTRAP_SERVERS

    started_at = clock()
    deadline = started_at + window_seconds

    producer = None
    consecutive_kafka_failures = 0
    aborted = False

    attempts = 0
    sent_count = 0
    skip_counts = {reason: 0 for reason in SKIP_REASONS}
    seen_identities = set()
    # Collected, formatted messages that have not been sent yet and were not
    # classified as skips. Order is preserved so replay stays in order.
    unsent = []

    while clock() < deadline:
        attempts += 1
        outcome = fetch_user(session, api_url, request_timeout)

        if not outcome["ok"]:
            reason = outcome["reason"]
            skip_counts[reason] += 1
            logger.info(
                "BATCH_SKIP batch_id=%s reason=%s detail=%s",
                batch_id,
                reason,
                outcome.get("detail", ""),
            )
            continue

        message = format_data(outcome["user"], id_factory=id_factory)
        identities = message_identities(message)
        if identities & seen_identities:
            skip_counts[SKIP_DUPLICATE] += 1
            logger.info(
                "BATCH_SKIP batch_id=%s reason=%s detail=username=%s email=%s",
                batch_id,
                SKIP_DUPLICATE,
                message["username"],
                message["email"],
            )
            continue

        unsent.append(message)

        # Lazily create the producer; a creation failure counts toward the
        # same consecutive-failure threshold as send failures.
        if producer is None:
            try:
                producer = producer_factory(bootstrap_servers)
            except Exception as exc:
                consecutive_kafka_failures += 1
                logger.error(
                    "BATCH_KAFKA_ERROR batch_id=%s stage=producer_create "
                    "consecutive_failures=%s error=%s: %s",
                    batch_id,
                    consecutive_kafka_failures,
                    type(exc).__name__,
                    exc,
                )
                if consecutive_kafka_failures >= failure_threshold:
                    aborted = True
                    break
                continue

        # Drain everything collected so far (including records buffered while
        # the producer was unavailable) in original order.
        while unsent:
            buffered = unsent[0]
            try:
                producer.send(
                    TOPIC, json.dumps(buffered, ensure_ascii=False).encode("utf-8")
                )
            except Exception as exc:
                consecutive_kafka_failures += 1
                if consecutive_kafka_failures >= failure_threshold:
                    # Threshold reached: keep this record buffered, stop
                    # requesting and persist the whole unsent buffer for replay.
                    aborted = True
                    logger.error(
                        "BATCH_KAFKA_ERROR batch_id=%s stage=send "
                        "consecutive_failures=%s username=%s error=%s: %s",
                        batch_id,
                        consecutive_kafka_failures,
                        buffered.get("username"),
                        type(exc).__name__,
                        exc,
                    )
                    break
                # Below the threshold the failed record is discarded and
                # counted as a skip; the run continues.
                unsent.pop(0)
                skip_counts[SKIP_KAFKA_SEND] += 1
                logger.error(
                    "BATCH_KAFKA_ERROR batch_id=%s stage=send "
                    "consecutive_failures=%s username=%s error=%s: %s",
                    batch_id,
                    consecutive_kafka_failures,
                    buffered.get("username"),
                    type(exc).__name__,
                    exc,
                )
                break

            unsent.pop(0)
            sent_count += 1
            consecutive_kafka_failures = 0
            seen_identities.update(message_identities(buffered))
            logger.info(
                "BATCH_SENT batch_id=%s topic=%s username=%s email=%s",
                batch_id,
                TOPIC,
                buffered["username"],
                buffered["email"],
            )

        if aborted:
            break

    # Anything still buffered was collected but never sent: persist it for
    # later replay instead of dropping it. Successfully sent records are not
    # present here and therefore never enter the pending file.
    pending_messages = list(unsent)
    pending_path = None
    if pending_messages:
        try:
            pending_path = write_pending_file(batch_id, pending_messages, pending_dir)
            logger.warning(
                "BATCH_PENDING batch_id=%s count=%s file=%s",
                batch_id,
                len(pending_messages),
                pending_path,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "BATCH_PENDING_WRITE_FAILED batch_id=%s count=%s error=%s: %s",
                batch_id,
                len(pending_messages),
                type(exc).__name__,
                exc,
            )

    if producer is not None:
        for stage in ("flush", "close"):
            try:
                getattr(producer, stage)()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "BATCH_PRODUCER_CLEANUP_ERROR batch_id=%s stage=%s error=%s: %s",
                    batch_id,
                    stage,
                    type(exc).__name__,
                    exc,
                )

    skipped_count = sum(skip_counts.values())
    summary = {
        "batch_id": batch_id,
        "topic": TOPIC,
        "outcome": "aborted_kafka_unavailable" if aborted else "completed",
        "attempts": attempts,
        "sent": sent_count,
        "skipped": skipped_count,
        "pending": len(pending_messages),
        "pending_file": pending_path or "",
        "skip_" + SKIP_TIMEOUT: skip_counts[SKIP_TIMEOUT],
        "skip_" + SKIP_REQUEST_ERROR: skip_counts[SKIP_REQUEST_ERROR],
        "skip_" + SKIP_HTTP_ERROR: skip_counts[SKIP_HTTP_ERROR],
        "skip_" + SKIP_INVALID_PAYLOAD: skip_counts[SKIP_INVALID_PAYLOAD],
        "skip_" + SKIP_DUPLICATE: skip_counts[SKIP_DUPLICATE],
        "skip_" + SKIP_KAFKA_SEND: skip_counts[SKIP_KAFKA_SEND],
        "window_seconds": window_seconds,
        "duration_seconds": round(clock() - started_at, 3),
    }
    # Stable, machine-parseable single-line summary in the Airflow log.
    logger.info("BATCH_SUMMARY %s", json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return summary


def stream_data():
    """Airflow PythonOperator entry point: one 60 second collection batch."""
    summary = run_batch(
        window_seconds=int(os.environ.get("USER_AUTOMATION_WINDOW_SECONDS", DEFAULT_WINDOW_SECONDS)),
        api_url=os.environ.get("USER_AUTOMATION_API_URL", DEFAULT_API_URL),
        request_timeout=float(
            os.environ.get(
                "USER_AUTOMATION_REQUEST_TIMEOUT_SECONDS",
                DEFAULT_REQUEST_TIMEOUT_SECONDS,
            )
        ),
        failure_threshold=int(
            os.environ.get(
                "USER_AUTOMATION_KAFKA_FAILURE_THRESHOLD",
                DEFAULT_KAFKA_FAILURE_THRESHOLD,
            )
        ),
        pending_dir=os.environ.get("USER_AUTOMATION_PENDING_DIR", DEFAULT_PENDING_DIR),
        bootstrap_servers=os.environ.get(
            "USER_AUTOMATION_BOOTSTRAP_SERVERS", ",".join(DEFAULT_BOOTSTRAP_SERVERS)
        ).split(","),
    )
    return summary


try:  # Keep the module importable in environments without Airflow (e.g. tests).
    from datetime import datetime

    from airflow import DAG
    from airflow.operators.python import PythonOperator
except ImportError:  # pragma: no cover - exercised outside an Airflow image
    DAG = None
else:
    default_args = {
        "owner": "airscholar",
        "start_date": datetime(2023, 9, 3, 10, 00),
    }

    with DAG(
        "user_automation",
        default_args=default_args,
        schedule_interval="@daily",
        catchup=False,
    ) as dag:
        streaming_task = PythonOperator(
            task_id="stream_data_from_api",
            python_callable=stream_data,
        )
