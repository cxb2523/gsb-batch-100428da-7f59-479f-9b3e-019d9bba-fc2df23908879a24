import scrapy


class ResultPage(scrapy.Item):
    apartments = scrapy.Field()
    failures = scrapy.Field()
    page = scrapy.Field()
