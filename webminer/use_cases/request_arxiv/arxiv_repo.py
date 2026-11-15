"""
    This is a helper class to parse arxiv.org site.
    It uses the arxiv.org REST API to search for articles.

    based on karpathy's arxiv-sanity:
    https://github.com/karpathy/arxiv-sanity-preserver/blob/master/fetch_papers.py
    and arxiv example:
    https://arxiv.org/help/api/examples/python_arXiv_parsing_example.txt
"""

import requests
import feedparser

from webminer.entities import arxiv_document as ad


# feedparser 5 exposes _FeedParserMixin at package level; feedparser 6 moved
# it into the feedparser.mixin module. Support both.
try:
    _FeedParserMixin = feedparser._FeedParserMixin  # pylint: disable=W0212
except AttributeError:
    from feedparser import mixin as _feedparser_mixin

    _FeedParserMixin = _feedparser_mixin._FeedParserMixin  # pylint: disable=W0212


class ArxivRepo:
    """
        The helper class to parse arxiv.org site.

    Raises:
        ValueError: If formatting operator is not supported
        AssertionError: If url parsing went wrong

    Returns:
        dict: Dictionary of results
    """

    # Base api query url
    base_url = "http://export.arxiv.org/api/query?"

    # Expose both of the open source metadata namespaces in feedparser
    _FeedParserMixin.namespaces[
        "http://a9.com/-/spec/opensearch/1.1/"
    ] = "opensearch"
    _FeedParserMixin.namespaces[
        "http://arxiv.org/schemas/atom"
    ] = "arxiv"

    def __init__(self, entries=None):
        self._entries = []
        if entries:
            self._entries.extend(entries)
        # Metadata about pages that could not be fetched/parsed during the
        # last fetch_papers() run. Each item is a dict with the page start
        # index ("start_index") and the error description ("error"). Empty
        # when every page succeeded.
        self.fetch_errors = []

    def _check(self, element, key, value):
        """Checks elements and formats them

        Args:
            element (obj): Document object
            key (string): Key in document object
            value (string): Value of the corresponding document object

        Raises:
            ValueError: If some operators are not supported

        Returns:
            string: Value of the attribute of an object
        """

        if "__" not in key:
            key = key + "__eq"

        key, operator = key.split("__")

        if operator not in ["eq", "lt", "gt"]:
            raise ValueError(f"Operator {operator} is not supported")

        operator = "__{}__".format(operator)

        return getattr(element[key], operator)(value)

    def list(self, filters=None):
        """Apply filters on entries and return a filtered list
            filters (dict, optional): Defaults to None. Parameters to filter entries

        Returns:
            list: List of arxiv document objects
        """

        if not filters:
            return self._entries

        # Fetch fresh papers and keep the injected entries untouched. The
        # filters are applied on every paper accumulated across all pages,
        # not only the last fetched page.
        entries = self.fetch_papers()

        result = []
        result.extend(entries)

        for key, value in filters.items():
            result = [e for e in result if self._check(e, key, value)]

        return [self._to_document(r) for r in result]

    @staticmethod
    def _to_document(dict_entry):
        """Build an ArxivDocument from a dict produced by extract_relevant_info"""

        return ad.ArxivDocument(
            doc_id=dict_entry["id"],
            url=dict_entry["link"],
            title=dict_entry["title"],
            abstract=dict_entry["abstract"],
            authors=dict_entry["authors"],
            publish_date=dict_entry["publish_date"],
            pdf_url=dict_entry.get("pdf", ""),
        )

    # @classmethod
    # def encode_feedparser_dict(cls, fp_dict):
    #     """
    #     recursive function to convert the internal feedparse object to a simple dict
    #     """
    #     if isinstance(fp_dict, feedparser.FeedParserDict) or isinstance(fp_dict, dict):
    #         ret_dict = {}
    #         for key in fp_dict.keys():
    #             ret_dict[key] = self.encode_feedparser_dict(fp_dict[key])
    #         return ret_dict
    #     elif isinstance(fp_dict, list):
    #         dict_list = []
    #         for key in fp_dict:
    #             dict_list.append(self.encode_feedparser_dict(key))
    #         return dict_list
    #     else:
    #         return fp_dict

    @classmethod
    def extract_relevant_info(cls, fp_dict):
        """Extracts the relevant info

        Args:
            fp_dict (dict): Dictionary that provides the information

        Returns:
            dict: The dictionary with only relevant information
        """

        ret_dict = {}
        ret_dict["publish_date"] = fp_dict["published"]
        ret_dict["authors"] = [auth["name"] for auth in fp_dict["authors"]]
        ret_dict["title"] = fp_dict["title"]
        ret_dict["abstract"] = fp_dict["summary"]
        ret_dict["id"] = fp_dict["id"]
        ret_dict["link"] = fp_dict["link"]

        for link in fp_dict["links"]:
            try:
                if link.title == "pdf":
                    ret_dict["pdf"] = link.href
            except AttributeError:
                pass

        return ret_dict

    def run_query(self, search_query, start=0, max_results=10):
        """Queries url and returns a parsed response

        Args:
            search_query (string): The terms we are looking for
            start (int, optional): Defaults to 0. Start at results page
            max_results (int, optional):
                Defaults to 10. How many results shall be retrieved

        Returns:
            FeedParserDict: A parsed dictionary with the information
        """

        query = f"search_query={search_query}&sortBy=lastUpdatedDate&start={start}&max_results={max_results}"  # pylint: disable=C0301
        r = requests.get(self.base_url + query)
        parsed_response = feedparser.parse(r.text)

        return parsed_response

    @classmethod
    def parse_arxiv_url(cls, url):
        """
        extracts the raw id and the version
        examples is http://arxiv.org/abs/1512.08756v2
        """
        ix = url.rfind("/")
        idversion = url[ix + 1 :]  # extract just the id (and the version)
        parts = idversion.split("v")
        if not len(parts) == 2:
            raise AssertionError("error parsing url " + url)
        return parts[0], int(parts[1])

    default_search_query = (
        "cat:cs.CV+OR+cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL+OR+cat:cs.NE+OR+cat:stat.ML"
    )

    def fetch_papers(
        self,
        search_query=default_search_query,
        start_index=0,
        max_index=100,
        results_per_iteration=100,
        # wait_time=5.0,
        # break_on_no_added=True,
    ):
        """loops according to the results_per_iteration and fetch results pages from arxiv.org

        Keyword Arguments:
            search_query {str} -- [arxiv.org topics to query]
                (default: default_search_query)
            start_index {int} -- [pagination start index] (default: {0})
            max_index {int} --
                [upper bound on paper index we will fetch] (default: {10000})
            results_per_iteration {int} -- [passed to arxiv API] (default: {100})
            wait_time {float} -- [pause in seconds between requests] (default: {5.0})
            break_on_no_added {bool} --
                [break out early if all returned query papers are already in db]
                (default: {True})

        Returns:
            list: dicts of all papers accumulated across the fetched pages.
                Pages that fail (request error or no parseable entries) are
                recorded in self.fetch_errors together with their start index
                and do not abort the whole fetch.

        Raises:
            RuntimeError: if every requested page failed and no paper could
                be fetched, instead of silently returning an empty list.
        """

        results = []
        self.fetch_errors = []
        page_starts = list(range(start_index, max_index, results_per_iteration))

        for i in page_starts:
            try:
                parsed_response = self.run_query(search_query, i, results_per_iteration)

                page_entries = parsed_response.entries
                if not page_entries:
                    raise ValueError("no entries parsed from page response")

                page_results = []
                for entry in page_entries:
                    dict_entry = self.extract_relevant_info(entry)

                    rawid, version = self.parse_arxiv_url(dict_entry["link"])
                    dict_entry["_rawid"] = rawid
                    dict_entry["_version"] = version

                    page_results.append(dict_entry)
            except Exception as exc:  # pylint: disable=W0703
                # A single broken page must not discard the papers already
                # fetched from the other pages: record it and keep going.
                self.fetch_errors.append(
                    {
                        "start_index": i,
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                )
                continue

            results.extend(page_results)

        if page_starts and not results and len(self.fetch_errors) == len(page_starts):
            details = "; ".join(
                f"start={failure['start_index']} {failure['error']}"
                for failure in self.fetch_errors
            )
            raise RuntimeError("all arxiv pages failed to fetch papers: " + details)

        return results
