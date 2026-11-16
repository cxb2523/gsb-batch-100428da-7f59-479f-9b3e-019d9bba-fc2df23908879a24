import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from pydantic import ValidationError
from cloud_functions.data_contracts.reddit_contract import RedditDataContract
from cloud_functions.reddit_data.fetch_reddit_data_function.main import (
    fetch_reddit_data,
)


@pytest.fixture
def sample_dates():
    return ["2024-01-01", "2024-01-02"]


@pytest.fixture
def sample_reddit_data():
    return {
        "date": "2024-01-01",
        "threads": [
            {
                "thread_id": "abc123",
                "title": "Match Thread: Arsenal vs Chelsea",
                "body": "Match discussion",
                "created_utc": int(datetime.now(timezone.utc).timestamp()),
                "score": 100,
                "upvote_ratio": 0.95,
                "num_comments": 500,
                "flair": "Match Thread",
                "author": "user1",
                "url": "https://reddit.com/r/soccer/abc123",
                "top_comments": [
                    {
                        "id": "comment1",
                        "body": "Great match!",
                        "score": 50,
                        "author": "user1",
                        "created_utc": int(datetime.now(timezone.utc).timestamp()),
                    }
                ],
            }
        ],
        "thread_count": 1,
    }


def _make_publisher():
    mock_publisher_instance = MagicMock()
    mock_future = MagicMock()
    mock_future.result.return_value = "test-publish-id"
    mock_publisher_instance.publish.return_value = mock_future
    return mock_publisher_instance


def test_fetch_reddit_data_success(sample_dates, sample_reddit_data):
    event = {}
    context = None

    with (
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.get_match_dates_from_bq"
        ) as mock_get_dates,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.check_file_exists_in_gcs"
        ) as mock_file_exists,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.fetch_reddit_threads"
        ) as mock_fetch_threads,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.save_to_gcs"
        ) as mock_save_to_gcs,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.pubsub_v1.PublisherClient"
        ) as mock_publisher,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.send_discord_notification"
        ) as mock_discord,
        patch.dict("os.environ", {"GCP_PROJECT_ID": "test-project"}),
    ):
        mock_get_dates.return_value = sample_dates
        mock_file_exists.return_value = False
        mock_fetch_threads.return_value = sample_reddit_data
        mock_save_to_gcs.return_value = None

        mock_publisher_instance = _make_publisher()
        mock_publisher.return_value = mock_publisher_instance

        result, status_code = fetch_reddit_data(event, context)

        assert status_code == 200
        assert "Process completed successfully" in result
        assert "Saved: 2" in result
        assert "Skipped (file already exists): 0" in result
        assert "Failed: 0" in result
        mock_get_dates.assert_called_once()
        assert mock_file_exists.call_count == len(sample_dates)
        assert mock_fetch_threads.call_count == len(sample_dates)
        assert mock_save_to_gcs.call_count == len(sample_dates)
        mock_publisher_instance.publish.assert_called_once()
        # Success notification body carries the three counters.
        success_body = mock_discord.call_args.args[1]
        assert "Saved: 2" in success_body
        assert "Skipped (file already exists): 0" in success_body
        assert "Failed: 0" in success_body


def test_fetch_reddit_data_no_threads(sample_dates):
    event = {}
    context = None

    with (
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.get_match_dates_from_bq"
        ) as mock_get_dates,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.check_file_exists_in_gcs"
        ) as mock_file_exists,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.fetch_reddit_threads"
        ) as mock_fetch_threads,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.pubsub_v1.PublisherClient"
        ) as mock_publisher,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.send_discord_notification"
        ),
        patch.dict("os.environ", {"GCP_PROJECT_ID": "test-project"}),
    ):
        mock_get_dates.return_value = sample_dates
        mock_file_exists.return_value = False
        mock_fetch_threads.return_value = {
            "date": "2024-01-01",
            "threads": [],
            "thread_count": 0,
        }

        mock_publisher_instance = _make_publisher()
        mock_publisher.return_value = mock_publisher_instance

        result, status_code = fetch_reddit_data(event, context)

        assert status_code == 200
        assert "Process completed successfully" in result
        assert "Saved: 0" in result
        assert "Failed: 0" in result
        mock_get_dates.assert_called_once()
        assert mock_fetch_threads.call_count == len(sample_dates)
        mock_publisher_instance.publish.assert_not_called()


