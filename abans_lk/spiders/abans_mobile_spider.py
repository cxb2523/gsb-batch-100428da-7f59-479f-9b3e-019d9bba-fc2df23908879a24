from .abans_base import BaseAbansSpider


class AbansSpider(BaseAbansSpider):
    name = "abans_mobile"
    start_urls = [
        'https://buyabans.com/mobile-phones?page=1'
    ]

    base_url = 'https://buyabans.com/mobile-phones'
    max_page_selector = (
        '#columns > div.row > div.sortPagiBar > div > nav > ul > ul > '
        'li:nth-child(5) > a ::text'
    )
    product_data_selector = '.intro::text'
