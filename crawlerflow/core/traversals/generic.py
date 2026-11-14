from scrapy.linkextractors import LinkExtractor
from urllib.parse import urlparse
import re


def _get_link_host(url):
    """
    Host (without port or credentials) of a url, lower cased, for same domain
    comparisons.
    """
    return (urlparse(url).hostname or "").lower()


class GenericLinkExtractor(object):
    """
    Extracts the links a traversal is allowed to follow from a response.

    Domain handling:

    * ``allow_domains`` empty (or not configured): only links that belong to
      the domain of the current response are returned, so a traversal can never
      silently escape to another site.
    * ``allow_domains`` containing ``"*"``: no domain restriction at all.
    * any other non empty ``allow_domains``: historical domain matching
      behaviour (a link is kept when it matches one of the configured domains).
    """

    def __init__(self,
                 restrict_xpaths=(),
                 restrict_css=(),
                 restrict_regex=(),
                 allow_domains=(),
                 link_extractor_cls=LinkExtractor, **kwargs):
        """

        :param restrict_xpaths: list of xpaths for links Extraction.
        :param restrict_css: list of xpath for links extraction
        :param restrict_regex: list of regex patterns
        :param link_extractor_cls: defaults to scrapy link extractor
        :param allow_domains: defaults to the allowed domains of spider
        """
        self.restrict_xpaths = restrict_xpaths
        self.restrict_css = restrict_css
        self.restrict_regex = restrict_regex
        if allow_domains is None:
            allow_domains = []
        elif isinstance(allow_domains, str):
            allow_domains = [allow_domains]
        self.allow_domains = list(allow_domains)
        self.link_extractor_cls = link_extractor_cls

    def extract_links(self, response=None):
        allow_domains = self.allow_domains
        allow_all_domains = "*" in allow_domains

        """
        Pass the domains to the underlying link extractor only when they are
        real domains. "*" means "no restriction" and an empty list means "stick
        to the domain of the current response", which is applied below.
        """
        link_extractor_allow_domains = () if allow_all_domains else allow_domains
        all_links = self.link_extractor_cls(allow=self.restrict_xpaths,
                                            restrict_xpaths=self.restrict_xpaths,
                                            restrict_css=self.restrict_css,
                                            allow_domains=link_extractor_allow_domains
                                            ).extract_links(response=response)
        all_links_strings = [link.url for link in all_links]
        if allow_all_domains:
            return all_links_strings
        if len(allow_domains) == 0:
            """
            With no configured domains, converge to the domain of the
            response being parsed instead of following every link we find.
            """
            response_domain = _get_link_host(response.url)
            return [link for link in all_links_strings
                    if _get_link_host(link) == response_domain]
        filtered_links = []
        for domain in allow_domains:
            regex_domain = r"/{}".format(domain).replace(".", r"\.")
            pattern = re.compile(regex_domain)
            for link in all_links_strings:
                if pattern.search(link):
                    filtered_links.append(link)
        return filtered_links