def test_fetch_reddit_data_exception():
    event = {}
    context = None

    with (
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.get_match_dates_from_bq"
        ) as mock_get_dates,
    ):
        mock_get_dates.side_effect = Exception("Test exception")

        result, status_code = fetch_reddit_data(event, context)

        assert status_code == 500
        assert "An error occurred" in result
        mock_get_dates.assert_called_once()


def test_fetch_reddit_data_one_saved_one_fetch_error(
    sample_dates, sample_reddit_data
):
    """One date saves successfully, another raises while fetching: the run still
    returns 200, records one saved date and a failure with date and reason, and
    only triggers one upload plus one downstream message."""
    event = {}
    context = None

    with (
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.get_match_dates_from_bq"
        ) as mock_get_dates,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.check_file_exists_in_gcs"
        ) as mock_file_exists,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.fetch_reddit_threads"
        ) as mock_fetch_threads,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.save_to_gcs"
        ) as mock_save_to_gcs,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.pubsub_v1.PublisherClient"
        ) as mock_publisher,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.send_discord_notification"
        ) as mock_discord,
        patch.dict("os.environ", {"GCP_PROJECT_ID": "test-project"}),
    ):
        mock_get_dates.return_value = sample_dates
        mock_file_exists.return_value = False

        def fetch_side_effect(date):
            if date == "2024-01-02":
                raise RuntimeError("Reddit API down")
            return sample_reddit_data

        mock_fetch_threads.side_effect = fetch_side_effect
        mock_publisher_instance = _make_publisher()
        mock_publisher.return_value = mock_publisher_instance

        result, status_code = fetch_reddit_data(event, context)

        assert status_code == 200
        assert "Process completed successfully" in result
        assert "Saved: 1" in result
        assert "Skipped (file already exists): 0" in result
        assert "Failed: 1" in result
        # Failure detail carries the failed date and a short reason.
        assert "2024-01-02" in result
        assert "Fetch error" in result
        assert "Reddit API down" in result
        # Dates are still processed in BigQuery order.
        assert [c.args[0] for c in mock_fetch_threads.call_args_list] == sample_dates
        # Only the healthy date is uploaded.
        assert mock_save_to_gcs.call_count == 1
        assert mock_save_to_gcs.call_args.args[1] == "2024-01-01"
        # One saved date means the downstream message is still published.
        mock_publisher_instance.publish.assert_called_once()
        success_body = mock_discord.call_args.args[1]
        assert "Saved: 1" in success_body
        assert "2024-01-02" in success_body
        assert "Reddit API down" in success_body


def test_fetch_reddit_data_one_skipped_one_validation_failure(
    sample_dates, sample_reddit_data
):
    """One date is skipped because its GCS file exists; the other fails Pydantic
    validation, so it is counted as failed and never uploaded."""
    event = {}
    context = None

    try:
        RedditDataContract.model_validate(
            {"date": "2024-01-02", "threads": "not-a-list", "thread_count": 1}
        )
    except ValidationError as caught_error:
        validation_error = caught_error
    else:  # pragma: no cover - guard against a contract that unexpectedly passes
        pytest.fail("Expected fixture data to fail the Reddit contract")

    with (
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.get_match_dates_from_bq"
        ) as mock_get_dates,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.check_file_exists_in_gcs"
        ) as mock_file_exists,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.fetch_reddit_threads"
        ) as mock_fetch_threads,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.validate_single"
        ) as mock_validate,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.save_to_gcs"
        ) as mock_save_to_gcs,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.pubsub_v1.PublisherClient"
        ) as mock_publisher,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.send_discord_notification"
        ) as mock_discord,
        patch.dict("os.environ", {"GCP_PROJECT_ID": "test-project"}),
    ):
        mock_get_dates.return_value = sample_dates
        mock_file_exists.side_effect = lambda date: date == "2024-01-01"
        mock_fetch_threads.return_value = sample_reddit_data
        mock_validate.side_effect = validation_error
        mock_publisher_instance = _make_publisher()
        mock_publisher.return_value = mock_publisher_instance

        result, status_code = fetch_reddit_data(event, context)

        assert status_code == 200
        assert "Saved: 0" in result
        assert "Skipped (file already exists): 1" in result
        assert "Failed: 1" in result
        assert "2024-01-02" in result
        assert "Pydantic validation failed" in result
        # The skipped date is never fetched; only the non-skipped date is.
        mock_fetch_threads.assert_called_once_with("2024-01-02")
        # Validation failures must not be uploaded.
        mock_save_to_gcs.assert_not_called()
        # Nothing saved means no downstream trigger.
        mock_publisher_instance.publish.assert_not_called()
        # The readable summary is still sent as a notification.
        mock_discord.assert_called_once()
        assert "Skipped (file already exists): 1" in mock_discord.call_args.args[1]


