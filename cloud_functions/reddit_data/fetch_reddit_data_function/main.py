import logging
import os
import json
from datetime import datetime
from pydantic import ValidationError
from google.cloud import pubsub_v1
from .utils.reddit_data_helper import (
    get_match_dates_from_bq,
    fetch_reddit_threads,
    check_file_exists_in_gcs,
    save_to_gcs,
)
from cloud_functions.discord_utils.discord_notifications import (
    send_discord_notification,
)
from cloud_functions.data_contracts.reddit_contract import RedditDataContract
from cloud_functions.data_contracts.validation import validate_single


def _build_summary(
    total_dates: int,
    saved_dates: int,
    skipped_dates: int,
    failures: list,
) -> str:
    """Build a human-readable summary of the per-date processing results."""
    lines = [
        "Process completed successfully",
        f"Dates processed: {total_dates}",
        f"Saved: {saved_dates}",
        f"Skipped (file already exists): {skipped_dates}",
        f"Failed: {len(failures)}",
    ]
    if failures:
        lines.append("Failures:")
        for failure in failures:
            lines.append(f"  - {failure['date']}: {failure['reason']}")
    return "\n".join(lines)


def fetch_reddit_data(event, context):
    """
    Cloud Function to fetch Reddit match threads from r/soccer and save to GCS bucket.

    Dates from BigQuery are processed in order. A failure on a single date is
    recorded with its reason and does not stop the remaining dates from being
    processed. Dates whose GCS file already exists are skipped without fetching
    or uploading.
    """
    try:
        dates = get_match_dates_from_bq()

        saved_dates = 0
        skipped_dates = 0
        failures = []

        for date in dates:
            # Skip dates that have already been collected: do not fetch from
            # Reddit or upload a duplicate file.
            try:
                if check_file_exists_in_gcs(date):
                    logging.info(
                        f"Skipping date {date} as file already exists in GCS"
                    )
                    skipped_dates += 1
                    continue
            except Exception as e:
                reason = f"GCS existence check failed: {str(e)}"
                logging.error(f"Error checking GCS for date {date}: {e}")
                failures.append({"date": date, "reason": reason})
                continue

            try:
                reddit_data = fetch_reddit_threads(date)
            except Exception as e:
                reason = f"Fetch error: {str(e)}"
                logging.error(f"Error fetching Reddit threads for date {date}: {e}")
                failures.append({"date": date, "reason": reason})
                continue

            # A day with no matching threads has nothing to persist; it is not a
            # saved date, a skip, or a failure.
            if not reddit_data["threads"]:
                logging.info(f"No Reddit threads found for date {date}")
                continue

            try:
                validate_single(reddit_data, RedditDataContract)
            except ValidationError as e:
                reason = f"Pydantic validation failed: {e.error_count()} issue(s)"
                logging.warning(
                    f"Reddit data validation failed for date {date}: {e}"
                )
                failures.append({"date": date, "reason": reason})
                continue
            except Exception as e:
                reason = f"Pydantic validation failed: {str(e)}"
                logging.warning(
                    f"Reddit data validation failed for date {date}: {e}"
                )
                failures.append({"date": date, "reason": reason})
                continue

            try:
                save_to_gcs(reddit_data, date)
            except Exception as e:
                reason = f"GCS save failed: {str(e)}"
                logging.error(f"Error saving Reddit data for date {date}: {e}")
                failures.append({"date": date, "reason": reason})
                continue

            saved_dates += 1

        summary = _build_summary(
            total_dates=len(dates),
            saved_dates=saved_dates,
            skipped_dates=skipped_dates,
            failures=failures,
        )
        failure_detail = (
            "\n".join(
                f"  - {failure['date']}: {failure['reason']}" for failure in failures
            )
            if failures
            else "None"
        )
        notification_body = (
            f"{summary}\nFailure details:\n{failure_detail}"
        )
        logging.info(notification_body)

        if saved_dates > 0:
            send_discord_notification(
                "✅ Fetch Reddit Data: Success", notification_body, 65280
            )

            publisher = pubsub_v1.PublisherClient()
            topic_path = publisher.topic_path(
                os.environ["GCP_PROJECT_ID"], "process_reddit_data_topic"
            )

            publish_data = {"action": "process_reddit"}
            future = publisher.publish(
                topic_path,
                data=json.dumps(publish_data).encode("utf-8"),
                timestamp=datetime.now().isoformat(),
            )

            publish_result = future.result()
            logging.info(f"Published trigger message with ID: {publish_result}")
        else:
            message = (
                "No new Reddit threads saved: all dates were skipped or failed\n"
                f"{notification_body}"
            )
            logging.info(message)
            send_discord_notification(
                "📝 Fetch Reddit Data: No New Threads", message, 16776960
            )

        return summary, 200

    except Exception as e:
        error_message = f"An error occurred: {str(e)}"
        send_discord_notification(
            "❌ Fetch Reddit Data: Failure", error_message, 16711680
        )
        logging.exception(error_message)
        return error_message, 500
