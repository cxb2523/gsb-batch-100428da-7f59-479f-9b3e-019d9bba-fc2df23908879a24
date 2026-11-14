"""Page spider: timeline story url, then full post parse."""

import base64
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from itemloaders.processors import MapCompose
from scrapy import Request
from scrapy.http import Response
from scrapy.selector import Selector
from scrapy_playwright.page import PageMethod

from ..items import FacebookCommentItemLoader, FacebookPostItemLoader
from ..outcomes import (
    NO_POST_ID,
    PARSE_ERROR,
    SUCCESS,
    PageOutcome,
)
from .fb_base import AUTH_FILE, FbBaseSpider


class FbPageSpider(FbBaseSpider):
    """Scrape one Facebook page timeline into post items."""

    name = 'fb_page'

    def parse_page(self, response: Response, **_kwargs: Any) -> Any:
        """Build the story url from page-level ids and follow it.

        Any unexpected parsing failure becomes a ``parse_error`` outcome
        for this page instead of killing the whole run; the next page is
        still requested.
        """
        # open_in_browser(response)
        page_id = response.meta.get("page_id", "")
        page_index = response.meta.get("page_index", 0)

        if response.xpath("//form[@id='login_form']").get():
            self._expire_from(
                response, page_index, detail="login form shown on timeline response"
            )
            return

        try:
            yield from self._parse_page_timeline(response, page_id, page_index)
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:  # noqa: BLE001 - recorded as page outcome
            self._record_parse_error(response, page_id, exc)
            next_request = self.next_page_request(page_index)
            if next_request is not None:
                yield next_request

    def _parse_page_timeline(
        self, response: Response, page_id: str, page_index: int
    ) -> Any:
        page_name = response.css('meta[property="og:title"]::attr(content)').get()
        if not page_name:
            title = response.meta.get("page_name", response.xpath("//title/text()").get())
            page_name = re.sub(r"^\(\d+\)\s*", "", title or "")
        # Infinite scroll is tracked in README; the timeline yields one
        # story here.
        articles = response.css(
            'div[data-pagelet="TimelineFeedUnit_0"] div[role="article"]'
        )
        self.logger.info("timeline articles: %d", len(articles))

        # Articles carry no post links (stories, reel, page only).
        # Ids live in the UFI JSON next to the actor id.
        match = re.search(
            r'"id":"(\d+)".{0,200}?"subscription_target_id":"(\d+)"',
            response.text,
        )
        if not match:
            # The timeline rendered but carries no story id. This page
            # gets a verifiable ending instead of being silently skipped.
            self.outcome_recorder.record(
                PageOutcome(
                    page_id=page_id,
                    page_url=response.url,
                    status=NO_POST_ID,
                    detail="no story id found in timeline response",
                )
            )
            self.logger.warning("no story id on %s", response.url)
            next_request = self.next_page_request(page_index)
            if next_request is not None:
                yield next_request
            return

        actor_id, story_id = match.groups()
        post_url = (
            "https://www.facebook.com/story.php"
            f"?story_fbid={story_id}&id={actor_id}"
        )
        yield Request(
            url=post_url,
            callback=self.parse_post,
            meta={
                "page_id": page_id,
                "page_index": page_index,
                "page_name": page_name,
                "page_url": response.url,
                "post_url": post_url,
                "story_id": story_id,
                "playwright": True,
                "playwright_context_kwargs": {"storage_state": AUTH_FILE},
                "playwright_page_methods": [
                    PageMethod("wait_for_timeout", 4000),
                ],
            },
        )

    def parse_post(self, response: Response, **_kwargs: Any) -> Any:
        """Extract the post item with counts and comments."""
        # open_in_browser(response)
        page_id = response.meta.get("page_id", "")
        page_index = response.meta.get("page_index", 0)

        if response.xpath("//form[@id='login_form']").get():
            self._expire_from(
                response,
                page_index,
                detail="login form shown on story response",
            )
            return

        try:
            yield from self._parse_post_item(response, page_id, page_index)
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:  # noqa: BLE001 - recorded as page outcome
            self._record_parse_error(
                response,
                page_id,
                exc,
                page_url=response.meta.get("page_url"),
            )
            next_request = self.next_page_request(page_index)
            if next_request is not None:
                yield next_request

    def _parse_post_item(
        self, response: Response, page_id: str, page_index: int
    ) -> Any:
        post_id_match = re.search(r'"subscription_target_id":"(\d+)"', response.text)
        post_id = post_id_match.group(1) if post_id_match else ""
        post_sel = self._pick_post_article(response)
        if post_sel is None:
            self.outcome_recorder.record(
                PageOutcome(
                    page_id=page_id,
                    page_url=response.meta.get("page_url", response.url),
                    status=PARSE_ERROR,
                    detail="no post article rendered on story response",
                )
            )
            self.logger.warning("no post article on %s", response.url)
            next_request = self.next_page_request(page_index)
            if next_request is not None:
                yield next_request
            return

        loader = self._build_post_loader(response, post_sel, page_id, post_id)
        item = loader.load_item()
        yield item

        # The page succeeded: tie its outcome to the produced post so the
        # report can be cross-checked against the exported items.
        self.outcome_recorder.record(
            PageOutcome(
                page_id=page_id,
                page_url=response.meta.get("page_url", response.url),
                status=SUCCESS,
                detail=(
                    "produced 1 post item "
                    f"({post_id or response.meta.get('story_id', '')})"
                ),
                post_ids=[post_id or response.meta.get("story_id", "")],
                post_urls=[response.meta.get("post_url", response.url)],
            )
        )
        next_request = self.next_page_request(page_index)
        if next_request is not None:
            yield next_request

    def _pick_post_article(self, response: Response) -> Selector | None:
        """Select the article node that renders the requested post."""
        articles = response.css('div[role="article"]')
        top_level = [
            article for article in articles
            if not article.xpath('./ancestor::*[@role="article"]').get()
        ]
        # Match the article linking this post id. First-with-text
        # grabs whatever renders first, often an ad unit.
        pfbid = (response.meta.get("post_url") or "").split("/posts/")[-1]
        post_sel = next(
            (article for article in top_level if article.css(f'a[href*="{pfbid}"]')),
            None,
        )
        if post_sel is None:
            post_sel = next(
                (article for article in top_level
                 if article.css('div[data-ad-preview="message"]')),
                None,
            )
        if post_sel is None:
            post_sel = next(iter(top_level), None)
        return post_sel

    def _build_post_loader(
        self, response: Response, post_sel: Selector, page_id: str, post_id: str
    ) -> FacebookPostItemLoader:
        """Populate the post item loader, comments included."""
        loader = FacebookPostItemLoader(selector=post_sel)
        loader.add_value("page_id", page_id)

        title = response.xpath("//title/text()").get() or ""
        tail = title.split(" - ")[-1].replace(" | Facebook", "")
        page_name = re.sub(r"^\(\d+\)\s*", "", tail).strip()
        loader.add_value("page_name", page_name or response.meta.get("page_name"))
        loader.add_value("page_url", response.meta.get("page_url"))
        loader.add_value("post_url", response.meta.get("post_url"))
        loader.add_value(
            "post_text",
            self._join_paragraphs(
                post_sel.css('div[data-ad-preview="message"] div[dir="auto"] ::text').getall()
            ),
        )
        loader.add_value("reaction_count", self._comet_count(
            response.text,
            r'"reaction_count":\{"count":(\d+)\},"cross_universe_feedback_info',
        ))
        loader.add_value("comment_count", self._comet_count(
            response.text, r'"comments":\{"total_count":(\d+)\}',
        ))
        loader.add_value("share_count", self._comet_share_count(response.text, post_id))

        for comment in response.css('div[role="article"]'):
            if not comment.xpath('./ancestor::*[@role="article"]').get():
                continue
            comment_loader = self._load_comment(comment, response, post_id)
            if comment_loader is not None:
                loader.add_value("comments", comment_loader.load_item())
        return loader

    def _expire_from(
        self, response: Response, page_index: int, detail: str
    ) -> None:
        """Record expiry for the current and every later page, then stop."""
        remaining_ids = list(self.page_id[page_index:])
        self._record_expiry(
            remaining_ids=remaining_ids,
            expiry_page_url=response.url,
            detail=detail,
        )

    def _record_parse_error(
        self,
        response: Response,
        page_id: str,
        exc: Exception,
        page_url: str | None = None,
    ) -> None:
        self.logger.exception("parse error for page %s", page_id)
        self.outcome_recorder.record(
            PageOutcome(
                page_id=page_id,
                page_url=page_url or response.url,
                status=PARSE_ERROR,
                detail=f"{type(exc).__name__}: {exc}",
            )
        )

    def _load_comment(self, comment: Selector, response: Response, post_id: str) -> Any:
        href = comment.css('a[href*="comment_id"]::attr(href)').get()
        if not href:
            return None
        comment_id = self._comment_id(href)
        comment_loader = FacebookCommentItemLoader(selector=comment)
        comment_loader.add_value("comment_id", comment_id)
        comment_loader.add_css("comment_text", 'div[dir="auto"] ::text')
        name = comment.css('a[href*="comment_id"] ::text').get()
        comment_loader.add_value("author_name", (name or "").strip())
        comment_loader.add_value(
            "author_url",
            href,
            MapCompose(response.urljoin, lambda v: v.split("?")[0]),
        )
        comment_loader.add_value(
            "comment_reaction_count",
            self._comment_reactions(response.text, post_id, comment_id),
        )
        return comment_loader

    @staticmethod
    def _join_paragraphs(values: list) -> str:
        # First block is sometimes a template header (emoji plus
        # names), the rest are the message paragraphs.
        texts = [v.strip() for v in values if v and v.strip()]
        if len(texts) > 1 and len(texts[0]) < 40:
            texts = texts[1:]
        return "\n".join(texts)

    @staticmethod
    def _comment_id(href: str) -> str:
        raw = dict(parse_qsl(urlsplit(href).query)).get("comment_id", "")
        if "_" in raw:
            return raw.split("_")[-1]
        try:
            decoded = base64.b64decode(raw + "==").decode()
            return decoded.split("_")[-1]
        except (ValueError, UnicodeDecodeError):
            return raw

    @staticmethod
    def _comet_count(text: str, pattern: str) -> int:
        match = re.search(pattern, text)
        return int(match.group(1)) if match else 0

    def _comet_share_count(self, text: str, post_id: str) -> int:
        if post_id:
            key = base64.b64encode(f"feedback:{post_id}".encode()).decode()
            match = re.search(
                r'"id":"' + re.escape(key) + r'".{0,400}?"share_count":\{"count":(\d+)\}',
                text,
            )
            if match:
                return int(match.group(1))
        return self._comet_count(text, r'"share_count":\{"count":(\d+)\}')

    @staticmethod
    def _comment_reactions(text: str, post_id: str, comment_id: str) -> int:
        # Relay ids comments as base64 of feedback:{post}_{comment}.
        if post_id and comment_id:
            key = base64.b64encode(f"feedback:{post_id}_{comment_id}".encode()).decode()
            match = re.search(
                r'"reaction_count":(\d+),"id":"' + re.escape(key) + '"', text
            )
            if match:
                return int(match.group(1))
        return 0
