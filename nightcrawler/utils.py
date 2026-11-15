# # nightcrawler/utils.py
from urllib.parse import urlparse
from mitmproxy import http
from typing import Optional, Set, Any, Tuple


# Target kinds used to keep link-derived and form-derived scan targets in
# separate signature namespaces, so the same method/path/param-name structure
# discovered both as a link and as a form does not swallow the other.
TARGET_TYPE_LINK = "link"
TARGET_TYPE_FORM = "form"


def is_in_scope(url: str, target_domains: Set[str]) -> bool:
    """Checks if the given URL's hostname is in scope."""
    if not url or not target_domains:
        return False
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return False
        return any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in target_domains
        )
    except Exception:
        return False


def _request_param_entries(
    request: http.Request,
) -> Tuple[Tuple[str, Any], ...]:
    """
    Collects the request's parameter names together with their default values.

    Query parameters are always considered. For non-GET requests, urlencoded
    body fields are considered as well. Entries are returned sorted by name so
    signatures stay stable regardless of field order.
    """
    entries = {name: request.query.get(name) for name in request.query.keys()}
    if request.method != "GET" and request.urlencoded_form:
        for name in request.urlencoded_form.keys():
            entries[name] = request.urlencoded_form.get(name)
    return tuple(sorted(entries.items()))


def create_target_signature(
    request: http.Request,
    logger: Any,
    target_type: str = TARGET_TYPE_LINK,
) -> Optional[str]:
    """
    Creates a unique signature for a scan target, logging with the provided
    logger.

    ``target_type`` separates link-derived targets (``TARGET_TYPE_LINK``) from
    form-derived targets (``TARGET_TYPE_FORM``):

    * Link signatures only take parameter *names* into account, so the same
      query structure (e.g. differing only by value) is enqueued once.
    * Form signatures also take each field's default *value* into account, so
      forms sharing the same action and parameter names but pre-filled with
      different defaults each get their own scan target.
    """
    try:
        logger.debug(
            f"[Create Sig] Checking request ({target_type}): "
            f"{request.method} {request.pretty_url}"
        )

        parsed_url = urlparse(request.pretty_url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"

        param_entries = _request_param_entries(request)
        if not param_entries:
            logger.debug(f"[Create Sig]   No parameters found, returning None.")
            return None

        if target_type == TARGET_TYPE_FORM:
            # Include field default values: same action/param names with
            # different pre-filled values must produce distinct signatures.
            param_part = ",".join(
                f"{name}={value}" for name, value in param_entries
            )
        else:
            # Links are deduplicated by parameter names only.
            param_part = ",".join(name for name, _ in param_entries)

        signature = (
            f"{target_type}::{request.method}::{base_url}::{param_part}"
        )
        logger.debug(
            f"[Create Sig]   Param Entries: {param_entries} | "
            f"Generated Signature: {signature}"
        )
        return signature
    except Exception as e:
        logger.error(
            f"[Create Sig] Error generating signature for {request.pretty_url}: {e}"
        )
        return None
