# emacs: -*- mode: python; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""Basic crawler for the web
"""


from os.path import splitext, dirname, basename

from datalad.utils import updated
from datalad.dochelpers import exc_str
from datalad.support.exceptions import (
    DownloadError,
    UnhandledRedirectError,
)
from datalad.downloaders.providers import Providers

from logging import getLogger
lgr = getLogger('datalad.crawl.crawl_url')


class crawl_url(object):
    """Given a source url, perform the initial crawling of the page, i.e. simply
    bloody fetch it and pass along

    """
    def __init__(self,
                 url=None, matchers=None,
                 input='url',
                 failed=None,
                 cache_redirects=True,
                 track_redirects=False,
                 output=('response', 'url')):
        """If url is None, would try to pick it up from data[input]

        Parameters
        ----------

        matchers: list of matchers
          Expect page content in and should produce url field
        failed: {skip}, optional
          What to do about failing urls. If None -- would consult (eventually) the config
        cache_redirects: bool, optional
          Either to remember redirects for subsequent invocations
        track_redirects: bool, optional
          Either to make the redirect chain observable to downstream nodes.
          When enabled, every successfully produced data dict additionally
          carries 'redirects' (the original url followed by every redirect
          target, in the order they were encountered) and 'redirect_count'
          (the number of redirects, 0 if the url was not redirected). A hit
          in the redirects cache is reflected in the chain as a single
          redirect to the cached target. When a url fails to download and
          `failed` is None or 'skip', instead of producing nothing a record
          marked with 'status' set to 'failed' is yielded, carrying the
          failed 'url' and an 'error' description. By default disabled and
          the produced data is unchanged.
        """
        self._url = url
        self._matchers = matchers
        self._input = input
        self._output = output
        self._seen = set()
        self._providers = Providers.from_config_files()
        # partially to overcome stuck redirect
        # https://github.com/kennethreitz/requests/issues/2997
        self._redirects_cache = {} if cache_redirects else None
        self._track_redirects = track_redirects
        self.failed = failed

    def reset(self):
        """Reset cache of seen URLs"""
        self._seen = set()

    def _visit_url(self, url, data):
        if url in self._seen:
            return
        # this is just a cruel first attempt
        lgr.debug("Visiting %s" % url)

        # the chain of urls involved in this visit: starts with the original
        # url and grows by one entry per followed redirect (or a cache hit)
        redirect_chain = [url]
        try:
            retry = 0
            orig_url = url
            if self._redirects_cache is not None:
                cached_url = self._redirects_cache.get(url, url)
                if cached_url != url:
                    lgr.debug("URL %s was previously redirected to %s",
                              url, cached_url)
                    url = cached_url
                    redirect_chain.append(url)
            while True:
                retry += 1
                if retry > 100:
                    raise DownloadError(
                        "We have followed 100 redirects already. "
                        "Something is wrong!")
                try:
                    self._seen.add(url)
                    page = self._providers.fetch(url, allow_redirects=False)
                    break
                except UnhandledRedirectError as exc:
                    # since we care about tracking URL for proper full url construction
                    # we should disallow redirects and handle them manually here
                    lgr.debug("URL %s was redirected to %s" % (url, exc.url))
                    if url == exc.url:
                        raise DownloadError("Was redirected to the same url upon %s" % exc_str(exc))
                    url = exc.url
                    # guard against a loop back to an url we have already
                    # visited in this chain before even attempting to fetch
                    # it, so we never spin in an infinite redirect loop
                    if url in redirect_chain:
                        raise DownloadError(
                            "Was redirected to an already visited url %s "
                            "upon %s" % (url, exc_str(exc)))
                    redirect_chain.append(url)
                    if self._redirects_cache is not None:
                        self._redirects_cache[orig_url] = exc.url
        except DownloadError as exc:
            lgr.warning("URL %s failed to download: %s" % (url, exc_str(exc)))
            if self.failed in {None, 'skip'}:
                # TODO: config  -- crawl.failed='skip' should be a config option, for now always skipping
                if self._track_redirects:
                    # make the failure observable to downstream nodes instead
                    # of silently dropping this url
                    yield updated(data, {'url': orig_url,
                                         'status': 'failed',
                                         'error': exc_str(exc),
                                         'redirects': redirect_chain,
                                         'redirect_count':
                                             len(redirect_chain) - 1})
                return
            raise  # otherwise -- kaboom
        except Exception as exc:
            # e.g. a redirect to the same url, for which requests gives up
            # with TooManyRedirects while resolving it, instead of letting
            # us handle the redirect manually. With tracking enabled such
            # failures must be observable; without it, preserve the
            # original behavior and let the exception propagate
            if not self._track_redirects or self.failed not in {None, 'skip'}:
                raise
            lgr.warning("URL %s failed to download: %s" % (url, exc_str(exc)))
            yield updated(data, {'url': orig_url,
                                 'status': 'failed',
                                 'error': exc_str(exc),
                                 'redirects': redirect_chain,
                                 'redirect_count': len(redirect_chain) - 1})
            return

        data_ = updated(data, zip(self._output, (page, url)))
        if self._track_redirects:
            data_ = updated(data_, {'redirects': redirect_chain,
                                    'redirect_count': len(redirect_chain) - 1})
        yield data_

        # now recurse if matchers were provided
        matchers = self._matchers
        if matchers:
            lgr.debug("Looking for more URLs at %s using %s", url, matchers)
            for matcher in (matchers if isinstance(matchers, (list, tuple)) else [matchers]):
                for data_matched in matcher(data_):
                    if 'url' not in data_matched:
                        lgr.warning("Got data without a url from %s" % matcher)
                        continue
                    # proxy findings
                    for data_matched_ in self._visit_url(data_matched['url'], data_matched):
                        yield data_matched_

    def __call__(self, data={}):
        #assert(data == {}) # atm assume we are the first of mogican
        url = data[self._input] if not self._url else self._url
        return self._visit_url(url, data)

    def recurse(self, data):
        """Recurse into the page - self._url gets ignored"""
        return self._visit_url(data[self._input], data)


#    for extractors, actions in conditionals:
#        extractors = assure_tuple_or_list(extractors)
#        actions = assure_tuple_or_list(actions)
#        seen_urls = set()
#        for extractor in extractors:
#            for url, meta_ in extractor(parent_url, meta=meta):
#                if url in seen_urls:
#                    continue
#                file = None
#                # progress through actions while possibly augmenting the url, file, and/or meta_
#                for action in actions:
#                    # TODO: may be we should return a dict with whatever that action
#                    # found necessary to change, update local state and pass into
#                    url, file, meta_ = \
#                        action(parent_url=parent_url, url=url, file=file, meta=meta_)
#                seen_urls.add(url)

# TODO: probably might sense to RF into just a generic TSV file parser
def parse_checksums(digest=None):
    """Generates a node capable of parsing checksums file and generating new URLs

    Base of the available in data url is used for new URLs
    """
    def _parse_checksums(data):
        url = data['url']
        urlsplit = url.split('/')
        topurl = '/'.join(urlsplit[:-1])
        if digest is None:
            # deduce from URL's file extension
            filename = urlsplit[-1]
            base, ext = splitext(filename)
            digest_ = ext if ext else digest

        content = data['response']
        # split into separate lines, first entry is checksum, 2nd file path
        for line in content.split('\n'):
            if not line:  # empty line
                continue
            checksum, fpath = line.split(None, 1)
            yield updated(data, {'digest': digest or digest_,
                                 'checksum': checksum,
                                 'path': dirname(fpath),
                                 'filename': basename(fpath),
                                 'url': "%s/%s" % (topurl, fpath)
                                 })
    return _parse_checksums
