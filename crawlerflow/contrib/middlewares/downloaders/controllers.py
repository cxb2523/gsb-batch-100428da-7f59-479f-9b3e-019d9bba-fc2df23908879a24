from scrapy.exceptions import CloseSpider, IgnoreRequest


class IgnoreTraversalRequestsController(object):
    """
    This middleware will stop the requests when the scraper reaches the maximum traversal
    requests.

    TODO - currently max pages is considered as maximum requests, without considering
    retry requests.

    """

    def process_request(self, request, spider):
        current_request_traversal_id = request.meta.get("current_request_traversal_id")
        current_traversal_max_count = request.meta.get("current_traversal_max_count")
        if current_request_traversal_id != "init":
            current_request_traversal_count = spider.crawler.stats.get_value(
                'crawlerflow-stats/traversals/{}/requests_count'.format(current_request_traversal_id), spider=spider) or 0
            """
            This middleware runs before the stats middleware increments the counter for
            the current request, so the value read here is the number of traversal
            requests already committed. Once that reaches max_count, this request is
            the (max_count + 1)-th one and must not be downloaded.
            """
            if current_traversal_max_count is not None and \
                    current_request_traversal_count >= current_traversal_max_count:
                raise IgnoreRequest(
                    "max traversals for traversal_id: {} achieved".format(current_request_traversal_id))


class SpiderRequestsBasedStopController(object):
    """
    This middleware will stop the requests when the scraper reaches the maximum requests for a spider.

    """

    def process_request(self, request, spider):
        spider_id = spider.spider_config.get("spider_id")

        current_spider_requests_count = spider.crawler.stats.get_value(
            'crawlerflow-stats/spiders/{}/requests_count'.format(spider_id),
            spider=spider)

        current_spider_max_requests_count = spider.spider_config.get("stop_criteria", {}).get("max_requests")
        if current_spider_max_requests_count and current_spider_requests_count:
            if current_spider_requests_count > current_spider_max_requests_count:
                raise IgnoreRequest(
                    "max requests {} for spider: {} achieved."
                    "Ignoring the rest of this spider requests".format(current_spider_max_requests_count,
                                                                       spider_id))


class SpiderResponsesBasedStopController(object):
    """
    This middleware will stop the requests when the scraper reaches the maximum responses for a spider.

    """

    def process_request(self, request, spider):
        spider_id = spider.spider_config.get("spider_id")

        current_spider_responses_count = spider.crawler.stats.get_value(
            'crawlerflow-stats/spiders/{}/responses_count'.format(spider_id),
            spider=spider)

        current_spider_max_responses_count = spider.spider_config.get("stop_criteria", {}).get("max_responses")
        if current_spider_max_responses_count and current_spider_responses_count:
            if current_spider_responses_count > current_spider_max_responses_count:
                raise IgnoreRequest(
                    "max responses {} for spider: {} achieved."
                    "Ignoring the rest of this spider requests ".format(current_spider_max_responses_count,
                                                                        spider_id))
