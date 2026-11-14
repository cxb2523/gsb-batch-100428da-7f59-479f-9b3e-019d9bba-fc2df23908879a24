import time
from datetime import datetime
import argparse

from .analyzer import (
    DEFAULT_DOWNLOAD_TIMEOUT,
    analyze,
    format_report,
)
from .personalized_config import fetch_new_emails_by_date,fetch_new_emails_by_id

from .phish_logger import Phish_Logger
logger=Phish_Logger.get_phish_logger('phish_logs')


help_desc = '''
Main Library to fetch, parse and crawl new reported emails
'''


def report_outcomes(outcomes):
    '''Print the per-email endings of a batch and return them.'''
    print(format_report(outcomes))
    return outcomes


def run_crawler(date=None, timeout=DEFAULT_DOWNLOAD_TIMEOUT):
    '''Fetch the inbox for ``date`` (today by default), analyze it, report.'''
    if date is None:
        date=datetime.today().strftime('%Y-%m-%d')
    inbox=fetch_new_emails_by_date(date)
    return report_outcomes(analyze(inbox, timeout=timeout))

def scheduler(interval=600) :
    '''Run one crawl round every ``interval`` seconds (600s by default).

    The cadence is anchored on the start of each round, and a failing round is
    logged instead of stopping the loop.
    '''
    while True:
        started = time.monotonic()
        try:
            run_crawler()
        except Exception as e:
            # Keep the periodic loop alive even if fetching the inbox fails.
            logger.error('[!] Scheduled crawl failed :: %s',str(e))
        remaining = interval - (time.monotonic() - started)
        time.sleep(max(0, remaining)) #wait 10minutes

def main(argv=None):
    parser= argparse.ArgumentParser(description=help_desc, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('-id', '--phish_id', default=None, help="phish_id of the reported email" )
    parser.add_argument('-d', '--date', default=None, help="date, if specified will fetch all the reported emails"  )
    parser.add_argument('--timeout', type=float, default=None,
                        help="per-email download timeout in seconds "
                             f"(default: {DEFAULT_DOWNLOAD_TIMEOUT[0]} connect / {DEFAULT_DOWNLOAD_TIMEOUT[1]} read)")

    args = parser.parse_args(argv)

    timeout = DEFAULT_DOWNLOAD_TIMEOUT if args.timeout is None else (args.timeout, args.timeout)

    if args.phish_id:
        inbox=fetch_new_emails_by_id(id=args.phish_id)
        return report_outcomes(analyze(inbox, timeout=timeout))

    elif args.date:
        inbox=fetch_new_emails_by_date(args.date)
        return report_outcomes(analyze(inbox, timeout=timeout))
    else:
        scheduler()

if __name__ == "__main__":
    main()
