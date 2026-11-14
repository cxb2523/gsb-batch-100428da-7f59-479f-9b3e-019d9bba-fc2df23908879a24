import json
import time
import warnings
import weakref
from typing import List, Dict, Any, Optional

try:  # Playwright is a declared dependency but is imported lazily by WebDesktop.
    from playwright.sync_api import Error as _PlaywrightError
except Exception:  # pragma: no cover - keeps `import tarsier` usable without a browser install
    class _PlaywrightError(Exception):
        """Fallback base error used when Playwright cannot be imported."""


class WebElementError(Exception):
    """Base class for web element lifecycle and lookup errors raised by Tarsier."""


class PageClosedError(WebElementError):
    """The browser tab owning an element has been closed; the element is stale."""


class PageNotActiveError(WebElementError):
    """The browser tab owning an element is still open but is not the active tab."""


class WebElementTimeoutError(TimeoutError, WebElementError):
    """Waiting for a web element exceeded its timeout.

    Inherits from the builtin ``TimeoutError`` so existing ``except TimeoutError``
    handlers keep working, while remaining distinguishable from page lifecycle errors.
    """


class WebElementAmbiguityWarning(UserWarning):
    """A role/name lookup matched several elements; the first visible one was used."""


# Maps the Playwright BrowserContext wrapper to the page WebDesktop currently
# treats as the active tab. Keyed by the context so that every WebElement can
# resolve the active tab from its own page without holding a WebDesktop reference.
_ACTIVE_PAGES = weakref.WeakKeyDictionary()
_UNSET = object()


def _register_active_page(page) -> None:
    """Records ``page`` as the active tab of its browser context."""
    try:
        _ACTIVE_PAGES[page.context] = page
    except Exception:
        # Standalone/fake page objects do not support weak references; remain permissive.
        pass


def _active_page_for(page):
    """Returns the active tab for the context that owns ``page``, if known."""
    try:
        return _ACTIVE_PAGES.get(page.context)
    except Exception:
        return None


def _same_page(first, second) -> bool:
    """Compares Playwright page wrappers, tolerating distinct wrapper instances."""
    if first is second:
        return True
    first_impl = getattr(first, "_impl_obj", _UNSET)
    second_impl = getattr(second, "_impl_obj", _UNSET)
    if first_impl is _UNSET or second_impl is _UNSET:
        return False
    return first_impl is second_impl


def _describe_lookup(role: Optional[str], name: Optional[str], selector: Optional[str]) -> str:
    parts = []
    if role is not None:
        parts.append(f"role={role!r}")
    if name is not None:
        parts.append(f"name={name!r}")
    if selector is not None:
        parts.append(f"selector={selector!r}")
    return ", ".join(parts) if parts else "any element"


def _plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    return singular if count == 1 else (plural or singular + "s")


