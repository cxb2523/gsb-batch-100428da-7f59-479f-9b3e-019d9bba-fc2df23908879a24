"""Value extraction from parsed HTML content."""
from __future__ import annotations

from typing import TYPE_CHECKING

from bs4 import Tag

if TYPE_CHECKING:
    from .selector import Selector


class ValueExtractor:
    """Extracts values from BeautifulSoup elements."""

    def __init__(self, separator: str = ","):
        """Initialize value extractor."""
        self._separator = separator

    def extract_single(self, element: Tag, selector: Selector) -> str:
        """Extract a value from a single element."""
        if selector.attribute is not None:
            return self._extract_attribute(element, selector)
        return self._extract_tag_value(element, selector)

    def extract_list(self, elements: list[Tag], selector: Selector) -> str:
        """Extract values from a list of elements and join with separator."""
        if selector.attribute is not None:
            values = [
                self._extract_attribute(elem, selector, index)
                for index, elem in enumerate(elements)
            ]
        else:
            values = [self._extract_tag_value(elem, selector) for elem in elements]
        return self._separator.join(values)

    @staticmethod
    def _extract_attribute(element: Tag, selector: Selector, index: int | None = None):
        """Extract an attribute, raising ValueError when the element lacks it."""
        try:
            return element[selector.attribute]
        except KeyError:
            # A page that no longer matches the config would otherwise leak a
            # KeyError, which on_error (and re-authentication) treat as an
            # unclassified failure. Report what is missing instead.
            if index is None:
                raise ValueError(
                    f"Could not find attribute '{selector.attribute}' in the selected tag"
                ) from None
            raise ValueError(
                f"Could not find attribute '{selector.attribute}' in element at "
                f"index {index} of the selected list"
            ) from None

    @staticmethod
    def _extract_tag_value(tag: Tag, selector: Selector) -> str:
        """Extract value from tag based on extract mode.

        For HTML raw-text elements (style, script, template) with default
        text extraction, we use tag.string which returns the raw content
        without parsing child tags. For explicit extract modes or XML
        content, the extract parameter is always respected.
        """
        if selector.extract == "content":
            return ''.join(map(str, tag.contents))
        if selector.extract == "tag":
            return str(tag)
        # Default text extraction (extract is "text" or None)
        if tag.name in ("style", "script", "template") and tag.string is not None:
            return tag.string
        return tag.text
