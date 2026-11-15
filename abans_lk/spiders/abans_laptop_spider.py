from .abans_base import BaseAbansSpider


class AbansSpider(BaseAbansSpider):
    name = "abans_laptop"
    start_urls = [
        'https://buyabans.com/computers?page=1'
    ]

    base_url = 'https://buyabans.com/computers'
    max_page_selector = (
        '#columns > div.row > div.sortPagiBar > div > nav > ul > ul > '
        'li:nth-child(6) > a ::text'
    )
    product_data_selector = 'strong::text'
