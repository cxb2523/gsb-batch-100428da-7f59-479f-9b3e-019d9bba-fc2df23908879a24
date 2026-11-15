"""Replay a user_automation pending file back into the users_created topic.

Pending files are JSON Lines, one Kafka message per line, written by
``dags/kafka_stream.py`` when Kafka was unavailable. This utility reads such a
file and re-sends every line verbatim to the same topic without altering the
message field structure.

Examples:
    python script/replay_pending.py /opt/airflow/pending/users_created_pending_<id>.jsonl
    USER_AUTOMATION_BOOTSTRAP_SERVERS=broker:29092 \
        python script/replay_pending.py ./pending/users_created_pending_<id>.jsonl

The pending file is left in place on failure and renamed to ``<file>.replayed``
only after every message has been accepted by the producer.
"""

import json
import os
import sys
import time

TOPIC = "users_created"


def replay(pending_file, bootstrap_servers, *, sleep_between=0.0):
    from kafka import KafkaProducer

    producer = KafkaProducer(
        bootstrap_servers=bootstrap_servers, max_block_ms=5000
    )
    replayed = 0
    with open(pending_file, "r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            # Validate the line is the same JSON structure before sending.
            json.loads(line)
            producer.send(TOPIC, line.encode("utf-8"))
            replayed += 1
            if sleep_between:
                time.sleep(sleep_between)

    producer.flush()
    producer.close()

    replayed_path = pending_file + ".replayed"
    os.replace(pending_file, replayed_path)
    return replayed, replayed_path


def main(argv):
    if len(argv) != 2:
        print("usage: python replay_pending.py <pending_file.jsonl>", file=sys.stderr)
        return 2

    pending_file = argv[1]
    if not os.path.isfile(pending_file):
        print("pending file not found: {0}".format(pending_file), file=sys.stderr)
        return 1

    bootstrap_servers = os.environ.get(
        "USER_AUTOMATION_BOOTSTRAP_SERVERS", "broker:29092"
    ).split(",")

    count, replayed_path = replay(pending_file, bootstrap_servers)
    print(
        "replayed {0} messages to topic {1}; spool renamed to {2}".format(
            count, TOPIC, replayed_path
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