def test_fetch_reddit_data_all_dates_already_exist(sample_dates):
    """When every date already has a GCS file: zero saved, every date skipped,
    no fetching, no uploads, no downstream message."""
    event = {}
    context = None

    with (
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.get_match_dates_from_bq"
        ) as mock_get_dates,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.check_file_exists_in_gcs"
        ) as mock_file_exists,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.fetch_reddit_threads"
        ) as mock_fetch_threads,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.save_to_gcs"
        ) as mock_save_to_gcs,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.pubsub_v1.PublisherClient"
        ) as mock_publisher,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.send_discord_notification"
        ) as mock_discord,
        patch.dict("os.environ", {"GCP_PROJECT_ID": "test-project"}),
    ):
        mock_get_dates.return_value = sample_dates
        mock_file_exists.return_value = True
        mock_publisher_instance = _make_publisher()
        mock_publisher.return_value = mock_publisher_instance

        result, status_code = fetch_reddit_data(event, context)

        assert status_code == 200
        assert "Process completed successfully" in result
        assert "Saved: 0" in result
        assert f"Skipped (file already exists): {len(sample_dates)}" in result
        assert "Failed: 0" in result
        assert mock_file_exists.call_count == len(sample_dates)
        mock_fetch_threads.assert_not_called()
        mock_save_to_gcs.assert_not_called()
        mock_publisher_instance.publish.assert_not_called()
        mock_discord.assert_called_once()
        assert "No New Threads" in mock_discord.call_args.args[0]


def test_fetch_reddit_data_gcs_save_failure(sample_dates, sample_reddit_data):
    """A failure while writing to GCS is recorded as a failed date with a
    distinct reason and does not count as saved or trigger a message."""
    event = {}
    context = None

    with (
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.get_match_dates_from_bq"
        ) as mock_get_dates,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.check_file_exists_in_gcs"
        ) as mock_file_exists,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.fetch_reddit_threads"
        ) as mock_fetch_threads,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.save_to_gcs"
        ) as mock_save_to_gcs,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.pubsub_v1.PublisherClient"
        ) as mock_publisher,
        patch(
            "cloud_functions.reddit_data.fetch_reddit_data_function.main.send_discord_notification"
        ),
        patch.dict("os.environ", {"GCP_PROJECT_ID": "test-project"}),
    ):
        mock_get_dates.return_value = ["2024-01-01"]
        mock_file_exists.return_value = False
        mock_fetch_threads.return_value = sample_reddit_data
        mock_save_to_gcs.side_effect = Exception("bucket unavailable")
        mock_publisher_instance = _make_publisher()
        mock_publisher.return_value = mock_publisher_instance

        result, status_code = fetch_reddit_data(event, context)

        assert status_code == 200
        assert "Saved: 0" in result
        assert "Failed: 1" in result
        assert "2024-01-01" in result
        assert "GCS save failed" in result
        assert "bucket unavailable" in result
        mock_publisher_instance.publish.assert_not_called()
