from datetime import date
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError
from django.test import TransactionTestCase
from django.utils import timezone

from crawler.helper import update_share_history_item
from crawler.models import Share, DailyHistory


def make_row(d: date, count=100):
    """Build a fake tsetmc ClosingPriceDailyList row for the given date."""
    return {
        'dEven': d.year * 10000 + d.month * 100 + d.day,
        'priceMax': 110,
        'priceMin': 90,
        'pClosing': 100,
        'priceYesterday': 95,
        'priceChange': 5,
        'priceFirst': 96,
        'qTotCap': 1000,
        'qTotTran5J': 10,
        'zTotTran': count,
    }


class FakeResponse:
    def __init__(self, rows):
        self._payload = {'closingPriceDaily': rows}

    def json(self):
        return self._payload


class UpdateShareHistoryItemTest(TransactionTestCase):
    def setUp(self):
        self.share = Share.objects.create(id=12345, ticker='ticker-1', description='a test share')

    def _dates(self):
        return set(
            DailyHistory.objects.filter(share=self.share).values_list('date', flat=True)
        )

    @patch('crawler.helper.submit_request')
    def test_first_run_inserts_all_new_dates_and_is_idempotent(self, mock_request):
        dates = [date(2024, 1, d) for d in range(1, 6)]
        mock_request.return_value = FakeResponse([make_row(d) for d in dates])

        result = update_share_history_item(self.share)

        self.assertEqual(result, {'inserted': 5, 'skipped': 0})
        self.assertEqual(self._dates(), set(dates))
        self.assertIsNotNone(self.share.last_update)

        # Second run: nothing new, no duplicate rows.
        result = update_share_history_item(self.share)

        self.assertEqual(result, {'inserted': 0, 'skipped': 5})
        self.assertEqual(DailyHistory.objects.filter(share=self.share).count(), 5)
        self.assertEqual(self._dates(), set(dates))

    @patch('crawler.helper.submit_request')
    def test_existing_date_does_not_block_later_dates_and_missing_date_is_filled(self, mock_request):
        # Day 3 is already stored; day 2 is missing upstream on the first run.
        DailyHistory.objects.create(
            share=self.share, date=date(2024, 1, 3), high=1, low=1, close=1, last=1,
            first=1, open=1, value=1, volume=1, count=1,
        )
        first_response = [
            make_row(date(2024, 1, 5)),
            make_row(date(2024, 1, 4)),
            make_row(date(2024, 1, 3)),
            make_row(date(2024, 1, 1)),
        ]
        mock_request.return_value = FakeResponse(first_response)

        result = update_share_history_item(self.share)

        # Day 3 skipped, later dates (4, 5) and the earlier date (1) inserted.
        self.assertEqual(result['inserted'], 3)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(
            self._dates(),
            {date(2024, 1, 1), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)},
        )

        # Upstream later supplies the missing day 2; it gets filled in.
        mock_request.return_value = FakeResponse([make_row(date(2024, 1, d)) for d in range(1, 6)])
        result = update_share_history_item(self.share)

        self.assertEqual(result['inserted'], 1)
        self.assertEqual(result['skipped'], 4)
        self.assertEqual(self._dates(), {date(2024, 1, d) for d in range(1, 6)})

    @patch('crawler.helper.submit_request')
    def test_zero_count_rows_are_ignored(self, mock_request):
        mock_request.return_value = FakeResponse([
            make_row(date(2024, 1, 1), count=10),
            make_row(date(2024, 1, 2), count=0),
        ])

        result = update_share_history_item(self.share)

        self.assertEqual(result, {'inserted': 1, 'skipped': 0})
        self.assertEqual(self._dates(), {date(2024, 1, 1)})

    @patch('crawler.helper.submit_request')
    def test_request_error_keeps_last_update_and_all_data_intact(self, mock_request):
        mock_request.side_effect = RuntimeError('upstream exploded')

        with self.assertRaises(RuntimeError):
            update_share_history_item(self.share)

        self.share.refresh_from_db()
        self.assertIsNone(self.share.last_update)
        self.assertEqual(DailyHistory.objects.filter(share=self.share).count(), 0)

    @patch('crawler.helper.submit_request')
    def test_write_failure_keeps_progress_up_to_last_written_date(self, mock_request):
        dates = [date(2024, 1, d) for d in range(1, 8)]
        mock_request.return_value = FakeResponse([make_row(d) for d in dates])
        real_bulk_create = DailyHistory.objects.bulk_create
        calls = {'count': 0}

        def failing_bulk_create(objs, *args, **kwargs):
            calls['count'] += 1
            if calls['count'] > 1:  # first batch of 3 commits, the remainder fails
                raise IntegrityError('simulated write failure')
            return real_bulk_create(objs, *args, **kwargs)

        with patch.object(DailyHistory.objects, 'bulk_create', autospec=True,
                          side_effect=failing_bulk_create):
            with self.assertRaises(IntegrityError):
                update_share_history_item(self.share, batch_size=3)

        self.share.refresh_from_db()
        self.assertEqual(self._dates(), {date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)})
        # last_update only advances to the last successfully written date.
        self.assertEqual(
            timezone.localtime(self.share.last_update).date(),
            date(2024, 1, 3),
        )

        # Resume run: the remaining four dates are filled in without duplicates.
        mock_request.return_value = FakeResponse([make_row(d) for d in dates])
        result = update_share_history_item(self.share, batch_size=3)

        self.assertEqual(result, {'inserted': 4, 'skipped': 3})
        self.assertEqual(self._dates(), set(dates))
        self.assertEqual(DailyHistory.objects.filter(share=self.share).count(), 7)


