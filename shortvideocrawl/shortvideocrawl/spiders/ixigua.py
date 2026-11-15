import base64
import json
import re
from urllib.parse import quote, urlencode

import scrapy

from ..items import ShortvideocrawlItem
from ..pagination import (
    CURSOR_HAS_MORE,
    MISSING,
    decide_next_page,
    log_pagination_stop,
)

SEARCH_API = "https://www.ixigua.com/api/searchv2/complex/"
headers = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "referer": "https://www.ixigua.com/",
}
cookies = {
    "csrf_session_id": "812a63a05b0adb796737198645a7db48",
    "support_webp": "true",
    "support_avif": "true",
    "msToken": "Ty1J7te-X2i7QNFCtXSru4Ynpvmh7B6j86CVTFcV3TxacHWwyTfweP_UvK54EuLEx3v7O_NDZPvgkDOqHZmMwXJTKEL3DC5-P5Fyqg8=",
    "fpk1": "U2FsdGVkX1+kemf8xYpe3wksHjGni1kMAfW8OpsdXThDsx8SaJ2kwcHajsP6Gpnfb9P8kkxnuIdyfipJez8j8w==",
    "fpk2": "d72690806e05ab108412ee33b4c5c3e1",
    "_tea_utm_cache_2285": "undefined",
    "ixigua-a-s": "1",
    "tt_scid": "LSErAHS5pGlnI2UJwICxaT4WdMNw36koPGT19HAcl7kBgJR378u6-Ggkhq.W7F7zc389",
    "ttwid": "1%7CLr-_X8mdYVnrdMcniUQYfLP0oRZwKi3caPq8oLe1wlg%7C1717690469%7C12dec422a6ddd831b29b75e8886f79caa4d1ca13a741c2557b10de86fad89446",
    "msToken": "uAbhlpb9Iw4kbIb3SF_Gcwvh9RcpOKJp4VUOy1x9k2q56nU_2kOaVWP6jbP-bWmBj_hcNvgbUB-ORByLnoxKS_M5Q-Cn0FUly-BpbJPmChvTGxeSercQKbn8Um6i3SA=",
}


class IxiguaSpider(scrapy.Spider):
    name = "ixigua"
    allowed_domains = ["www.ixigua.com"]

    query = "蔡徐坤"
    count = 20

    def start_requests(self):
        yield self.search_request(0)

    def search_request(self, page: int):
        offset = page * 10
        params = {
            "min_duration": 1,
            "max_duration": 300,
        }
        return scrapy.Request(
            SEARCH_API
            + quote(self.query)
            + "/"
            + str(offset)
            + "?"
            + urlencode(params),
            headers=headers,
            cookies=cookies,
            meta={"page": page},
            callback=self.parse_search,
        )

    def detail_request(self, vid):
        return scrapy.Request(
            f"https://www.ixigua.com/{vid}",
            cookies=cookies,
            callback=self.parse_detail,
        )

    def parse_search(self, response):
        current_page = response.meta.get("page", 0)
        data = self.search_data(response, current_page)
        if data is None:
            # The payload itself was unusable, so nothing on this page could
            # be parsed and no pagination marker is available.
            log_pagination_stop(self.logger, self.name, "empty_page", current_page)
            return

        entries_found = False
        videos = data.get("data")
        if isinstance(videos, list):
            for video in videos:
                vid = self.parse_group_id(video)
                if vid is None:
                    continue
                entries_found = True
                yield self.detail_request(vid)

        raw_cursor = data.get("has_more", MISSING)
        # not enough, theoretically 10 per page
        decision = decide_next_page(
            raw_cursor,
            current_page=current_page,
            entries_found=entries_found,
            cursor_kind=CURSOR_HAS_MORE,
            count_allows_next=lambda next_page: next_page * 10 < int(self.count),
        )
        if decision.should_continue:
            yield self.search_request(decision.next_page)
        else:
            log_pagination_stop(self.logger, self.name, decision.reason, current_page)

    def search_data(self, response, current_page):
        """Return the ``data`` payload, or None if unusable."""
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
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            self.logger.warning(
                "search response has no data.data listing: site=%s page=%s",
                self.name,
                current_page,
            )
            return None
        return data

    @staticmethod
    def parse_group_id(video):
        payload = video.get("data") if isinstance(video, dict) else None
        if not isinstance(payload, dict):
            return None
        group_id = payload.get("group_id")
        if not isinstance(group_id, (str, int)) or isinstance(group_id, bool):
            return None
        if group_id == "":
            return None
        return group_id

    def parse_detail(self, response):
        # print(response.text)
        pattern = re.compile(
            r"<script id=\"SSR_HYDRATED_DATA\" nonce=.*?>window._SSR_HYDRATED_DATA=(.*?)<\/script>"
        )
        vals = pattern.findall(response.text)
        # print(vals[0])
        if len(vals) > 0:
            data = json.loads(vals[0].replace('":undefined', '":null'))
            meta = data["anyVideo"]["gidInformation"]
            url = self.get_highest_quality(
                meta["packerData"]["video"]["videoResource"]["normal"]["video_list"]
            )
            if url != "":
                yield ShortvideocrawlItem(
                    id=meta["gid"],
                    file_urls=[url],
                    _cookies=cookies,
                    _headers=headers,
                )

    @staticmethod
    def get_highest_quality(data) -> str:
        url = ""
        max_width = 0
        for _, v in data.items():
            if (
                v["vwidth"] > max_width and v["size"] < 64 * 1024 * 1024
            ):  # file size smaller than 64MB
                # url = v["main_url"]
                url = base64.b64decode(v["main_url"].encode("utf-8")).decode("utf-8")
                max_width = v["vwidth"]
        return url
