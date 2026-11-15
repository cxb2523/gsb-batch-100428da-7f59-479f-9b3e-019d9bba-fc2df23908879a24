# -*- coding: utf-8 -*-

# Scrapy settings for olx_scraper project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     http://doc.scrapy.org/en/latest/topics/settings.html
#     http://scrapy.readthedocs.org/en/latest/topics/downloader-middleware.html
#     http://scrapy.readthedocs.org/en/latest/topics/spider-middleware.html

import os

BOT_NAME = 'olx_scraper'

SPIDER_MODULES = ['olx_scraper.spiders']
NEWSPIDER_MODULE = 'olx_scraper.spiders'

# Use the project's `crawl` command so that a structurally failed crawl
# (unrecognised list page, failed list request) exits with a non-zero status
# instead of looking like an empty success.
COMMANDS_MODULE = 'olx_scraper.commands'


# Crawl responsibly by identifying yourself (and your website) on the user-agent
USER_AGENT= 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.132 Safari/537.36'

# Obey robots.txt rules
ROBOTSTXT_OBEY = False








# Configure item pipelines
# See http://scrapy.readthedocs.org/en/latest/topics/item-pipeline.html
#
# The MongoDB pipeline is enabled automatically as soon as a database and a
# collection name are configured, so a checkout without a running MongoDB does
# not break crawls.  Flip it on permanently by filling in the values below, or
# set the same names as environment variables.  A crawl that runs without the
# pipeline still succeeds and records ``pipeline_disabled: true`` in its
# outcome summary.
#--------------------MONGODB CONFIG ---------------------------------------
MONGODB_SERVER = os.environ.get("MONGODB_SERVER", "localhost")
try:
    MONGODB_PORT = int(os.environ.get("MONGODB_PORT", "27017"))
except ValueError:
    MONGODB_PORT = 27017
MONGODB_DB = os.environ.get("MONGODB_DB", "")
MONGODB_COLLECTION = os.environ.get("MONGODB_COLLECTION", "")
# Optional full URI; takes precedence over server/port when set.
MONGODB_URI = os.environ.get("MONGODB_URI", "")
#--------------------------------------------------------------------------

ITEM_PIPELINES = {}
if MONGODB_DB and MONGODB_COLLECTION:
    ITEM_PIPELINES = {
        'olx_scraper.pipelines.MongoDBPipeline': 300,
    }













# Configure maximum concurrent requests performed by Scrapy (default: 16)
#CONCURRENT_REQUESTS = 32

# Configure a delay for requests for the same website (default: 0)
# See http://scrapy.readthedocs.org/en/latest/topics/settings.html#download-delay
# See also autothrottle settings and docs
#DOWNLOAD_DELAY = 3
# The download delay setting will honor only one of:
#CONCURRENT_REQUESTS_PER_DOMAIN = 16
#CONCURRENT_REQUESTS_PER_IP = 16

# Disable cookies (enabled by default)
#COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
#TELNETCONSOLE_ENABLED = False

# Override the default request headers:
#DEFAULT_REQUEST_HEADERS = {
#   'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
#   'Accept-Language': 'en',
#}

# Enable or disable spider middlewares
# See http://scrapy.readthedocs.org/en/latest/topics/spider-middleware.html
#SPIDER_MIDDLEWARES = {
#    'olx_scraper.middlewares.OlxScraperSpiderMiddleware': 543,
#}

# Enable or disable downloader middlewares
# See http://scrapy.readthedocs.org/en/latest/topics/downloader-middleware.html
#DOWNLOADER_MIDDLEWARES = {
#    'olx_scraper.middlewares.MyCustomDownloaderMiddleware': 543,
#}

# Enable or disable extensions
# See http://scrapy.readthedocs.org/en/latest/topics/extensions.html
#EXTENSIONS = {
#    'scrapy.extensions.telnet.TelnetConsole': None,
#}



# Enable and configure the AutoThrottle extension (disabled by default)
# See http://doc.scrapy.org/en/latest/topics/autothrottle.html
#AUTOTHROTTLE_ENABLED = True
# The initial download delay
#AUTOTHROTTLE_START_DELAY = 5
# The maximum download delay to be set in case of high latencies
#AUTOTHROTTLE_MAX_DELAY = 60
# The average number of requests Scrapy should be sending in parallel to
# each remote server
#AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
#AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# See http://scrapy.readthedocs.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
#HTTPCACHE_ENABLED = True
#HTTPCACHE_EXPIRATION_SECS = 0
#HTTPCACHE_DIR = 'httpcache'
#HTTPCACHE_IGNORE_HTTP_CODES = []
#HTTPCACHE_STORAGE = 'scrapy.extensions.httpcache.FilesystemCacheStorage'
