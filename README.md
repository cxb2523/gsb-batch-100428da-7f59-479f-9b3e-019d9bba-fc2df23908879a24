# Realtime Data Streaming | End-to-End Data Engineering Project

## Table of Contents
- [Introduction](#introduction)
- [System Architecture](#system-architecture)
- [What You'll Learn](#what-youll-learn)
- [Technologies](#technologies)
- [Getting Started](#getting-started)
- [Watch the Video Tutorial](#watch-the-video-tutorial)

## Introduction

This project serves as a comprehensive guide to building an end-to-end data engineering pipeline. It covers each stage from data ingestion to processing and finally to storage, utilizing a robust tech stack that includes Apache Airflow, Python, Apache Kafka, Apache Zookeeper, Apache Spark, and Cassandra. Everything is containerized using Docker for ease of deployment and scalability.

## System Architecture

![System Architecture](https://github.com/airscholar/e2e-data-engineering/blob/main/Data%20engineering%20architecture.png)

The project is designed with the following components:

- **Data Source**: We use `randomuser.me` API to generate random user data for our pipeline.
- **Apache Airflow**: Responsible for orchestrating the pipeline and storing fetched data in a PostgreSQL database.
- **Apache Kafka and Zookeeper**: Used for streaming data from PostgreSQL to the processing engine.
- **Control Center and Schema Registry**: Helps in monitoring and schema management of our Kafka streams.
- **Apache Spark**: For data processing with its master and worker nodes.
- **Cassandra**: Where the processed data will be stored.

## What You'll Learn

- Setting up a data pipeline with Apache Airflow
- Real-time data streaming with Apache Kafka
- Distributed synchronization with Apache Zookeeper
- Data processing techniques with Apache Spark
- Data storage solutions with Cassandra and PostgreSQL
- Containerizing your entire data engineering setup with Docker

## Technologies

- Apache Airflow
- Python
- Apache Kafka
- Apache Zookeeper
- Apache Spark
- Cassandra
- PostgreSQL
- Docker

## Getting Started

1. Clone the repository:
    ```bash
    git clone https://github.com/airscholar/e2e-data-engineering.git
    ```

2. Navigate to the project directory:
    ```bash
    cd e2e-data-engineering
    ```

3. Run Docker Compose to spin up the services:
    ```bash
    docker-compose up
    ```

For more detailed instructions, please check out the video tutorial linked below.

## Watch the Video Tutorial

For a complete walkthrough and practical demonstration, check out our [YouTube Video Tutorial](https://www.youtube.com/watch?v=GqAcTrqKcrY).

## User automation batch semantics

The `user_automation` DAG (`dags/kafka_stream.py`) collects users from
`randomuser.me` during a fixed 60 second window per run and writes each record
to the `users_created` Kafka topic. Every run finishes with an explicit,
machine-parseable outcome instead of silently logging and continuing.

- HTTP requests always carry a timeout (default 10 s). Non-2xx responses,
  payloads without `results`, and payloads missing required fields are counted
  as **skipped** and never interrupt the loop.
- Kafka send failures are also counted as **skipped** rather than raised.
- Deduplication is per batch only: a record whose `login.username` **or**
  `email` matches an already sent record in the same run is skipped with the
  `duplicate` reason. Records from previous runs are not compared.
- If the Kafka producer cannot be created, or sends fail consecutively
  `USER_AUTOMATION_KAFKA_FAILURE_THRESHOLD` times (default 3), the run stops
  requesting early. All collected-but-not-sent records are written to a local
  pending file, one JSON message per line, using the exact Kafka message field
  structure, for manual or next-run replay. Successfully sent records never
  enter the pending file.

Each run emits a single stable, parseable summary line to the Airflow log:

```
BATCH_SUMMARY {"attempts": 12, "batch_id": "20261115T090000-a1b2c3d4", "outcome": "completed", "pending": 0, "pending_file": "", "sent": 9, "skip_duplicate": 1, "skip_http_error": 1, "skip_invalid_payload": 0, "skip_kafka_send": 1, "skip_request_error": 0, "skip_timeout": 0, "skipped": 3, "topic": "users_created", ...}
```

Pending files are written to `USER_AUTOMATION_PENDING_DIR`
(default `/opt/airflow/pending`, mounted from `./pending`) as
`users_created_pending_<batch_id>.jsonl`, and a `BATCH_PENDING` log line
records the count and path. A run that stopped early is marked
`"outcome": "aborted_kafka_unavailable"`.

To replay a spooled file once Kafka is reachable (messages are re-sent
verbatim, and the spool is renamed to `*.jsonl.replayed` only on full
success):

```bash
python script/replay_pending.py ./pending/users_created_pending_<batch_id>.jsonl
```

Tunable environment variables: `USER_AUTOMATION_WINDOW_SECONDS`,
`USER_AUTOMATION_REQUEST_TIMEOUT_SECONDS`,
`USER_AUTOMATION_KAFKA_FAILURE_THRESHOLD`, `USER_AUTOMATION_PENDING_DIR`,
`USER_AUTOMATION_BOOTSTRAP_SERVERS`, `USER_AUTOMATION_API_URL`.

### Tests

The collection logic is fully testable without Airflow, Kafka or network
access using fake HTTP responses and a fake producer:

```bash
python -m unittest discover -s tests -v
```