class UpdateShareHistoryCommandTest(TransactionTestCase):
    def setUp(self):
        self.good_share = Share.objects.create(id=1, ticker='good', description='good share')
        self.bad_share = Share.objects.create(id=2, ticker='broken', description='broken share')

    @patch('crawler.helper.submit_request')
    def test_command_continues_after_failure_and_reports_summary(self, mock_request):
        good_dates = [date(2024, 1, d) for d in range(1, 4)]

        def fake_submit_request(method, url, **kwargs):
            if f'/{self.bad_share.id}/' in url:
                raise RuntimeError('upstream exploded')
            return FakeResponse([make_row(d) for d in good_dates])

        mock_request.side_effect = fake_submit_request

        call_command('update_share_history')

        # The healthy share still got fetched and persisted.
        self.assertEqual(
            set(DailyHistory.objects.filter(share=self.good_share).values_list('date', flat=True)),
            set(good_dates),
        )
        self.good_share.refresh_from_db()
        self.assertIsNotNone(self.good_share.last_update)

        # The failed share keeps no last_update progress from this run.
        self.bad_share.refresh_from_db()
        self.assertIsNone(self.bad_share.last_update)
        self.assertEqual(DailyHistory.objects.filter(share=self.bad_share).count(), 0)

    @patch('crawler.helper.submit_request')
    def test_command_output_lists_per_share_counters_and_failure_reasons(self, mock_request):
        good_dates = [date(2024, 1, d) for d in range(1, 4)]

        def fake_submit_request(method, url, **kwargs):
            if f'/{self.bad_share.id}/' in url:
                raise RuntimeError('upstream exploded')
            return FakeResponse([make_row(d) for d in good_dates])

        mock_request.side_effect = fake_submit_request

        out = self._call_command_capture()

        self.assertIn('good (1): 3 day(s) added, 0 day(s) skipped.', out)
        self.assertIn('1 failed', out)
        self.assertIn('upstream exploded', out)
        self.assertIn('broken (2)', out)

        # Second run: idempotent, good share adds nothing.
        def fake_submit_request_again(method, url, **kwargs):
            if f'/{self.bad_share.id}/' in url:
                raise RuntimeError('upstream exploded')
            return FakeResponse([make_row(d) for d in good_dates])

        mock_request.side_effect = fake_submit_request_again
        out = self._call_command_capture()

        self.assertIn('good (1): 0 day(s) added, 3 day(s) skipped.', out)
        self.assertEqual(DailyHistory.objects.filter(share=self.good_share).count(), 3)

    def _call_command_capture(self):
        import io

        buffer = io.StringIO()
        call_command('update_share_history', stdout=buffer)
        return buffer.getvalue()
