"""In-process browser state machine for the mock AgentInBrowser server.

One :class:`MockBrowser` holds a set of windows, each with its own DOM and
its own element cache.  It implements the same command semantics as the real
Selenium backed service:

* ``find`` / ``inputs`` / ``buttons`` replace the current window's cache.
* ``click`` / ``send_keys`` / ``get_html`` consume the cache by index;
  indices pointing at elements that existed before a DOM mutation raise a
  *stale element* error, indices outside the cached list raise an
  *index out of range* error, and an empty cache raises a
  *no elements cached* error.
* Button ``data-action`` hooks drive deterministic page mutations and new
  windows so tests can exercise navigation and popup handling locally.
* A one-shot ``inject`` queue lets tests force a transient stale failure to
  prove the runner's re-find + single-retry recovery.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .dom import Element, all_elements, parse_html, select


class MockBrowserError(RuntimeError):
    """Mirrors the server-side error strings real Selenium would produce."""


class Window:
    def __init__(self, handle: str, url: str, html: str):
        self.handle = handle
        self.url = url
        self.root = parse_html(html)
        # generation bumps on every DOM mutation; element ids encode it.
        self.generation = 1
        self.cache: List[Element] = []
        self.cache_generation = 0
        self.cache_kind = ""
        # Per-element input value storage so send_keys/clear are observable.
        self.values: Dict[int, str] = {}

    # ------------------------------------------------------------- lookup
    def refresh_dom(self, html: str, url: Optional[str] = None) -> None:
        self.root = parse_html(html)
        if url is not None:
            self.url = url
        self.generation += 1
        self.cache = []
        self.cache_generation = 0

    def element_token(self, element: Element) -> str:
        return f"{self.handle}:g{self.generation}:e{id(element)}"

    def cache_token(self, index: int) -> str:
        element = self.cache[index]
        return f"{self.handle}:g{self.cache_generation}:e{id(element)}"


class MockBrowser:
    def __init__(self, sites_dir: str):
        self.sites_dir = sites_dir
        self.windows: List[Window] = []
        self.current: Optional[Window] = None
        self._handle_seq = 0
        # Queued one-shot injections: list of dicts describing the next
        # forced failure, consumed FIFO by the indexed commands.
        self.injections: List[Dict[str, Any]] = []

    # ------------------------------------------------------------ helpers
    def _new_handle(self) -> str:
        self._handle_seq += 1
        return f"window-{self._handle_seq}"

    def _load_page_file(self, path: str) -> str:
        # path arrives like "/pages/index.html"; map under sites_dir safely.
        relative = path.lstrip("/")
        full = os.path.normpath(os.path.join(self.sites_dir, relative))
        if not full.startswith(os.path.abspath(self.sites_dir)):
            raise MockBrowserError(f"refusing to read outside sites dir: {path}")
        if not os.path.isfile(full):
            raise MockBrowserError(f"page not found: {path}")
        with open(full, "r", encoding="utf-8") as handle:
            return handle.read()

    def _window_index(self, handle: str) -> int:
        for i, window in enumerate(self.windows):
            if window.handle == handle:
                return i
        raise MockBrowserError(f"unknown window handle: {handle}")

    @property
    def window(self) -> Window:
        if self.current is None:
            raise MockBrowserError("browser is not initialized")
        return self.current

    # ------------------------------------------------------------ lifecycle
    def init(self, url: str) -> Dict[str, Any]:
        html = self._load_page_file(url)
        handle = self._new_handle()
        self.windows = [Window(handle, url, html)]
        self.current = self.windows[0]
        return {"current_url": url, "title": self._title(self.current), "window_count": 1}

    def quit(self) -> Dict[str, Any]:
        self.windows = []
        self.current = None
        self.injections = []
        return {"quit": True}

    def status(self) -> Dict[str, Any]:
        return {
            "status": "running",
            "browser_active": self.current is not None,
            "current_url": self.current.url if self.current else None,
            "window_count": len(self.windows),
        }

    @staticmethod
    def _title(window: Window) -> str:
        for element in all_elements(window.root):
            if element.tag == "title":
                return element.text
        return ""

    # -------------------------------------------------------------- finders
    def find(self, selector: str) -> Dict[str, Any]:
        window = self.window
        elements = select(window.root, selector)
        self._set_cache(window, elements, "find")
        return {
            "count": len(elements),
            "selector": selector,
            "elements": [el.public_dict(i) for i, el in enumerate(elements)],
        }

    def inputs(self) -> Dict[str, Any]:
        window = self.window
        elements = [el for el in all_elements(window.root) if el.tag == "input"]
        self._set_cache(window, elements, "inputs")
        return {
            "count": len(elements),
            "inputs": [el.input_dict(i) for i, el in enumerate(elements)],
        }

    def buttons(self) -> Dict[str, Any]:
        window = self.window
        elements = [
            el for el in all_elements(window.root)
            if el.tag == "button" or (el.tag == "input" and el.attrs.get("type") == "button")
        ]
        self._set_cache(window, elements, "buttons")
        return {
            "count": len(elements),
            "buttons": [el.button_dict(i) for i, el in enumerate(elements)],
        }

    def _set_cache(self, window: Window, elements: List[Element], kind: str) -> None:
        window.cache = elements
        window.cache_generation = window.generation
        window.cache_kind = kind

    # ------------------------------------------------------- cache checks
    def _consume_injection(self) -> Optional[Dict[str, Any]]:
        if not self.injections:
            return None
        injection = self.injections.pop(0)
        if injection.get("target") in (None, self.window.handle):
            return injection
        # Injection targeted another window; put it back and continue.
        self.injections.insert(0, injection)
        return None

    def _resolve_cached(self, index: int) -> Tuple[Window, Element]:
        window = self.window
        injection = self._consume_injection()
        if injection and injection.get("kind") == "stale":
            # Simulate the page refreshing underneath the cached handle.
            window.refresh_dom(self._render(window), window.url)
        # A mutation bumps generation and drops the cache; either condition
        # means any previously handed-out index is stale.
        if not window.cache or window.cache_generation != window.generation:
            raise MockBrowserError(
                "stale element reference: DOM changed after the elements were "
                "cached; re-run find to refresh the index list"
            )
        if not isinstance(index, int) or index < 0 or index >= len(window.cache):
            raise MockBrowserError(
                f"index {index!r} out of range for cached {len(window.cache)} element(s)"
            )
        return window, window.cache[index]

    # ------------------------------------------------------------- actions
    def click(self, index: int) -> Dict[str, Any]:
        window, element = self._resolve_cached(index)
        action = element.attrs.get("data-action", "")
        if action:
            self._apply_action(window, element, action)
        return {
            "current_url": window.url,
            "title": self._title(window),
            "window_count": len(self.windows),
        }

    def send_keys(self, index: int, text: str) -> Dict[str, Any]:
        window, element = self._resolve_cached(index)
        if element.tag != "input":
            raise MockBrowserError(f"element <{element.tag}> is not an input")
        # Real service calls clear() first, then types.
        window.values[id(element)] = text
        element.attrs["value"] = text
        return {"success": True, "value": text}

    def get_html(self, index: int) -> Dict[str, Any]:
        _window, element = self._resolve_cached(index)
        return {"html": element.inner_html()[:5000]}

    # -------------------------------------------------------- page queries
    def get_text(self) -> Dict[str, Any]:
        window = self.window
        return {"text": window.root.text[:2000]}

    def page_info(self) -> Dict[str, Any]:
        window = self.window
        return {
            "url": window.url,
            "title": self._title(window),
            "source_length": len(self._render(window)),
        }

    def sleep(self, seconds: float) -> Dict[str, Any]:
        return {"slept": seconds}

    def execute_script(self, script: str) -> Dict[str, Any]:
        # Tiny, explicit scripting surface for local tests only.
        window = self.window
        stripped = script.strip()
        if stripped == "return document.title":
            return {"result": self._title(window)}
        if stripped == "return document.URL":
            return {"result": window.url}
        if stripped.startswith("return document.title +"):
            return {"result": self._title(window) + " [scripted]"}
        raise MockBrowserError(f"mock server does not support script: {script[:80]}")

    # ------------------------------------------------------------- windows
    def switch_window(self, index: int) -> Dict[str, Any]:
        if not self.windows:
            raise MockBrowserError("no open windows")
        if index == -1:
            target = self.windows[-1]
        else:
            if not isinstance(index, int) or index < 0 or index >= len(self.windows):
                raise MockBrowserError(
                    f"window index {index!r} out of range "
                    f"(have {len(self.windows)} window(s))"
                )
            target = self.windows[index]
        self.current = target
        return {
            "current_url": target.url,
            "title": self._title(target),
            "window_count": len(self.windows),
        }

    def close_window(self) -> Dict[str, Any]:
        if not self.windows:
            raise MockBrowserError("no open windows")
        target_handle = self.window.handle
        position = self._window_index(target_handle)
        self.windows.pop(position)
        if not self.windows:
            self.current = None
            raise MockBrowserError("last window closed; no window available")
        # Real service falls back to the main (first) window.
        self.current = self.windows[0]
        return {
            "current_url": self.current.url,
            "title": self._title(self.current),
            "window_count": len(self.windows),
        }

    # ---------------------------------------------------------- injections
    def inject(self, kind: str, target: Optional[str] = None) -> Dict[str, Any]:
        self.injections.append({"kind": kind, "target": target})
        return {"queued": len(self.injections), "injection": kind}

    # ------------------------------------------------------ data-action DSL
    def _apply_action(self, window: Window, element: Element, action: str) -> None:
        if action == "navigate-detail":
            html = self._load_page_file("/pages/detail.html")
            window.refresh_dom(html, "/pages/detail.html")
        elif action == "open-popup":
            popup_html = self._load_page_file("/pages/popup.html")
            handle = self._new_handle()
            popup = Window(handle, "/pages/popup.html", popup_html)
            self.windows.append(popup)
            self.current = popup
        elif action == "refresh-list":
            html = self._load_page_file("/pages/list.html")
            window.refresh_dom(html, window.url)
        elif action == "remove-item":
            # Remove the item nearest the clicked button to force index shifts.
            target = element.parent
            if target is not None and target.parent is not None:
                target.parent.children.remove(target)
                window.generation += 1
                window.cache = []
                window.cache_generation = 0
        elif action == "add-item":
            container = element.parent
            new_item = Element(
                "li", {"class": "item dynamic"}, container
            )
            new_item.text_parts.append("Dynamically added item")
            if container is not None:
                container.children.append(new_item)
                window.generation += 1
                window.cache = []
                window.cache_generation = 0
        elif action == "mutate-value":
            element.attrs["data-clicked"] = "true"
            window.generation += 1
            window.cache = []
            window.cache_generation = 0
        else:
            raise MockBrowserError(f"unknown data-action: {action}")

    def _render(self, window: Window) -> str:
        # Re-serialise via inner_html; good enough to feed back through the
        # parser when simulating a refresh.
        body = "".join(child.inner_html() for child in window.root.children)
        return f"<!DOCTYPE html><html>{body}</html>"
