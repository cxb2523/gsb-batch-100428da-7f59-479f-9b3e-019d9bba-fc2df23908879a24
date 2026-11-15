.. _response:

================
Response mapping
================

.. _response-parameters:

Parameters
==========

Zyte API response parameters are mapped into :ref:`response class
<response-classes>` attributes where possible:

-   :http:`response:url` becomes :class:`response.url
    <scrapy_zyte_api.responses.ZyteAPIResponse.url>`.

-   :http:`response:statusCode` becomes :class:`response.status
    <scrapy_zyte_api.responses.ZyteAPIResponse.status>`.

-   :http:`response:httpResponseHeaders` and
    :http:`response:experimental.responseCookies` become
    :class:`response.headers
    <scrapy_zyte_api.responses.ZyteAPIResponse.headers>`.

-   :http:`response:experimental.responseCookies` is also mapped into the
    request :reqmeta:`cookiejar <scrapy:cookiejar>`.

-   :http:`response:browserHtml` and :http:`response:httpResponseBody` are
    mapped into both
    :class:`response.text <scrapy_zyte_api.responses.ZyteAPITextResponse.text>`
    and
    :class:`response.body <scrapy_zyte_api.responses.ZyteAPIResponse.body>`.

    If none of these parameters were present, e.g. if the only requested output
    was :http:`response:screenshot`,
    :class:`response.text <scrapy_zyte_api.responses.ZyteAPITextResponse.text>`
    and
    :class:`response.body <scrapy_zyte_api.responses.ZyteAPIResponse.body>`
    would be empty.

    If a future version of Zyte API supported requesting both outputs on the
    same request, and both parameters were present,
    :http:`response:browserHtml` would be the one mapped into
    :class:`response.text <scrapy_zyte_api.responses.ZyteAPITextResponse.text>`
    and
    :class:`response.body <scrapy_zyte_api.responses.ZyteAPIResponse.body>`.

Both :ref:`response classes <response-classes>` have a
:class:`response.raw_api_response <scrapy_zyte_api.responses.ZyteAPIResponse.raw_api_response>`
attribute that contains a :class:`dict` with the complete, raw response from
Zyte API, where you can find all Zyte API response parameters, including those
that are not mapped into other response class attributes.

For example, for a request for :http:`response:httpResponseBody` and
:http:`response:httpResponseHeaders`, you would get:

.. code-block:: python

    def parse(self, response):
        print(response.url)
        # "https://quotes.toscrape.com/"
        print(response.status)
        # 200
        print(response.headers)
        # {b"Content-Type": [b"text/html"], …}
        print(response.text)
        # "<html>…</html>"
        print(response.body)
        # b"<html>…</html>"
        print(response.raw_api_response)
        # {
        #     "url": "https://quotes.toscrape.com/",
        #     "statusCode": 200,
        #     "httpResponseBody": "PGh0bWw+4oCmPC9odG1sPg==",
        #     "httpResponseHeaders": […],
        # }

For a request for :http:`response:screenshot`, on the other hand, the response
would look as follows:

.. code-block:: python

    def parse(self, response):
        print(response.url)
        # "https://quotes.toscrape.com/"
        print(response.status)
        # 200
        print(response.headers)
        # {}
        print(response.text)
        # ""
        print(response.body)
        # b""
        print(response.raw_api_response)
        # {
        #     "url": "https://quotes.toscrape.com/",
        #     "statusCode": 200,
        #     "screenshot": "iVBORw0KGgoAAAANSUh…",
        # }
        from base64 import b64decode

        print(b64decode(response.raw_api_response["screenshot"]))
        # b'\x89PNG\r\n\x1a\n\x00\x00\x00\r…'


.. _response-classes:

Classes
=======

Zyte API responses are mapped with one of the following classes:

-   :class:`~scrapy_zyte_api.responses.ZyteAPITextResponse` is used for HTML
    responses: responses with :http:`response:browserHtml`, or responses with
    both :http:`response:httpResponseBody` and
    :http:`response:httpResponseHeaders` where the content type is HTML.

-   :class:`~scrapy_zyte_api.responses.ZyteAPIXmlResponse` is used for XML
    responses, i.e. responses with both :http:`response:httpResponseBody` and
    :http:`response:httpResponseHeaders` where the content type is XML (e.g.
    ``text/xml``, ``application/xml``, ``application/rss+xml``).

-   :class:`~scrapy_zyte_api.responses.ZyteAPIJsonResponse` is used for JSON
    responses, i.e. responses with both :http:`response:httpResponseBody` and
    :http:`response:httpResponseHeaders` where the content type is JSON (e.g.
    ``application/json``).

