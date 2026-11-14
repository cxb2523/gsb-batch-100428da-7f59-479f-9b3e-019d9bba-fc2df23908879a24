# !/usr/bin/env python
# -*- coding:utf-8 -*-

import time

import cchardet
import requests

from talospider.utils import Logger


class RequestOutcome(object):
    """
    Final result of a single Request execution.

    Every call of :meth:`Request.__call__` produces exactly one outcome, no
    matter whether the download succeeded, failed or was retried. Instances
    are plain picklable containers so they can travel back from worker
    processes when the spider runs in multi-process mode.
    """

    def __init__(self, url, method, success, attempts, status_code=None,
                 error_type=None, elapsed=None):
        # Final URL: after redirects when a response was received, otherwise
        # the URL handed to the downloader.
        self.url = url
        self.method = method
        # Whether a usable response body was obtained in the end.
        self.success = success
        # Total number of download attempts (initial attempt + every retry).
        self.attempts = attempts
        # HTTP status code of the last response, ``None`` when no response
        # was received (connection errors, invalid url, ...).
        self.status_code = status_code
        # Distinguishable machine-readable failure category, ``None`` on
        # success, e.g. ``http_4xx`` / ``http_5xx`` / ``connection_error``
        # / ``request_error`` / ``decode_error`` / ...
        self.error_type = error_type
        # Wall-clock seconds spent on the whole Request, including retries.
        self.elapsed = elapsed

    def as_dict(self):
        return {
            'url': self.url,
            'method': self.method,
            'success': self.success,
            'attempts': self.attempts,
            'status_code': self.status_code,
            'error_type': self.error_type,
            'elapsed': self.elapsed,
        }

    def __repr__(self):
        return ('RequestOutcome(url={url!r}, method={method!r}, success={success}, '
                'attempts={attempts}, status_code={status_code!r}, '
                'error_type={error_type!r}, elapsed={elapsed})').format(**self.as_dict())

    def __eq__(self, other):
        if not isinstance(other, RequestOutcome):
            return NotImplemented
        return self.as_dict() == other.as_dict()


