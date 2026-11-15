# Product-Info-Crawler

![platform](https://img.shields.io/badge/python-2.7-blue.svg)
![platform](https://img.shields.io/badge/license-MIT%20License-blue.svg)

Product-Info-Crawler is a python web crawler developed using scrapy framework. It has four spiders for crawling the search results from olx.in, amazon.in, ebay.in and shopclues.com. The crawler extract the product names, price, image urls, product urls and source and stores them in a csv file named `results.csv`. It can be useful for comparing the price of a particular product between different e-commerce websites.

## Installing Dependencies

### Windows
1. Install Python2.7 (Download from https://www.python.org/downloads/)
2. Install pip (Follow instructions here https://pip.pypa.io/en/stable/installing/)
3. Install scrapy using `pip install scrapy`
4. Install flask using `pip install flask`

### Linux
1. Install pip using `sudo apt-get install python-pip`
3. Install scrapy using `sudo pip install scrapy`
4. Install flask using `sudo pip install flask`


## Running Crawler
1. Open command line
2. Go to root directory i.e. Product-Info-Crawler
3. Run `python run_crawler.py`
4. Enter the search keyword (a product or brand name) in command line.
5. See the crawling results in `results.csv` file

## Running Demo
1. Open Terminal
2. Go to demo directory i.e. Product-Info-Crawler/demo
3. Run `python run.py`
4. Open http://127.0.0.1:5000/ in browser window
5. Enter the search keyword and click search button
6. The products found are displayed with images, price and source info

## Per-site crawl status
Each demo search writes `demo/search_status.json` next to `results.csv`. It
records, for every site (amazon, ebay, shopclues, olx), whether the site was
scraped successfully (`success`, with the number of items), was reachable but
had nothing for the keyword (`zero_results`), or could not be scraped
(`failed`, with a short reason). The results page shows a notice for sites
that failed or returned nothing, so an empty section is never ambiguous.
The merge + manifest logic lives in `crawl_summary.py` and can also be run
standalone (`python crawl_summary.py <keyword>`) to rebuild `results.csv`
and the manifest from existing per-site CSVs in `demo/tmp/`.

 ## Thanks
Feel free to post issues if you find any problem or contact me [Aishwarya Mittal](https://www.facebook.com/aishhmittal)<br>
©[MIT License](https://github.com/aishmittal/Product-Info-Crawler/blob/master/LICENSE)
