

class IndividualSpiderRequestStats(object):
    """
    To calculate the spider stats
    """

    def process_request(self, request, spider):
        current_request_traversal_id = request.meta.get("current_request_traversal_id")
        if current_request_traversal_id:
            spider.crawler.stats.inc_value(
                'crawlerflow-stats/traversals/{}/requests_count'.format(current_request_traversal_id), spider=spider)

        spider_id = spider.spider_config.get("spider_id")
        spider.crawler.stats.inc_value('crawlerflow-stats/spiders/{}/requests_count'.format(spider_id), spider=spider)
        spider.crawler.stats.inc_value('downloader/request_count', spider=spider)


class IndividualSpiderResponseStats(object):
    """
    This will calculate the individual spider stats
    """

    def process_response(self, request, response, spider):
        spider_id = spider.spider_config.get("spider_id")
        spider.crawler.stats.inc_value('crawlerflow-stats/{}/responses_count'.format(spider_id), spider=spider)
        """
        Note: 'crawlerflow-stats/traversals/<id>/requests_count' is incremented
        in IndividualSpiderRequestStats only. It is the number of requests
        committed for the traversal and is used as the max_pages gate, so it
        must not be incremented again here (that would halve the effective
        pagination budget).
        """
        return response