class Request():
    """
    Request class for each request
    """
    name = 'downloading'

    REQUEST_CONFIG = {
        'RETRIES': 3,
        'DELAY': 0,
        'TIMEOUT': 30
    }

    METHOD = ['GET', 'POST']

    def __init__(self, url, method='GET', params=None, data=None, request_config=None, headers=None, proxies=None,
                 cookies=None, verify=False, callback=None, extra_value={}, file_type='text', **kwargs):
        self.url = url
        self.method = method.upper()
        if self.method not in self.METHOD:
            raise ValueError('%s method is not supported' % self.method)
        if request_config and isinstance(request_config, dict):
            self.request_config = request_config
        elif request_config and not isinstance(request_config, dict):
            raise ValueError(
                "request_config must be a dict type, just like request_config = {'RETRIES':3,'DELAY':0,'TIMEOUT':60}")
        else:
            self.request_config = self.REQUEST_CONFIG
        self.params = params
        self.data = data
        self.headers = headers
        self.proxies = proxies
        self.cookies = cookies
        self.verify = verify
        self.callback = callback
        self.extra_value = extra_value
        self.file_type = file_type
        self.kwargs = kwargs
        self.logger = Logger(name=self.name)
        # :class:`RequestOutcome` of the last :meth:`__call__`, populated
        # after every execution so the spider can collect it.
        self.outcome = None

    def __call__(self):
        if self.request_config.get('DELAY', 0) > 0:
            time.sleep(self.request_config.get('DELAY', 0))
        start = time.perf_counter()
        res, self.outcome = self.download(url=self.url,
                                          method=self.method,
                                          params=self.params,
                                          data=self.data,
                                          headers=self.headers,
                                          proxies=self.proxies,
                                          cookies=self.cookies,
                                          verify=self.verify,
                                          num_retries=self.request_config.get('RETRIES', 3),
                                          extra_value=self.extra_value,
                                          file_type=self.file_type,
                                          **self.kwargs)
        self.outcome.elapsed = time.perf_counter() - start
        self.logger.info("{method}: {url}".format(
            method=self.method,
            url=self.url))
        if self.callback is None:
            return res
        callback_result = self.callback(res=res)
        if callback_result is not None:
            return list(callback_result)

    def _fetch(self, url, method, headers, proxies, params, data, cookies, verify, **kwargs):
        """
        Send one raw HTTP request and return the response object.

        Isolated as its own method so the download flow (retry counting,
        outcome recording, body decoding) can be exercised in tests with a
        replaceable download function instead of hitting the network.
        """
        if not verify:
            requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
        if method.lower() == 'get':
            return requests.get(
                url=url,
                params=params,
                headers=headers,
                proxies=proxies,
                cookies=cookies,
                timeout=self.request_config.get('TIMEOUT', 30),
                verify=verify,
                **kwargs
            )
        return requests.post(
            url=url,
            data=data,
            headers=headers,
            proxies=proxies,
            cookies=cookies,
            timeout=self.request_config.get('TIMEOUT', 30),
            verify=verify,
            **kwargs
        )

    def download(self, url, method, headers, proxies, params, data, cookies, verify, num_retries, extra_value,
                 file_type, **kwargs):
        """
        Run the download loop and return ``(response, outcome)``.

        Only HTTP 5xx responses trigger a retry (and only while
        ``num_retries`` is positive); every retry is counted in
        ``outcome.attempts``. 4xx responses, connection errors, decoding
        failures and any other exception do not retry and each produce a
        distinguishable :class:`RequestOutcome` instead of only a log line.
        """
        text = None
        response = None
        attempts = 0
        last_status_code = None
        error_type = None
        # When a response is received this becomes the final URL after
        # redirects; without a response (connection error, invalid url) it
        # stays the URL handed to the downloader.
        final_url = url
        while True:
            attempts += 1
            try:
                response = self._fetch(url=url,
                                       method=method,
                                       headers=headers,
                                       proxies=proxies,
                                       params=params,
                                       data=data,
                                       cookies=cookies,
                                       verify=verify,
                                       **kwargs)
                final_url = getattr(response, 'url', url)
                last_status_code = response.status_code
                # Retry trigger is intentionally unchanged: 5xx only, and
                # only while retries remain.
                if num_retries > 0 and 500 <= response.status_code < 600:
                    self.logger.info('Retrying url: %s' % url)
                    num_retries -= 1
                    continue

                response.raise_for_status()
                if file_type == 'bytes':
                    text = response.content
                elif file_type == 'json':
                    text = response.json()
                elif file_type == 'raw':
                    text = response.raw
                elif file_type == 'text':
                    content = response.content
                    charset = cchardet.detect(content)
                    text = content.decode(charset['encoding'])
                break
            except requests.exceptions.MissingSchema:
                error_type = 'missing_schema'
                self.logger.error(
                    "Invalid URL '{url}': No schema supplied. Perhaps you meant http://{url} ?".format(url=url))
                break
            except requests.exceptions.HTTPError as e:
                # raise_for_status only raises for 4xx/5xx responses that
                # were not retried away.
                if last_status_code is not None and 500 <= last_status_code < 600:
                    error_type = 'http_5xx'
                else:
                    error_type = 'http_4xx'
                self.logger.error(e)
                break
            except requests.exceptions.ConnectionError:
                error_type = 'connection_error'
                self.logger.error('%s excepted a ConnectionError' % url)
                break
            except requests.exceptions.RequestException as e:
                # Timeouts, invalid urls and other transport-level failures
                # that are neither connection errors nor HTTP status errors.
                error_type = 'request_error'
                self.logger.error(e)
                break
            except Exception as e:
                # Body decoding / json parsing / anything raised before a
                # usable body was produced.
                error_type = 'decode_error'
                self.logger.exception(e)
                break

        success = text is not None
        outcome = RequestOutcome(
            url=final_url,
            method=method,
            success=success,
            attempts=attempts,
            status_code=last_status_code,
            error_type=None if success else error_type,
        )
        result = type('Response', (),
                      {'html': text, 'url': url, 'extra_value': extra_value}) if success else None
        return result, outcome

    def __getstate__(self):
        # The logger holds runtime logging state and must not be required to
        # cross a process boundary; it is recreated on the other side.
        state = self.__dict__.copy()
        state.pop('logger', None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.logger = Logger(name=self.name)

    def __str__(self):
        return "<%s %s>" % (self.method, self.url)
