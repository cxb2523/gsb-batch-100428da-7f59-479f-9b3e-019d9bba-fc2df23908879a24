"""A deliberately tiny DOM + HTML parser + CSS selector engine.

The mock AgentInBrowser server needs just enough browser semantics to
reproduce the tricky parts of the real protocol:

* a per-window element list with stable per-DOM-generation element ids
* ``find`` caches the most recent element list and resets it on every call
* DOM mutations (button actions, injected failures) bump a generation so
  indices captured against an old list become *stale*
* minimal CSS selector support: tag, ``.class``, ``#id`` and combinations
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Dict, List, Optional

VOID_TAGS = {"input", "br", "hr", "img", "meta", "link"}


class Element:
    """One parsed DOM element with the metadata the API exposes."""

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["Element"]):
        self.tag = tag.lower()
        self.attrs = attrs
        self.parent = parent
        self.children: List[Element] = []
        self.text_parts: List[str] = []

    # ------------------------------------------------------------- helpers
    @property
    def id(self) -> str:
        return self.attrs.get("id", "")

    @property
    def class_list(self) -> List[str]:
        return self.attrs.get("class", "").split()

    @property
    def text(self) -> str:
        parts = list(self.text_parts)
        for child in self.children:
            parts.append(child.text)
        return " ".join(" ".join(parts).split())

    def public_dict(self, index: int) -> Dict[str, str]:
        """The metadata shape returned by the real ``find`` endpoint."""
        return {
            "index": index,
            "tag": self.tag,
            "text": self.text[:80],
            "class": self.attrs.get("class", ""),
            "id": self.attrs.get("id", ""),
        }

    def input_dict(self, index: int) -> Dict[str, str]:
        return {
            "index": index,
            "type": self.attrs.get("type", "text"),
            "name": self.attrs.get("name", ""),
            "id": self.attrs.get("id", ""),
            "placeholder": self.attrs.get("placeholder", ""),
            "value": self.attrs.get("value", ""),
        }

    def button_dict(self, index: int) -> Dict[str, str]:
        return {
            "index": index,
            "text": self.text[:80],
            "class": self.attrs.get("class", ""),
            "id": self.attrs.get("id", ""),
        }

    def inner_html(self) -> str:
        attrs = "".join(f' {k}="{v}"' for k, v in self.attrs.items())
        if self.tag in VOID_TAGS:
            return f"<{self.tag}{attrs} />"
        inner = "".join(self.text_parts) + "".join(c.inner_html() for c in self.children)
        return f"<{self.tag}{attrs}>{inner}</{self.tag}>"


class _DOMBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("document", {}, None)
        self.stack: List[Element] = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        element = Element(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(element)
        if tag not in VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs) -> None:
        element = Element(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(element)

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self.stack) - 1, 0, -1):
            if self.stack[depth].tag == tag:
                del self.stack[depth:]
                break

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].text_parts.append(data)


def parse_html(html: str) -> Element:
    builder = _DOMBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def all_elements(root: Element) -> List[Element]:
    out: List[Element] = []

    def walk(node: Element) -> None:
        for child in node.children:
            out.append(child)
            walk(child)

    walk(root)
    return out


# --------------------------------------------------------------------- CSS
def _parse_simple_selector(selector: str) -> List[dict]:
    """Parse a compound selector like ``button.btn.primary`` into conditions."""
    conditions = []
    token = ""
    i = 0
    while i < len(selector):
        char = selector[i]
        if char in ".#":
            if token:
                conditions.append({"kind": "tag", "value": token.lower()})
                token = ""
            kind = "class" if char == "." else "id"
            i += 1
            name = ""
            while i < len(selector) and selector[i] not in ".# ":
                name += selector[i]
                i += 1
            conditions.append({"kind": kind, "value": name})
            continue
        token += char
        i += 1
    if token:
        conditions.append({"kind": "tag", "value": token.lower()})
    return conditions


def _matches(element: Element, conditions: List[dict]) -> bool:
    for cond in conditions:
        kind, value = cond["kind"], cond["value"]
        if kind == "tag" and element.tag != value:
            return False
        if kind == "id" and element.id != value:
            return False
        if kind == "class" and value not in element.class_list:
            return False
    return True


def select(root: Element, selector: str) -> List[Element]:
    """Support ``a, button.primary, #id`` style (comma + compound) selectors."""
    groups = [group.strip() for group in selector.split(",") if group.strip()]
    matched: List[Element] = []
    seen: List[int] = []
    for group in groups:
        # Descendant combinators: take the last compound and match anywhere;
        # sufficient for the test pages while keeping the engine tiny.
        compounds = group.split()
        conditions = _parse_simple_selector(compounds[-1])
        for element in all_elements(root):
            if id(element) in seen:
                continue
            if _matches(element, conditions):
                matched.append(element)
                seen.append(id(element))
    return matched
