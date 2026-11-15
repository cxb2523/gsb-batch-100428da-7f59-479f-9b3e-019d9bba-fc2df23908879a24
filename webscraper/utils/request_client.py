from requests.auth import HTTPProxyAuth

from utils.proxy_utils.proxy import Proxy, ua
import requests
import config


class RequestFailure:
    """Identifiable, non-silent failure result for a request.

    Returned instead of a response (and instead of a silent None) when a
    proxied request cannot be completed, so callers can tell *why* the
    request failed. Falsy, with response-like attributes, so existing
    truthiness/attribute checks keep working.
    """

    ok = False
    status_code = None
    text = ""

    def __init__(self, url, reason):
        self.url = url
        self.reason = reason

    def __bool__(self):
        return False

    def __repr__(self):
        return "RequestFailure(url={!r}, reason={!r})".format(self.url, self.reason)


class ReqestClient(Proxy):
    # how many times a proxied request is attempted in total
    # (first attempt + one retry with a fresh proxy)
    MAX_PROXY_ATTEMPTS = 2

    def __init__(self):
        super().__init__()

    def request_with_proxy_header(self, url):
        header = {'User-Agent': ua.user_agent()}

        if config.USE_PROXY_SERVER:
            return self._request_via_proxy(url, header)

        # non-proxy mode: original behavior (None on connection errors)
        try:
            response = requests.get(url, headers=header, timeout=20, verify=True)
        except Exception:
            response = None
        return response

    def _request_via_proxy(self, url, header):
        """Request through a proxy. On a proxy error, evict the bad proxy and
        retry once with a different one. If that still fails (or no proxy is
        left), return an identifiable RequestFailure instead of None."""
        auth = HTTPProxyAuth("", "")
        last_error = None

        for attempt in range(self.MAX_PROXY_ATTEMPTS):
            if not self.proxy_list:
                return RequestFailure(
                    url,
                    "no usable proxy available (proxy list is empty or exhausted)")
            proxy = self.generate_proxy(force_refresh=attempt > 0)
            try:
                return requests.get(url, proxies=proxy, auth=auth, headers=header,
                                    timeout=20, verify=True)
            except requests.RequestException as exc:
                last_error = exc
                # remove the invalid proxy from the proxy list and update the file
                self._drop_proxy(proxy.get("http"))

        return RequestFailure(
            url,
            "proxied request failed after {} attempts: {}".format(
                self.MAX_PROXY_ATTEMPTS, last_error))

    def _drop_proxy(self, proxy_url):
        """Evict an invalid proxy from the list and persist the update."""
        if proxy_url in self.proxy_list:
            self.proxy_list.remove(proxy_url)
            self.write_proxy_list()


if __name__ == '__main__':
    cli = ReqestClient()
    print(cli.request_with_proxy_header("https://www.wikipedia.org/").text)