-   :class:`~scrapy_zyte_api.responses.ZyteAPIResponse` is used for any other
    response, including binary responses and text responses with an
    unrecognized content type.

All response classes share a common base mixin,
:class:`~scrapy_zyte_api.responses.ZyteAPIMixin`, which can be used for
:func:`isinstance` checks::

    from scrapy_zyte_api.responses import ZyteAPIMixin

    def parse(self, response):
        if isinstance(response, ZyteAPIMixin):
            ...  # any Zyte API response

.. _response-max-size:

Responses that exceed :setting:`DOWNLOAD_MAXSIZE <scrapy:DOWNLOAD_MAXSIZE>`
===========================================================================

When a response body, whether it comes from :http:`response:browserHtml` or
:http:`response:httpResponseBody`, is larger than
:setting:`DOWNLOAD_MAXSIZE <scrapy:DOWNLOAD_MAXSIZE>`, the response body is
dropped. In that case the download handler does not raise and does not return
an empty value: it returns a
:class:`~scrapy_zyte_api.responses.ZyteAPIResponseMaxSizeExceeded` response
instead, so that the outcome can be told apart from both a normal response
and a request error.

These responses are recognizable in two ways:

-   With an :func:`isinstance` check::

        from scrapy_zyte_api.responses import ZyteAPIResponseMaxSizeExceeded

        def parse(self, response):
            if isinstance(response, ZyteAPIResponseMaxSizeExceeded):
                logger.warning(
                    "Skipping %s: its %s-byte body exceeded "
                    "DOWNLOAD_MAXSIZE",
                    response.url,
                    response.body_size,
                )
                return

-   Through the ``"zyte-api-max-size-exceeded"`` entry in
    ``response.flags`` (the regular ``"zyte-api"`` flag is also present).

A :class:`~scrapy_zyte_api.responses.ZyteAPIResponseMaxSizeExceeded` response:

-   keeps the URL of the original request in its
    :attr:`~scrapy.http.Response.url` attribute,

-   exposes the actual size of the dropped body, in bytes, through its
    :attr:`~scrapy_zyte_api.responses.ZyteAPIResponseMaxSizeExceeded.body_size`
    attribute,

-   has an empty :attr:`~scrapy.http.Response.body` (the oversize body is not
    stored),

-   is a regular :class:`~scrapy.http.Response` subclass, so downloader
    middlewares and spider callbacks receive it through the normal response
    flow instead of the error/retry flow.

Responses larger than
:setting:`DOWNLOAD_WARNSIZE <scrapy:DOWNLOAD_WARNSIZE>` but not larger than
:setting:`DOWNLOAD_MAXSIZE <scrapy:DOWNLOAD_MAXSIZE>` are returned normally;
only a warning is logged. When
:setting:`DOWNLOAD_MAXSIZE <scrapy:DOWNLOAD_MAXSIZE>` is ``0`` (the way an
unset limit is stored), no response is ever considered oversize.

.. autoclass:: scrapy_zyte_api.responses.ZyteAPIResponse
    :show-inheritance:

    .. autoattribute:: url

    .. autoattribute:: status

    .. autoattribute:: headers

    .. attribute:: body
        :type: bytes

    .. autoattribute:: raw_api_response

.. autoclass:: scrapy_zyte_api.responses.ZyteAPIResponseMaxSizeExceeded
    :show-inheritance:

    .. autoattribute:: url

    .. attribute:: body_size
        :type: int

        Actual size, in bytes, of the response body that was dropped because it
        exceeded :setting:`DOWNLOAD_MAXSIZE <scrapy:DOWNLOAD_MAXSIZE>`.

    .. attribute:: body
        :type: bytes

        Always empty: the oversize body is not stored.

    .. autoattribute:: flags

.. autoclass:: scrapy_zyte_api.responses.ZyteAPITextResponse
    :show-inheritance:

    .. autoattribute:: url

    .. autoattribute:: status

    .. autoattribute:: headers

    .. attribute:: body
        :type: bytes

    .. attribute:: text
        :type: str

    .. autoattribute:: raw_api_response

.. autoclass:: scrapy_zyte_api.responses.ZyteAPIXmlResponse
    :show-inheritance:

    .. autoattribute:: raw_api_response

.. autoclass:: scrapy_zyte_api.responses.ZyteAPIJsonResponse
    :show-inheritance:

    .. autoattribute:: raw_api_response
