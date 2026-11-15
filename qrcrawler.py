#!/usr/bin/env python3

# Copyright (c) 2018 Marco Zollinger
# Licensed under MIT, the license file shall be included in all copies

from icrawler.builtin import GoogleImageCrawler, BingImageCrawler, BaiduImageCrawler
import os
import sys
import time
import traceback

MAX_IMAGES = 1000
BOOTY_DIR = 'qrbooty'
ENGINES = (
    ('google', GoogleImageCrawler),
    ('bing', BingImageCrawler),
    ('baidu', BaiduImageCrawler),
)


def engine_dir(engine):
    return os.path.join(BOOTY_DIR, engine)


def count_images(directory):
    """Count the image files an engine left in its download directory."""
    try:
        filenames = os.listdir(directory)
    except OSError:
        return 0
    return sum(1 for filename in filenames
               if os.path.isfile(os.path.join(directory, filename)))


def crawl_engine(engine, crawler_cls, keywords):
    """Run one engine, recording its outcome instead of aborting the batch."""
    print('crawling %s for images with description %s...' % (engine, keywords))
    try:
        crawler = crawler_cls(parser_threads=4, downloader_threads=8,
                              storage={'root_dir': engine_dir(engine)})
        crawler.crawl(keyword=keywords, offset=0, max_num=MAX_IMAGES)
    except (Exception, SystemExit) as exc:
        # icrawler itself calls sys.exit() on some failures, so SystemExit is
        # treated like any other engine error instead of ending the batch.
        success = False
        if isinstance(exc, SystemExit):
            reason = 'SystemExit: exit code %s' % exc.code
        else:
            reason = '%s: %s' % (type(exc).__name__, exc)
        print('%s failed: %s' % (engine, reason), file=sys.stderr)
        if not isinstance(exc, SystemExit):
            traceback.print_exc()
    else:
        success = True
        reason = ''

    downloaded = count_images(engine_dir(engine))
    if success:
        print('%s done, %d images downloaded.' % (engine, downloaded))
    else:
        print('%s failed after downloading %d images, continuing with the '
              'remaining engines.' % (engine, downloaded), file=sys.stderr)
    return {'engine': engine, 'success': success,
            'downloaded': downloaded, 'reason': reason}


def print_summary(results):
    rows = [('engine', 'success', 'downloaded', 'reason')]
    for result in results:
        rows.append((result['engine'],
                     'yes' if result['success'] else 'no',
                     str(result['downloaded']),
                     result['reason']))
    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    print('\nper-engine summary:')
    for row in rows:
        print('%-*s  %-*s  %-*s  %s' % (widths[0], row[0], widths[1], row[1],
                                        widths[2], row[2], row[3]))


def main(argv):
    if len(argv) < 2 or not argv[1].strip():
        print('usage: %s <keywords>' % os.path.basename(argv[0]), file=sys.stderr)
        return 2

    keywords = argv[1]
    print('crawling search engines for images with description %s...' % keywords)
    time.sleep(2)

    results = [crawl_engine(engine, crawler_cls, keywords)
               for engine, crawler_cls in ENGINES]

    print_summary(results)

    failed = [result['engine'] for result in results if not result['success']]
    if failed:
        print('\nqrcrawler finished with %d failed engine(s): %s'
              % (len(failed), ', '.join(failed)))
        return 1

    print('qrcrawler done.\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
