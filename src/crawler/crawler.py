import queue
from random import randint
from threading import Lock, current_thread
from time import sleep
from urllib.parse import urlsplit

import robotexclusionrulesparser as robotstxt

from .libs.internals.colours import ColouredText
from .libs.internals.crawler_exceptions import CrawlerException
from .libs.internals.custom_logger import WARN, INFO, log_says

from .libs.internals.debug import print_stack_trace
from .libs.internals.flags import VERBOSE, DEBUG
from .libs.internals.pink_thread import PinkThread
from .libs.internals.web_objects import URL

from .libs.page_grabber import PageGrabber
from .libs.page_parser import HTMLParser


class CrawlEngine(object):
    """ Crawler Class """

    # if true then use parent link as referer
    use_referer = True
    # starting url that crawling starts from
    start_url = URL(link="https://www.researchgate.net", depth=0, parent=None)
    # base domain; confine the crawler to search here
    start_domain = "researchgate.net"
    # robots.txt url
    robotstxt_url = "https://www.researchgate.net/robots.txt"
    # max number of nodes being visited by the crawler
    max_links = 1000
    # max distance that could a visiting node have
    max_depth = 3
    # how many page grab can fail before giving up?
    max_failure_threshold = 4
    # max sleep time if an error occurs while grabbing a page
    max_sleep_time = 6
    # if queue is empty how many seconds should the crawler wait before next try?
    empty_queue_wait = 8
    # maximum number of working threads
    max_worker_threads = 16
    # if now worker thread was available the how much shall the craller wait before retrying
    thread_sleep_time = 0.1
    # if number of times that work queue is empty exceeds this number then break the loop
    max_empty_queue_threshold = 8
    # should the crawler send a HEAD before gabbing a page?
    send_head_beforehand = True
    # valid http return codes
    valid_response_codes = ["200", "301", "302", "999"]

    ## private variables
    # list of visited pages
    visited_pages = set()
    # pages that could not be crawled
    missed_pages = set()
    # pages that should be ignores(not html or not-texual pages)
    garbage_pages = set()
    # links that were never expanded because their depth exceeded max_depth
    too_deep_pages = set()
    # crawler's work queue containig the pages to be visited
    work_queue = queue.Queue()
    # a lock to controll the treads
    glock = Lock()
    # page grabber object used in crawling
    pg = PageGrabber()

    def __init__(self):
        # per-instance (per-batch) state; the class attributes above act as defaults
        self.visited_pages = set()
        self.missed_pages = set()
        self.garbage_pages = set()
        self.too_deep_pages = set()
        self.work_queue = queue.Queue()
        self.glock = Lock()
        self.pg = PageGrabber()
        # worker threads spawned by the current batch (joined before start() returns)
        self._worker_threads = []
        # structured outcome of the last finished batch
        self.last_outcome = None

    def _get_robotstxt(self, robotstxt_url):
        try:
            robotsfile = robotstxt.RobotExclusionRulesParser()
            robotsfile.fetch(url=robotstxt_url.strip(), timeout=60)
        except:
            if DEBUG:
                Debugger.print_stack_trace()
            return None
        else:
            return robotsfile

    def _download(self, url, robotsfile=None):
        """This method downloads a web-page.
        """

        if not robotsfile or robotsfile.is_allowed(user_agent=self.pg.USER_AGENT[0], url=url):

            # initial settings
            hp = HTMLParser()  # html parser object

            if self.send_head_beforehand:
                try:
                    page = self.pg.get_page(url=url, verb='HEAD', use_referer=self.use_referer)
                except CrawlerException as err:

                    # did head work on first try?
                    if err.code == "100" and url.DEPTH == 0:
                        """ SWITCH FROM HEAD TO GET """
                        with self.glock:
                            self.send_head_beforehand = False

                        if VERBOSE >= 3:
                            with self.glock:
                                log_says(message=ColouredText.red("DISABLED HEAD BEFORE EACH GET"),
                                         log_type=WARN, agent="CrawlEngine.download")

                    # bad body type; don't get it
                    elif err.code in ("100", "101"):
                        """ A GARBAGE PAGE """
                        with self.glock:
                            self.garbage_pages.add(url.LINK)
                except:
                    if DEBUG:
                        print_stack_trace()
                else:
                    # some websites like instagram.com won't let us send HEAD beforhand
                    if str(page.STATUS_CODE) not in self.valid_response_codes + ["405"]:
                        """ A GARBAGE PAGE """
                        with self.glock:
                            self.garbage_pages.add(page.LINK)

                    # delete garbase web objects
                    del page

            # is it a valid link?
            if url.LINK not in self.garbage_pages:
                try:
                    # get the page's source
                    page = self.pg.get_page(url=url, verb='GET', use_referer=self.use_referer)

                except:
                    if DEBUG:
                        print_stack_trace()

                    if url.FAILURES <= self.max_failure_threshold:
                        with self.glock:
                            url.FAILURES += 1
                            self.work_queue.put_nowait(url)
                    else:
                        with self.glock:
                            self.missed_pages.add(url.LINK)

                    # don't make haste if an error occured; rest a little
                    sleep(randint(0, self.max_sleep_time))
                else:
                    if not self.send_head_beforehand and str(page.STATUS_CODE) not in self.valid_response_codes:
                        """ A GARBAGE PAGE """
                        with self.glock:
                            self.garbage_pages.add(page.LINK)
                    else:
                        # add the link to list of visited links
                        with self.glock:
                            self.visited_pages.add(url.LINK)

                        ### COULD DO SOMETHING HERE ###

                        # extract all the links inside a page
                        links = hp.extract_links(page)

                        if links:
                            for link in links:
                                # print(link.LINK)
                                pass

                        for link in links:
                            if link.LINK in self.visited_pages:
                                if VERBOSE >= 7:
                                    with self.glock:
                                        log_says(message="I FOUND A VISITED LINK", log_type=WARN,
                                                 agent=current_thread().name)

                            elif self.start_domain not in urlsplit(link.LINK).netloc.lower():
                                if VERBOSE >= 5:
                                    with self.glock:
                                        log_says(message="OUTGOING LINK", log_type=WARN, agent=current_thread().name)

                            elif link.LINK in self.garbage_pages:
                                if VERBOSE >= 5:
                                    with self.glock:
                                        log_says(message="I FOUND A GARBAGE LINK", log_type=WARN,
                                                 agent=current_thread().name)
                            else:
                                with self.glock:
                                    self.work_queue.put_nowait(
                                        URL(link=link.LINK, depth=url.DEPTH + 1, parent=url.PARENT))

                        # inspecting the the visited pages list
                        with self.glock:
                            if VERBOSE >= 3:
                                log_says(message=("PAGE VISITED: " +
                                                  ColouredText.yellow({"crawled-pages": len(self.visited_pages) - len(
                                                      self.garbage_pages),
                                                                       "page-depth": url.DEPTH,
                                                                       "page-failures": url.FAILURES,
                                                                       "grabage-pages": len(self.garbage_pages)})),
                                         log_type=INFO, agent=current_thread().name)

                        # delete garbase objects
                        del page

            with self.glock:
                self.max_worker_threads += 1

    def _finalize_outcome(self):
        """Build the structured outcome of the finished batch.

        Every link seen in this batch lands in exactly one category:
        crawled (successfully grabbed) > missed (retries exhausted) >
        garbage (non-textual) > too-deep (beyond max_depth, never expanded).
        """
        crawled = set(self.visited_pages)
        missed = set(self.missed_pages) - crawled
        garbage = set(self.garbage_pages) - crawled - missed
        too_deep = set(self.too_deep_pages) - crawled - missed - garbage

        self.last_outcome = {
            "crawled_pages": frozenset(crawled),
            "missed_pages": frozenset(missed),
            "too_deep_pages": frozenset(too_deep),
            "garbage_pages": frozenset(garbage),
        }
        return self.last_outcome

    def start(self):
        # how many times in a row the work queue may be empty before giving up;
        # kept local so the configured threshold survives each batch
        empty_queue_strikes = self.max_empty_queue_threshold
        try:
            # insert the start url to crawler's work queue
            with self.glock:
                self.work_queue.put_nowait(self.start_url)

            while len(self.visited_pages) < self.max_links and empty_queue_strikes > 0:
                try:
                    # fetch me a link to process
                    with self.glock:
                        url = self.work_queue.get_nowait()

                except queue.Empty:
                    if VERBOSE >= 3:
                        with self.glock:
                            log_says(message="WORK QUEUE IS EMPTY", log_type=WARN, agent="CrawlEngine")

                    # crawler's work queue was empty
                    empty_queue_strikes -= 1

                    # wait a little
                    sleep(self.empty_queue_wait)
                else:
                    # a link is processed only if it's not processed before and also is not too far away
                    if url.LINK in self.missed_pages:
                        if VERBOSE >= 5:
                            with self.glock:
                                log_says(message=("TOO MANY TRIES FOR GRABBING: " + \
                                                  ColouredText.red(url.LINK)), log_type=WARN, agent="CrawlEngine")

                    elif url.LINK in self.visited_pages:
                        if VERBOSE >= 5:
                            with self.glock:
                                log_says(message=("LINK HAS BEEN VISITED BEFORE: " + ColouredText.red(url.LINK)),
                                         log_type=WARN, agent="CrawlEngine")

                    elif url.DEPTH > self.max_depth:
                        # record the link instead of silently dropping it
                        with self.glock:
                            self.too_deep_pages.add(url.LINK)

                        if VERBOSE >= 5:
                            with self.glock:
                                log_says(
                                    message=(
                                        "LINK IS TOO FAR AWAY: " +
                                        ColouredText.cyan(
                                            url.LINK)),
                                    log_type=WARN,
                                    agent="CrawlEngine")
                    else:
                        while self.max_worker_threads <= 0:
                            sleep(self.thread_sleep_time)
                            if VERBOSE >= 5:
                                with self.glock:
                                    log_says(message=ColouredText.magenta("NOT ENOUGH WORKER THREADS: [" + \
                                                                          str(self.max_worker_threads) + " left]"),
                                             log_type=WARN, agent="CrawlEngine")
                        try:
                            t = PinkThread(name=url.LINK, target=self._download, args=[url])
                            t.daemon = True
                            t.start()
                        except:
                            print_stack_trace()
                        else:
                            with self.glock:
                                self.max_worker_threads -= 1
                                self._worker_threads.append(t)
        except:
            if DEBUG:
                print_stack_trace()
        finally:
            # wait for every worker of this batch so the outcome is complete
            # by the time start() returns
            for t in self._worker_threads:
                t.join()

        return self._finalize_outcome()
