Stats
=====

This Scrapy plugin tracks some stats.

Stats for the `proxy mode`_ of `Zyte API`_ and stats for `Zyte Smart
Proxy Manager`_ (formerly Crawlera) have a different prefix, ``zyte_api_proxy``
and ``zyte_smartproxy`` respectively.

.. _proxy mode: https://docs.zyte.com/zyte-api/usage/proxy-mode.html
.. _Zyte API: https://docs.zyte.com/zyte-api/get-started.html
.. _Zyte Smart Proxy Manager: https://www.zyte.com/smart-proxy-manager/

Per-slot backoff stats
----------------------

Exponential backoff triggered by throttling or authentication errors is
tracked independently for each download slot, i.e. for each ``download_slot``
request metadata value (or, when it is not set, for the URL netloc of the
request). The following stats include the slot identifier in their key, so
that you can tell which site is being throttled:

``<prefix>/delay/backoff/level/<slot>``
    Current backoff level of the slot. It is ``0`` after a successful response
    resets the slot, and grows by one every time a throttling or authentication
    error raises the delay of the slot.

``<prefix>/delay/backoff/throttle/<slot>``
    Number of throttling responses (``429``/``503`` responses with a
    ``Zyte-Error-Type`` or ``X-Crawlera-Error`` header) received for the slot
    over the whole crawl.

``<prefix>`` is ``zyte_smartproxy`` or ``zyte_api_proxy``, as described above.
For example, ``zyte_smartproxy/delay/backoff/level/example.com``.

These stats do not change the meaning of the existing global stats, such as
``<prefix>/delay/reset_backoff``, which keeps counting all successful responses
regardless of the slot. Per-slot backoff stats are only added for requests
handled by the plugin (requests with ``dont_proxy`` set to ``True`` or requests
sent while the plugin is disabled are not tracked).
