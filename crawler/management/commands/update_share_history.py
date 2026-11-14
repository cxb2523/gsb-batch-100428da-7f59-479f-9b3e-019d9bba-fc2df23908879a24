import concurrent
import logging
from collections import Counter
from functools import partial

from django.core.management.base import BaseCommand

from crawler.helper import run_jobs, update_share_history_item, update_contract_history_item
from crawler.models import Share, DailyHistory, Contract

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Update share history'
    requires_migrations_checks = True

    def handle(self, *args, **options):
        history_count: int = DailyHistory.objects.filter(share__isnull=False).count()
        share_list: list[Share] = list(Share.objects.all())

        results, failures = self._update_share_histories(share_list)

        successful_shares = [share for share in share_list if share.id not in failures]
        if successful_shares:
            Share.objects.bulk_update(successful_shares, ['last_update'], batch_size=100)

        for share in share_list:
            result = results.get(share.id)
            if result is not None:
                self.stdout.write(
                    f"{share.ticker} ({share.id}): {result['inserted']} day(s) added, "
                    f"{result['skipped']} day(s) skipped."
                )

        self.stdout.write(
            f"Share history update finished: {len(results)} succeeded, {len(failures)} failed "
            f"out of {len(share_list)} share(s)."
        )
        if failures:
            reason_counter = Counter(reason for reason, _ in failures.values())
            for reason, count in reason_counter.most_common():
                tickers = [ticker for item_reason, ticker in failures.values() if item_reason == reason]
                self.stdout.write(f"failure ({count} share(s)): {reason} -> {', '.join(tickers)}")

        contract_list: list[Contract] = list(Contract.objects.all())
        jobs = [partial(update_contract_history_item, contract) for contract in contract_list]
        run_jobs("Update Contract History", jobs, log=True, log_exception_on_failure=False)

        new_history_count: int = DailyHistory.objects.filter(share__isnull=False).count()
        logger.info(f"Share history updated. {new_history_count - history_count} added.")

    def _update_share_histories(self, share_list: list[Share], max_workers: int = 10):
        """Run one job per share and collect per-share results without letting
        a single share's error stop the others."""
        results = {}
        failures = {}

        if not share_list:
            return results, failures

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_share = {
                pool.submit(update_share_history_item, share, last_update=False): share
                for share in share_list
            }
            for future in concurrent.futures.as_completed(future_to_share):
                share = future_to_share[future]
                try:
                    results[share.id] = future.result()
                except Exception as error:
                    reason = str(error) or error.__class__.__name__
                    failures[share.id] = (reason, f"{share.ticker} ({share.id})")
                    logger.warning(f"Update Share History: {share.ticker} ({share.id}) failed: {reason}")

        return results, failures
