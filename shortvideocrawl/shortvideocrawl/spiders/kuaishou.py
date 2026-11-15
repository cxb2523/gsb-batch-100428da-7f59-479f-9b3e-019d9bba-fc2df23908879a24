import json

import scrapy

from ..items import ShortvideocrawlItem
from ..pagination import (
    CURSOR_NUMERIC,
    MISSING,
    decide_next_page,
    log_pagination_stop,
)

headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "content-type": "application/json",
}

cookies = {
    "kpf": "PC_WEB",
    "did": "web_35b63ae980e92610232940034cb2dc66",
    "clientid": "3",
    "kpn": "KUAISHOU_VISION",
}

SEARCH_API = "https://www.kuaishou.com/graphql"


class KuaishouSpider(scrapy.Spider):
    name = "kuaishou"
    allowed_domains = ["www.kuaishou.com", "kwaicdn.com"]

    query = "蔡徐坤"
    count = 40

    def start_requests(self):
        yield self.request(0)

    def request(self, page: int):
        body = {
            "operationName": "visionSearchPhoto",
            "variables": {
                "keyword": self.query,
                "pcursor": str(page),
                "page": "search",
            },
            "query": "fragment photoContent on PhotoEntity {\n  __typename\n  id\n  duration\n  caption\n  originCaption\n  likeCount\n  viewCount\n  commentCount\n  realLikeCount\n  coverUrl\n  photoUrl\n  photoH265Url\n  manifest\n  manifestH265\n  videoResource\n  coverUrls {\n    url\n    __typename\n  }\n  timestamp\n  expTag\n  animatedCoverUrl\n  distance\n  videoRatio\n  liked\n  stereoType\n  profileUserTopPhoto\n  musicBlocked\n  riskTagContent\n  riskTagUrl\n}\n\nfragment recoPhotoFragment on recoPhotoEntity {\n  __typename\n  id\n  duration\n  caption\n  originCaption\n  likeCount\n  viewCount\n  commentCount\n  realLikeCount\n  coverUrl\n  photoUrl\n  photoH265Url\n  manifest\n  manifestH265\n  videoResource\n  coverUrls {\n    url\n    __typename\n  }\n  timestamp\n  expTag\n  animatedCoverUrl\n  distance\n  videoRatio\n  liked\n  stereoType\n  profileUserTopPhoto\n  musicBlocked\n  riskTagContent\n  riskTagUrl\n}\n\nfragment feedContent on Feed {\n  type\n  author {\n    id\n    name\n    headerUrl\n    following\n    headerUrls {\n      url\n      __typename\n    }\n    __typename\n  }\n  photo {\n    ...photoContent\n    ...recoPhotoFragment\n    __typename\n  }\n  canAddComment\n  llsid\n  status\n  currentPcursor\n  tags {\n    type\n    name\n    __typename\n  }\n  __typename\n}\n\nquery visionSearchPhoto($keyword: String, $pcursor: String, $searchSessionId: String, $page: String, $webPageArea: String) {\n  visionSearchPhoto(keyword: $keyword, pcursor: $pcursor, searchSessionId: $searchSessionId, page: $page, webPageArea: $webPageArea) {\n    result\n    llsid\n    webPageArea\n    feeds {\n      ...feedContent\n      __typename\n    }\n    searchSessionId\n    pcursor\n    aladdinBanner {\n      imgUrl\n      link\n      __typename\n    }\n    __typename\n  }\n}\n",
        }
        return scrapy.Request(
            SEARCH_API,
            method="POST",
            cookies=cookies,
            headers=headers,
            body=json.dumps(body),
            meta={"page": page},
        )

    def parse(self, response):
        current_page = response.meta.get("page", 0)
        data = self.search_data(response, current_page)
        if data is None:
            # The payload itself was unusable, so nothing on this page could
            # be parsed and no pagination marker is available.
            log_pagination_stop(self.logger, self.name, "empty_page", current_page)
            return

        entries_found = False
        feeds = data.get("feeds")
        if isinstance(feeds, list):
            for feed in feeds:
                item = self.parse_feed(feed)
                if item is not None:
                    entries_found = True
                    yield item

        raw_cursor = data.get("pcursor", MISSING)
        decision = decide_next_page(
            raw_cursor,
            current_page=current_page,
            entries_found=entries_found,
            previous_cursor=current_page,
            cursor_kind=CURSOR_NUMERIC,
            count_allows_next=lambda next_page: next_page * 20 < int(self.count),
        )
        if decision.should_continue:
            yield self.request(decision.next_page)
        else:
            log_pagination_stop(self.logger, self.name, decision.reason, current_page)

    def search_data(self, response, current_page):
        """Return the ``visionSearchPhoto`` payload, or None if unusable."""
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
        data = data.get("visionSearchPhoto") if isinstance(data, dict) else None
        if not isinstance(data, dict):
            self.logger.warning(
                "search response has no visionSearchPhoto payload: site=%s page=%s",
                self.name,
                current_page,
            )
            return None
        return data

    @staticmethod
    def parse_feed(feed):
        photo = feed.get("photo") if isinstance(feed, dict) else None
        if not isinstance(photo, dict):
            return None
        video_id = photo.get("id")
        photo_url = photo.get("photoUrl")
        if not isinstance(video_id, (str, int)) or isinstance(video_id, bool):
            return None
        if not isinstance(photo_url, str) or not photo_url:
            return None
        return ShortvideocrawlItem(id=video_id, file_urls=[photo_url])
