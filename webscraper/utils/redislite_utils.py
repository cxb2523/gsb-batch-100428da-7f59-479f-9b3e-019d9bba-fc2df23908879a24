try:
    from redislite import Redis
except ImportError:  # redislite cannot be built on some platforms (e.g. Windows)
    # fall back to an in-memory redis-compatible client so the crawler and
    # its tests still run; install redislite for a persistent, file-backed db
    import fakeredis

    def Redis(dbfilename=None, decode_responses=True):  # noqa: N802 - mimic redislite API
        return fakeredis.FakeStrictRedis(decode_responses=decode_responses)

from utils.url_utils import get_filtered_links


redis_client = Redis(dbfilename="./redis.db", decode_responses=True)


def redis_cleanup(website_full_url):
    """removed invalid entries from redis caches"""
    # remove intersections
    for anchor in redis_client.sinter("new_urls", "processed_urls"):
        redis_client.srem("new_urls", anchor)
        print("Removed processed URL from redis: {}!\n".format(anchor))
    for anchor in redis_client.smembers("new_urls"):
        if len(get_filtered_links([anchor], website_full_url)) < 1:
            redis_client.srem("new_urls", anchor)


if __name__ == '__main__':
    redis_cleanup("https://www.wikipedia.org/")
    pass