class WebElement:
    def __init__(self, page, locator=None, role="document", name="", highlight_actions: bool = False):
        self.page = page
        self._locator = locator or page.locator("body")
        # Locator used by actions/reads. Semantic lookups point this at the first
        # visible match; ``_locator`` keeps matching every candidate so nested
        # ``find`` calls retain their historical scoping.
        self._action_locator = self._locator.first
        self._role = role
        self._name = name
        self.highlight_actions = highlight_actions
        self._selector: Optional[str] = None
        self._match_base = None
        # Populated by semantic lookups (find/wait_for_element).
        self.match_count: Optional[int] = None
        self.visible_match_count: Optional[int] = None
        self.chosen_index: Optional[int] = None
        self.is_ambiguous: bool = False
        self._created_url = self._safe_url()

    @property
    def name(self) -> str:
        return self._name

    @property
    def role(self) -> str:
        return self._role

    def _safe_url(self) -> Optional[str]:
        try:
            return self.page.url
        except Exception:
            return None

    def _page_is_closed(self) -> bool:
        is_closed = getattr(self.page, "is_closed", None)
        if not callable(is_closed):
            # Duck-typed page stand-ins (e.g. test doubles) cannot report closure.
            return False
        try:
            return bool(is_closed())
        except Exception:
            return True

    def _page_index(self, page) -> Optional[int]:
        try:
            for index, candidate in enumerate(page.context.pages):
                if _same_page(candidate, page):
                    return index
        except Exception:
            pass
        return None

    def _page_reference(self) -> str:
        index = self._page_index(self.page)
        url = self._safe_url() or self._created_url
        if index is None:
            return f"browser tab (url={url!r})" if url else "its browser tab"
        return f"browser tab [{index}] (url={url!r})" if url else f"browser tab [{index}]"

    def _target_label(self) -> str:
        if self._selector:
            return f"selector={self._selector!r}"
        if self._role and self._name:
            return f"role={self._role!r}, name={self._name!r}"
        if self._role and self._role != "document":
            return f"role={self._role!r}"
        if self._name:
            return f"name={self._name!r}"
        return "the page body"

    def _require_current_page(self) -> None:
        """Ensures the element still belongs to the open, active tab.

        Raises:
            PageClosedError: the owning tab has been closed.
            PageNotActiveError: the owning tab is open but is not the active tab.
        """
        page = self.page
        if self._page_is_closed():
            raise PageClosedError(
                f"Cannot use web element ({self._target_label()}): its {self._page_reference()} "
                "has been closed. Re-locate the element from an open tab via "
                "WebDesktop.get_current_page() or WebDesktop.switch_to_page(index)."
            )

        active = _active_page_for(page)
        if active is None or _same_page(active, page):
            return

        own_index = self._page_index(page)
        active_index = self._page_index(active)
        active_url = None
        try:
            active_url = active.url
        except Exception:
            pass
        try:
            active_closed = bool(active.is_closed())
        except Exception:
            active_closed = False

        if active_closed:
            active_description = "the tab WebDesktop last treated as active, which has since been closed"
        elif active_index is not None:
            active_description = f"browser tab [{active_index}] (url={active_url!r})"
        else:
            active_description = f"another browser tab (url={active_url!r})"

        if own_index is not None:
            recovery = (
                f"Call WebDesktop.switch_to_page({own_index}) before reusing this element, "
                "or re-locate it from the active tab."
            )
        else:
            recovery = "Switch back to its tab before reusing this element, or re-locate it from the active tab."

        raise PageNotActiveError(
            f"Cannot use web element ({self._target_label()}): it belongs to {self._page_reference()}, "
            f"which is not the active tab. The active tab is {active_description}. {recovery}"
        )

    def _highlight(self):
        """Visually flashes the element on screen to help humans debug automation."""
        if not getattr(self, 'highlight_actions', False):
            return

        try:
            # Inject a red box shadow temporarily using Playwright evaluate
            locator = self._action_locator
            locator.evaluate("""el => {
                const oldShadow = el.style.boxShadow;
                const oldTransition = el.style.transition;
                el.style.transition = 'box-shadow 0.1s';
                el.style.boxShadow = '0 0 0 5px rgba(255, 0, 0, 0.8)';
                setTimeout(() => {
                    el.style.boxShadow = oldShadow;
                    el.style.transition = oldTransition;
                }, 400);
            }""")
            time.sleep(0.1) # brief pause to let human see it before action
        except Exception:
            pass

    def click(self) -> 'WebElement':
        self._require_current_page()
        self._highlight()
        self._action_locator.click()
        return self

    def double_click(self) -> 'WebElement':
        self._require_current_page()
        self._highlight()
        self._action_locator.dblclick()
        return self

    def focus(self) -> 'WebElement':
        self._require_current_page()
        self._highlight()
        self._action_locator.focus()
        return self

    def type(self, text: str, waitTime: float = 0.05) -> 'WebElement':
        self._require_current_page()
        self._highlight()
        self._action_locator.fill(text)
        return self

    def read(self) -> str:
        self._require_current_page()
        return self._action_locator.inner_text()

    def scroll_into_view(self) -> 'WebElement':
        """Scrolls the browser window until this element is visible."""
        self._require_current_page()
        self._action_locator.scroll_into_view_if_needed()
        return self

    def scroll_to_bottom(self) -> 'WebElement':
        """Scrolls the browser window to the absolute bottom of the page."""
        self._require_current_page()
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        return self

    def _locator_for(self, role: Optional[str], name: Optional[str], selector: Optional[str], exact: bool):
        """Builds the locator for a lookup, mirroring the historical precedence."""
        if selector:
            return self._locator.locator(selector), False
        elif role and name:
            return self._locator.get_by_role(role, name=name, exact=exact), True
        elif role:
            return self._locator.get_by_role(role), True
        elif name:
            return self._locator.get_by_text(name, exact=exact), True
        return self._locator, False

    def _resolve_semantic_match(self):
        """Picks the first visible match among role/name candidates.

        Returns ``(total, visible, chosen_index)`` where ``chosen_index`` is the
        zero-based position of the selected visible match (``None`` if hidden).
        """
        base = self._locator
        self._match_base = base
        total = base.count()
        visible_locator = base.filter(visible=True)
        visible = visible_locator.count()
        if visible > 0:
            self._action_locator = visible_locator.first

        chosen_index = None
        if total > 1 and visible > 0:
            for index in range(total):
                try:
                    if base.nth(index).is_visible():
                        chosen_index = index
                        break
                except _PlaywrightError:
                    break
        self.match_count = total
        self.visible_match_count = visible
        self.chosen_index = chosen_index
        self.is_ambiguous = total > 1
        return total, visible, chosen_index

    def _ambiguity_message(self, role, name, total, visible, chosen_index) -> str:
        if chosen_index is None:
            resolution = "none of the matches are visible, so waiting for this element will time out"
        else:
            resolution = f"using the first visible match (index {chosen_index})"
        visible_word = _plural(visible, "is", "are")
        return (
            f"Ambiguous web element lookup in {self._page_reference()}: found {total} "
            f"{_plural(total, 'element')} matching role={role!r} and name={name!r}, "
            f"of which {visible} {visible_word} visible. "
            f"Tarsier is {resolution}. Use a more specific selector if another element was intended."
        )

    def _refresh_match_stats(self) -> None:
        base = self._match_base
        if base is None:
            return
        try:
            self.match_count = base.count()
            self.visible_match_count = base.filter(visible=True).count()
            self.is_ambiguous = self.match_count > 1
            self.chosen_index = None
            if self.visible_match_count > 0:
                for index in range(self.match_count):
                    if base.nth(index).is_visible():
                        self.chosen_index = index
                        break
        except Exception:
            pass

    def _build_child(self, role: Optional[str], name: Optional[str], selector: Optional[str], exact: bool):
        """Builds a child element and resolves semantic candidates.

        Returns ``(element, semantic, role_and_name_match, total, visible, chosen_index)``.
        """
        self._require_current_page()
        loc, semantic = self._locator_for(role, name, selector, exact)
        element = WebElement(self.page, loc, role, name or selector, highlight_actions=self.highlight_actions)
        element._selector = selector

        total = visible = chosen_index = None
        if semantic:
            try:
                total, visible, chosen_index = element._resolve_semantic_match()
            except _PlaywrightError as exc:
                if self._page_is_closed():
                    raise PageClosedError(
                        f"Cannot use web element ({_describe_lookup(role, name, selector)}): "
                        f"its {self._page_reference()} was closed while looking up a new element."
                    ) from exc
                raise
        return element, semantic, role is not None and name is not None, total, visible, chosen_index

    def _warn_if_ambiguous(self, element, role, name, total, visible, chosen_index) -> None:
        if total is not None and total > 1:
            warnings.warn(
                element._ambiguity_message(role, name, total, visible, chosen_index),
                WebElementAmbiguityWarning,
                stacklevel=3,
            )

    def find(self, role: Optional[str] = None, name: Optional[str] = None, selector: Optional[str] = None, exact: bool = False) -> 'WebElement':
        element, _, role_name_match, total, visible, chosen_index = self._build_child(role, name, selector, exact)
        if role_name_match:
            self._warn_if_ambiguous(element, role, name, total, visible, chosen_index)
        return element

    def wait_for_element(self, role: Optional[str] = None, name: Optional[str] = None, selector: Optional[str] = None, timeout: int = 10) -> 'WebElement':
        started = time.monotonic()
        element, semantic, role_name_match, _, _, _ = self._build_child(role, name, selector, exact=False)

        try:
            if semantic:
                # Wait for the first visible candidate so that hidden duplicates
                # never satisfy the wait, and only one visible match is adopted.
                wait_locator = element._match_base.filter(visible=True).first
                wait_locator.wait_for(state="visible", timeout=timeout*1000)
                element._action_locator = wait_locator
            else:
                element._action_locator.wait_for(state="attached", timeout=timeout*1000)
        except Exception as exc:
            elapsed = time.monotonic() - started
            if self._page_is_closed():
                raise PageClosedError(
                    f"Cannot use web element ({_describe_lookup(role, name, selector)}): "
                    f"its {self._page_reference()} was closed while waiting for the element."
                ) from exc
            element._refresh_match_stats()
            raise WebElementTimeoutError(
                self._timeout_message(role, name, selector, timeout, elapsed, element, semantic)
            ) from exc

        # Report ambiguity from the post-wait state so matches that appeared or
        # disappeared while waiting are reflected accurately.
        element._refresh_match_stats()
        if role_name_match:
            self._warn_if_ambiguous(
                element, role, name, element.match_count, element.visible_match_count, element.chosen_index
            )
        return element

    def _timeout_message(self, role, name, selector, timeout, elapsed, element, semantic) -> str:
        target = "a visible web element" if semantic else "a web element"
        lookup = _describe_lookup(role, name, selector)
        details = ""
        if element.match_count is not None:
            details = (
                f" Found {element.match_count} matching {_plural(element.match_count, 'element')}, "
                f"{element.visible_match_count} of them visible."
            )
        timeout_word = _plural(timeout, "second") if isinstance(timeout, int) else "seconds"
        return (
            f"Timed out after {max(elapsed, 0.0):.2f} seconds waiting for {target} "
            f"({lookup}) in {self._page_reference()}; the configured timeout was {timeout} {timeout_word}."
            f"{details} The element may have been removed from the page or may never become visible."
        )

    def wait_until_clickable(self, timeout: int = 10) -> 'WebElement':
        self._require_current_page()
        self._action_locator.wait_for(state="visible", timeout=timeout*1000)
        return self

    def button(self, name: str) -> 'WebElement':
        return self.find(role="button", name=name)

    def textbox(self, name: Optional[str] = None) -> 'WebElement':
        tb = self.find(role="textbox", name=name)
        try:
            if tb._locator.first.count() > 0:
                return tb
        except Exception:
            pass
        return self.find(role="searchbox", name=name)

    def menu(self, name: str) -> 'WebElement':
        return self.find(role="menuitem", name=name)

    def dump_ui(self, max_depth: int = 5) -> Dict[str, Any]:
        """
        Returns a JSON structure representing the page state using ARIA snapshot.
        This provides a semantic view similar to the Desktop DOM.
        """
        self._require_current_page()
        try:
            snapshot = self.page.locator("body").aria_snapshot()
        except Exception:
            snapshot = ""

        return {
            "role": "document",
            "name": self.page.title(),
            "aria_snapshot": snapshot
        }

    def to_json(self) -> str:
        return json.dumps(self.dump_ui(), indent=2)


