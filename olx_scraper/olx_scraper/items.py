# -*- coding: utf-8 -*-

# Define here the models for your scraped items
#
# See documentation in:
# http://doc.scrapy.org/en/latest/topics/items.html

import scrapy


class OlxScraperItem(scrapy.Item):
    """A single OLX advertisement.

    ``url`` is the natural (deduplication) key: the detail page link.  The
    ``*_missing`` flags tell consumers when a value is an explicit fallback
    instead of data found on the page.
    """

    title = scrapy.Field()
    phone_number = scrapy.Field()
    date = scrapy.Field()
    price = scrapy.Field()
    # Numeric value used for filtering / sorting.  ``None`` for skipped ads;
    # accepted items always carry an int.
    price_value = scrapy.Field()
    url = scrapy.Field()
    description = scrapy.Field()
    user = scrapy.Field()
    phone_missing = scrapy.Field()
    description_missing = scrapy.Field()
    seller_missing = scrapy.Field()
    date_missing = scrapy.Field()
