#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Build navigation entries from already-extracted content pages.

Used as a fallback when the rendered page exposes no usable sidebar links, so
every extracted page still gets a settled outcome instead of being dropped.
"""

import re
from typing import List, Sequence
from urllib.parse import urljoin

from src.domain.models.wiki_page import ContentPage, NavigationPage


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    return _SLUG_RE.sub("-", title.casefold()).strip("-")


def navigation_from_content(
    content_pages: Sequence[ContentPage],
    base_url: str,
) -> List[NavigationPage]:
    """Derive navigation links from content page ids/titles.

    URLs mimic DeepWiki's ``/org/repo/<id>-<slug>`` shape when an explicit URL
    is not present, so page-id matching still applies.
    """
    navigation = []
    for page in content_pages:
        if not page.title:
            continue
        if page.url:
            url = page.url if "://" in page.url else urljoin(base_url, page.url)
        elif page.page_id:
            url = f"{base_url.rstrip('/')}/{page.page_id}-{slugify(page.title)}"
        else:
            continue
        navigation.append(NavigationPage(title=page.title, url=url))
    return navigation
