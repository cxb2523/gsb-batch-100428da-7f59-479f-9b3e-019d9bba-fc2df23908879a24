import json
from urllib.parse import urlencode

import scrapy

from ..items import ShortvideocrawlItem
from ..pagination import (
    CURSOR_HAS_MORE,
    MISSING,
    decide_next_page,
    log_pagination_stop,
)

SEARCH_API = "https://quanmin.baidu.com/wise/growth/api/home/searchmorelist"


class QuanminSpider(scrapy.Spider):
    name = "quanmin"
    allowed_domains = ["quanmin.baidu.com", "bdstatic.com"]

    query = "蔡徐坤"
    count = 20

    def start_requests(self):
        yield self.request(0)

    def request(self, page: int):
        query = {
            "rn": 12,
            "pn": page,
            "q": self.query,
            "type": "search",
            "_format": "json",
        }

        return scrapy.Request(
            SEARCH_API + "?" + urlencode(query),
            meta={"page": page},
        )

    def parse(self, response):
        current_page = response.meta.get("page", 0)
        listing = self.search_data(response, current_page)
        if listing is None:
            # The payload itself was unusable, so nothing on this page could
            # be parsed and no pagination marker is available.
            log_pagination_stop(self.logger, self.name, "empty_page", current_page)
            return

        entries_found = False
        videos = listing.get("video_list")
        if isinstance(videos, list):
            for video in videos:
                item = self.parse_video(video)
                if item is not None:
                    entries_found = True
                    yield item

        raw_cursor = listing.get("has_more", MISSING)
        # not enough, theoretically 10 per page
        decision = decide_next_page(
            raw_cursor,
            current_page=current_page,
            entries_found=entries_found,
            cursor_kind=CURSOR_HAS_MORE,
            count_allows_next=lambda next_page: next_page * 10 < int(self.count),
        )
        if decision.should_continue:
            yield self.request(decision.next_page)
        else:
            log_pagination_stop(self.logger, self.name, decision.reason, current_page)

    def search_data(self, response, current_page):
        """Return the ``data.list`` payload, or None if unusable."""
        try:
            resp = json.loads(response.body)
        except (TypeError, ValueError):
            self.logger.warning(
                "search response is not valid JSON: site=%s page=%s",
                self.name,
                current_page,
            )
            return None
        data = resp.get("data") if isinstance(resp, dict) else None
        listing = data.get("list") if isinstance(data, dict) else None
        if not isinstance(listing, dict):
            self.logger.warning(
                "search response has no data.list payload: site=%s page=%s",
                self.name,
                current_page,
            )
            return None
        return listing

    @staticmethod
    def parse_video(video):
        if not isinstance(video, dict):
            return None
        video_id = video.get("vid")
        play_url = video.get("play_url")
        if not isinstance(video_id, (str, int)) or isinstance(video_id, bool):
            return None
        if video_id == "":
            return None
        if not isinstance(play_url, str) or not play_url:
            return None
        return ShortvideocrawlItem(id=video_id, file_urls=[play_url])