class WebDesktop:
    def __init__(self, headless: bool = False, highlight_actions: bool = False):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=headless)
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.highlight_actions = highlight_actions
        _register_active_page(self.page)

    @property
    def pages(self) -> List[WebElement]:
        """Returns a list of all open pages/tabs in the browser context."""
        return [WebElement(p, highlight_actions=self.highlight_actions) for p in self.context.pages]

    def new_page(self, url: Optional[str] = None) -> WebElement:
        """Opens a new tab/page, sets it as active, and optionally navigates to a URL."""
        self.page = self.context.new_page()
        _register_active_page(self.page)
        if url:
            self.goto(url)
        return WebElement(self.page, highlight_actions=self.highlight_actions)

    def switch_to_page(self, index: int) -> WebElement:
        """Switches the active page context to the tab at the specified index."""
        pages = self.context.pages
        if index < 0 or index >= len(pages):
            raise IndexError(f"Page index {index} out of range (0 to {len(pages)-1})")
        self.page = pages[index]
        self.page.bring_to_front()
        _register_active_page(self.page)
        return WebElement(self.page, highlight_actions=self.highlight_actions)

    def switch_to_page_by_title(self, title: str) -> WebElement:
        """Switches the active page context to the tab containing the specified title (case-insensitive)."""
        for p in self.context.pages:
            try:
                p_title = p.title()
                if title.lower() in p_title.lower():
                    self.page = p
                    self.page.bring_to_front()
                    _register_active_page(self.page)
                    return WebElement(self.page, highlight_actions=self.highlight_actions)
            except Exception:
                pass
        raise ValueError(f"No page found with title containing: '{title}'")

    def goto(self, url: str) -> WebElement:
        _register_active_page(self.page)
        self.page.goto(url)
        try:
            self.page.wait_for_load_state('networkidle', timeout=5000)
        except Exception:
            pass
        return WebElement(self.page, highlight_actions=self.highlight_actions)

    def get_current_page(self) -> WebElement:
        return WebElement(self.page, highlight_actions=self.highlight_actions)

    def close(self):
        try:
            _ACTIVE_PAGES.pop(self.context, None)
        except Exception:
            pass
        browser = getattr(self, 'browser', None)
        if browser:
            try:
                if not getattr(browser, 'is_connected', lambda: True)():
                    # Already closed elsewhere; skip the redundant (no-op) teardown.
                    self.browser = None
                else:
                    browser.close()
                    self.browser = None
            except Exception:
                self.browser = None
        if hasattr(self, '_pw') and self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
