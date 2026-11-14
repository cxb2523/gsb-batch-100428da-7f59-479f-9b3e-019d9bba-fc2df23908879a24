"""Exceptions raised by gmaps-extractor.

All of them inherit from :class:`GMapsExtractorError`.
"""


class GMapsExtractorError(Exception):
    """Base class for every error raised by this library."""


class ConfigurationError(GMapsExtractorError):
    """Invalid settings, e.g. an incomplete proxy configuration."""


class BoundaryError(GMapsExtractorError):
    """The area name could not be resolved to a bounding box."""


class RequestError(GMapsExtractorError):
    """A Google Maps request failed, also after its one retry.

    Causes: a network error, an HTTP error status, Google's consent page or
    an empty answer (which is how Google usually answers a blocked client).
    """


class RateLimitError(RequestError):
    """Google answered HTTP 429 (too many requests), also after the retry."""
