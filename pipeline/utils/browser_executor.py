"""Advanced browser automation and monkey-patching for the pipeline.

This module provides the `BrowserExecutor` which implements a robust Two-Pass
form-filling strategy. It also contains several monkey-patches for the
`browser-use` library to enable features like local file:// URL access,
improved stability tracking, and custom launch logging.

Structured result contract
--------------------------
`BrowserExecutor.fill_form` returns a JSON-serializable dict instead of free
text. Every mapped field gets an explicit outcome (see the ``STATUS_*``
constants below), field results keep the mapping order, and the top-level
object records overall success/failure and a machine-readable failure reason.

Field outcomes:

* ``filled``            - value typed into an exactly matching label.
* ``truncated``         - value exceeded the field's character constraint, was
                          truncated, and the truncated value was typed. Both
                          the original and the actually-filled value are kept.
* ``label_not_found``   - no element with that exact label exists on the page;
                          the value is never guessed into a similar field.
* ``not_mapped``        - a field discovered on the page was not produced by
                          the mapping stage.
* ``fill_error``        - a browser interaction failed while filling the field.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import aiofiles

from pipeline.config import PipelineConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Structured result statuses / failure reasons
# ---------------------------------------------------------------------------

#: Value was typed into the matching field without modification.
STATUS_FILLED = "filled"
#: Value was truncated against a field constraint and then typed.
STATUS_TRUNCATED = "truncated"
#: No element with the exact mapped label exists on the page.
STATUS_LABEL_NOT_FOUND = "label_not_found"
#: Field exists on the page but the mapping stage did not produce a value.
STATUS_NOT_MAPPED = "not_mapped"
#: Browser interaction raised while filling the field.
STATUS_FILL_ERROR = "fill_error"

#: Failure reason: the mapping LLM response could not be parsed as JSON.
REASON_MAPPING_PARSE_ERROR = "mapping_parse_error"
#: Failure reason: the mapping LLM returned an empty object.
REASON_MAPPING_EMPTY = "mapping_empty"
#: Failure reason: one or more fields could not be filled (see field_results).
REASON_FIELD_FAILURES = "field_failures"
#: Failure reason: an unexpected exception aborted the run.
REASON_UNEXPECTED_ERROR = "unexpected_error"

#: Custom exception raised by the mapping stage on unusable LLM output.
class MappingError(Exception):
    """Raised when the LLM mapping output cannot be used for filling."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# ---------------------------------------------------------------------------
# Optional browser-use dependency
# ---------------------------------------------------------------------------
# The heavy `browser-use` stack (plus a real browser) is only required when a
# form is actually filled. Importing it lazily/optionally lets the module be
# imported - and unit tested with stubs - in environments where the package,
# its browser, or API credentials are unavailable.
try:
    from browser_use import Agent, Browser, BrowserProfile
    from browser_use.browser.events import (
        BrowserStateRequestEvent,
    )
    from browser_use.browser.views import (
        BrowserStateSummary,
        PageInfo,
        SerializedDOMState,
    )
    from browser_use.browser.watchdogs.dom_watchdog import DOMWatchdog
    from browser_use.browser.watchdogs.local_browser_watchdog import LocalBrowserWatchdog
    from browser_use.browser.watchdogs.security_watchdog import SecurityWatchdog
    from browser_use.llm.google.chat import ChatGoogle
    from browser_use.utils import create_task_with_error_handling
    from langchain_core.messages import HumanMessage

    _BROWSER_USE_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised implicitly in stubbed envs
    _BROWSER_USE_AVAILABLE = False
    Agent = Browser = BrowserProfile = None  # type: ignore[assignment]
    BrowserStateRequestEvent = None  # type: ignore[assignment]
    BrowserStateSummary = PageInfo = SerializedDOMState = None  # type: ignore[assignment]
    DOMWatchdog = LocalBrowserWatchdog = SecurityWatchdog = None  # type: ignore[assignment]
    ChatGoogle = None  # type: ignore[assignment]
    create_task_with_error_handling = None  # type: ignore[assignment]
    HumanMessage = None  # type: ignore[assignment]

if _BROWSER_USE_AVAILABLE:
    # Monkey-patch SecurityWatchdog to allow file:// URLs
    _original_is_url_allowed = getattr(SecurityWatchdog, "_is_url_allowed", None)

    def _patched_is_url_allowed(self: SecurityWatchdog, url: str) -> bool:
        """Patch for SecurityWatchdog to permit local file access.

        This override ensures that the agent can interact with local HTML files
        and documents stored on the filesystem.
        """
        # Always allow file:// URLs for local development
        if url.startswith('file:///'):
            return True
        if _original_is_url_allowed is None:
            return True
        return _original_is_url_allowed(self, url)

    SecurityWatchdog._is_url_allowed = _patched_is_url_allowed

    # Monkey-patch LocalBrowserWatchdog to log launch command
    _original_launch_browser = getattr(LocalBrowserWatchdog, "_launch_browser", None)

    async def _patched_launch_browser(self: LocalBrowserWatchdog, max_retries: int = 3) -> Any:
        self.logger.info(f"[LocalBrowserWatchdog] Intercepted launch! Profile args: {self.browser_session.browser_profile.get_args()}")
        if _original_launch_browser is None:  # pragma: no cover - defensive
            raise AttributeError("LocalBrowserWatchdog._launch_browser is unavailable")
        return await _original_launch_browser(self, max_retries)

    if _original_launch_browser is not None:
        LocalBrowserWatchdog._launch_browser = _patched_launch_browser

    # Monkey-patch DOMWatchdog to allow DOM building for file:// URLs


    _original_on_browser_state_request = getattr(DOMWatchdog, "on_BrowserStateRequestEvent", None)

    async def _handle_empty_page_state(self: DOMWatchdog, page_url: str, tabs_info: list[PageInfo], event: BrowserStateRequestEvent) -> BrowserStateSummary:
        self.logger.debug(f'⚡ Skipping BuildDOMTree for empty target: {page_url}')

        # Create minimal DOM state
        content = SerializedDOMState(_root=None, selector_map={})
        screenshot_b64 = None

        try:
            page_info = await self._get_page_info()
        except Exception as e:
            self.logger.debug(f'Failed to get page info from CDP for empty page: {e}, using fallback')
            viewport = self.browser_session.browser_profile.viewport or {'width': 1280, 'height': 720}
            page_info = PageInfo(
                viewport_width=viewport['width'],
                viewport_height=viewport['height'],
                page_width=viewport['width'],
                page_height=viewport['height'],
                scroll_x=0, scroll_y=0,
                pixels_above=0, pixels_below=0,
                pixels_left=0, pixels_right=0,
            )

        return BrowserStateSummary(
            dom_state=content,
            url=page_url,
            title='Empty Tab',
            tabs=tabs_info,
            screenshot=screenshot_b64,
            page_info=page_info,
            pixels_above=0, pixels_below=0,
            browser_errors=[],
            is_pdf_viewer=False,
            recent_events=self._get_recent_events_str() if event.include_recent_events else None,
            pending_network_requests=[],
            pagination_buttons=[],
            closed_popup_messages=self.browser_session._closed_popup_messages.copy(),
        )


    async def _get_page_info_safe(self) -> PageInfo:
        try:
            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: Getting page info from CDP...')
            return await asyncio.wait_for(self._get_page_info(), timeout=1.0)
        except Exception as e:
            self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Failed to get page info from CDP: {e}, using fallback')
            viewport = self.browser_session.browser_profile.viewport or {'width': 1280, 'height': 720}
            return PageInfo(
                viewport_width=viewport['width'],
                viewport_height=viewport['height'],
                page_width=viewport['width'],
                page_height=viewport['height'],
                scroll_x=0, scroll_y=0,
                pixels_above=0, pixels_below=0,
                pixels_left=0, pixels_right=0,
            )


    async def _execute_dom_and_screenshot(self, event: BrowserStateRequestEvent) -> tuple[SerializedDOMState, str | None]:
        dom_task = None
        screenshot_task = None

        if event.include_dom:
            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: 🌳 Starting DOM tree build task...')
            previous_state = (
                self.browser_session._cached_browser_state_summary.dom_state
                if self.browser_session._cached_browser_state_summary
                else None
            )
            dom_task = create_task_with_error_handling(
                self._build_dom_tree_without_highlights(previous_state),
                name='build_dom_tree',
                logger_instance=self.logger,
                suppress_exceptions=True,
            )

        if event.include_screenshot:
            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: 📸 Starting clean screenshot task...')
            screenshot_task = create_task_with_error_handling(
                self._capture_clean_screenshot(),
                name='capture_screenshot',
                logger_instance=self.logger,
                suppress_exceptions=True,
            )

        content = SerializedDOMState(_root=None, selector_map={})
        screenshot_b64 = None

        if dom_task:
            try:
                content = await dom_task
                self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ DOM tree build completed')
            except Exception as e:
                self.logger.warning(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: DOM build failed: {e}, using minimal state')
                # content is already initialized to empty

        if screenshot_task:
            try:
                screenshot_b64 = await screenshot_task
                self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ Clean screenshot captured')
            except Exception as e:
                self.logger.warning(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Clean screenshot failed: {e}')
                screenshot_b64 = None

        return content, screenshot_b64


    def _create_recovery_state(page_url: str, error_msg: str) -> BrowserStateSummary:
        return BrowserStateSummary(
            dom_state=SerializedDOMState(_root=None, selector_map={}),
            url=page_url,
            title='Error',
            tabs=[],
            screenshot=None,
            page_info=PageInfo(
                viewport_width=1280, viewport_height=720,
                page_width=1280, page_height=720,
                scroll_x=0, scroll_y=0,
                pixels_above=0, pixels_below=0,
                pixels_left=0, pixels_right=0,
            ),
            pixels_above=0, pixels_below=0,
            browser_errors=[error_msg],
            is_pdf_viewer=False,
            recent_events=None,
            pending_network_requests=[],
            pagination_buttons=[],
            closed_popup_messages=[],
        )


    async def _add_highlights_if_needed(self, content: SerializedDOMState):
        if not (content and content.selector_map and self.browser_session.browser_profile.dom_highlight_elements):
            return
        try:
            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: 🎨 Adding browser-side highlights...')
            await self.browser_session.add_highlights(content.selector_map)
        except Exception as e:
            self.logger.warning(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Browser highlighting failed: {e}')


    async def _get_title_safe(self) -> str:
        try:
            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: Getting page title...')
            return await asyncio.wait_for(self.browser_session.get_current_page_title(), timeout=1.0)
        except Exception as e:
            self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Failed to get title: {e}')
            return 'Page'


    async def _wait_for_page_stability(self, pending_requests: list):
        self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ⏳ Waiting for page stability...')
        try:
            if pending_requests:
                await asyncio.sleep(0.3)
            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ Page stability complete')
        except Exception as e:
            self.logger.warning(
                f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Network waiting failed: {e}, continuing anyway...'
            )


    async def on_patched_browser_state_request_event(self, event: BrowserStateRequestEvent) -> BrowserStateSummary:
        """Coordinated browser state capture with enhanced stability and local file support.

        This is a patched replacement for `DOMWatchdog.on_BrowserStateRequestEvent`.
        It ensures that local `file://` URLs are treated as valid states and
        implements extra wait logic for network stability before capturing the DOM.
        """
        try:
            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: STARTING browser state request (PATCHED)')
            page_url = await self.browser_session.get_current_page_url()
            self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got page URL: {page_url}')

            if self.browser_session.agent_focus_target_id:
                self.logger.debug(f'Current page URL: {page_url}, target_id: {self.browser_session.agent_focus_target_id}')

            scheme = page_url.lower().split(':', 1)[0]
            not_a_meaningful_website = scheme not in ('http', 'https', 'file')

            pending_requests = []
            if not not_a_meaningful_website:
                try:
                    pending_requests = await self._get_pending_network_requests()
                    if pending_requests:
                        self.logger.debug(f'🔍 Found {len(pending_requests)} pending requests before stability wait')
                except Exception as e:
                    self.logger.debug(f'Failed to get pending requests before wait: {e}')

                await _wait_for_page_stability(self, pending_requests)

            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: Getting tabs info...')
            tabs_info = await self.browser_session.get_tabs()
            self.logger.debug(f'🔍 DOMWatchdog.on_BrowserStateRequestEvent: Got {len(tabs_info)} tabs')

            if not_a_meaningful_website:
                return await _handle_empty_page_state(self, page_url, tabs_info, event)

            content, screenshot_b64 = await _execute_dom_and_screenshot(self, event)
            await _add_highlights_if_needed(self, content)

            title = await _get_title_safe(self)
            page_info = await _get_page_info_safe(self)

            is_pdf_viewer = page_url.endswith('.pdf') or '/pdf/' in page_url

            pagination_buttons_data = []
            if content and content.selector_map:
                pagination_buttons_data = self._detect_pagination_buttons(content.selector_map)

            browser_state = BrowserStateSummary(
                dom_state=content,
                url=page_url,
                title=title,
                tabs=tabs_info,
                screenshot=screenshot_b64,
                page_info=page_info,
                pixels_above=0, pixels_below=0,
                browser_errors=[],
                is_pdf_viewer=is_pdf_viewer,
                recent_events=self._get_recent_events_str() if event.include_recent_events else None,
                pending_network_requests=pending_requests,
                pagination_buttons=pagination_buttons_data,
                closed_popup_messages=self.browser_session._closed_popup_messages.copy(),
            )

            self.browser_session._cached_browser_state_summary = browser_state
            if page_info:
                self.browser_session._original_viewport_size = (page_info.viewport_width, page_info.viewport_height)

            self.logger.debug('🔍 DOMWatchdog.on_BrowserStateRequestEvent: ✅ COMPLETED - Returning browser state')
            return browser_state

        except Exception as e:
            self.logger.error(f'Failed to get browser state: {e}')
            return _create_recovery_state(locals().get('page_url', ''), str(e))

    if _original_on_browser_state_request is not None:
        DOMWatchdog.on_BrowserStateRequestEvent = on_patched_browser_state_request_event


# ---------------------------------------------------------------------------
# Helpers for schema / label / constraint handling
# ---------------------------------------------------------------------------

def _normalize_label(label: Any) -> str:
    """Normalize a label for exact, case/whitespace-insensitive matching.

    Matching stays *exact* (no fuzzy guessing): only case, surrounding
    whitespace and repeated inner whitespace are folded.
    """
    text = str(label if label is not None else "")
    return re.sub(r"\s+", " ", text.strip()).casefold()


_MAX_LENGTH_PATTERNS = (
    re.compile(r"max(?:\.|imum)?\s*(?:of)?\s*(\d{1,6})\s*(?:characters?|chars?|letters?)", re.IGNORECASE),
    re.compile(r"(\d{1,6})\s*(?:characters?|chars?|letters?)\s*(?:max(?:\.|imum)?)", re.IGNORECASE),
    re.compile(r"limit\s*(?:of)?\s*(\d{1,6})", re.IGNORECASE),
)


def _parse_max_length(constraint: Any) -> int | None:
    """Extract a maximum character count from a constraint description."""
    if constraint is None:
        return None
    if isinstance(constraint, bool):
        return None
    if isinstance(constraint, int):
        return constraint if constraint > 0 else None
    text = str(constraint)
    for pattern in _MAX_LENGTH_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


def _parse_schema_fields(schema_text: str) -> list[dict[str, Any]]:
    """Parse the Pass-1 schema output into an ordered list of field dicts.

    Each dict has ``label`` (str), ``field_type`` (str | None) and
    ``max_length`` (int | None). JSON object/array output is preferred; a
    best-effort text parse is used as a fallback. Duplicate labels are kept
    once, preserving first-seen order.
    """
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(label: Any, field_type: Any = None, constraint: Any = None) -> None:
        if label is None:
            return
        label_text = str(label).strip()
        if not label_text:
            return
        key = _normalize_label(label_text)
        if key in seen:
            return
        seen.add(key)
        fields.append(
            {
                "label": label_text,
                "field_type": str(field_type).strip() if field_type else None,
                "max_length": _parse_max_length(constraint),
            }
        )

    raw_text = schema_text or ""
    json_candidate = raw_text.strip()
    # Unwrap a fenced JSON block if the agent emitted one.
    if "```json" in json_candidate:
        json_candidate = json_candidate.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in json_candidate:
        json_candidate = json_candidate.split("```", 1)[1].split("```", 1)[0].strip()

    parsed: Any = None
    try:
        parsed = json.loads(json_candidate)
    except (ValueError, TypeError):
        parsed = None

    if isinstance(parsed, dict):
        # Either {"fields": [...]} or a label -> constraint mapping.
        candidate = parsed.get("fields") if isinstance(parsed.get("fields"), list) else None
        if candidate is not None:
            for item in candidate:
                if isinstance(item, dict):
                    _add(
                        item.get("label") or item.get("name"),
                        item.get("field_type") or item.get("type"),
                        item.get("constraint") or item.get("constraints") or item.get("max_length"),
                    )
        else:
            for label, constraint in parsed.items():
                if isinstance(constraint, dict):
                    _add(
                        label,
                        constraint.get("field_type") or constraint.get("type"),
                        constraint.get("constraint")
                        or constraint.get("constraints")
                        or constraint.get("max_length"),
                    )
                else:
                    _add(label, None, constraint)
    elif isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                _add(
                    item.get("label") or item.get("name"),
                    item.get("field_type") or item.get("type"),
                    item.get("constraint") or item.get("constraints") or item.get("max_length"),
                )
            elif isinstance(item, str):
                _add(item)

    if fields:
        return fields

    # Fallback: best-effort line-based parse of free-text schema output.
    for line in raw_text.splitlines():
        cleaned = line.strip(" \t-*#0123456789.")
        if not cleaned:
            continue
        label = cleaned.split(":", 1)[0].strip()
        # Drop a trailing parenthesized type annotation, e.g. "Full Name (text)".
        label = re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()
        _add(label, None, cleaned)

    return fields


class BrowserExecutor:
    """High-level browser automation engine for form filling.

    Implements a Two-Pass strategy:
    1. Schema Extraction: Discovering field labels and constraints.
    2. Data Mapping: Using an LLM to map source data to the discovered schema.
    3. Deterministic Filling: Executing precise, label-based interactions and
       reporting a structured per-field outcome for every mapped field.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        if not _BROWSER_USE_AVAILABLE:
            # Leave `llm` unset; tests inject their own stub and real usage
            # requires the optional browser-use dependency to be installed.
            self.llm = None
            return
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.llm = ChatGoogle(
            model="gemini-2.0-flash",
            api_key=api_key
        )

    # -- factory methods (overridable in tests to inject stubs) -------------

    def _create_browser(self, browser_config: Any) -> Any:
        """Create a browser session. Wrapped so tests can substitute a stub."""
        return Browser(browser_profile=browser_config)

    def _create_agent(self, task: str, browser: Any) -> Any:
        """Create a browser-use agent. Wrapped so tests can substitute a stub."""
        return Agent(task=task, llm=self.llm, browser=browser)

    # -- Pass 1 -------------------------------------------------------------

    async def _extract_schema(self, browser_config: Any, url: str) -> str:
        """Pass 1: Extracts the form schema (labels and constraints) from the page."""
        task = (
            f"Navigate to {url}. \n"
            "Identify all visible input fields, textareas, and form elements. \n"
            "Wait for the form to fully load. Scroll if necessary. \n"
            "For each field, extract: \n"
            "1. The exact label text.\n"
            "2. The field type (text, email, tel, etc.).\n"
            "3. Any constraints (e.g., 'Max 50 characters').\n"
            "Output the results as a JSON array of objects with keys "
            "'label', 'field_type' and 'constraint'. "
            "Do NOT fill anything yet. Just report the schema and stop."
        )

        browser = self._create_browser(browser_config)
        agent = self._create_agent(task, browser)
        try:
            result = await agent.run(max_steps=10)
            return str(result.final_result())
        finally:
            await browser.kill()

    # -- Pass 1.5 -----------------------------------------------------------

    async def _map_data_to_schema(self, schema: str, source_data: dict[str, Any]) -> dict[str, Any]:
        """Pass 1.5: Uses LLM to map source data to the extracted schema labels.

        Raises:
            MappingError: when the LLM output is not parseable JSON or is an
                empty object. Truncation is deliberately *not* requested from
                the LLM anymore - field constraints are enforced deterministically
                in Pass 2 so the original values can be preserved in the report.
        """
        prompt = (
            "You are a Form Mapping Expert. \n"
            "Given the following FORM SCHEMA (list of labels/fields) and SOURCE DATA (JSON), "
            "create a deterministic MAPPING JSON. \n\n"
            f"### FORM SCHEMA:\n{schema}\n\n"
            f"### SOURCE DATA:\n{json.dumps(source_data, indent=2)}\n\n"
            "### RULES:\n"
            "1. Map each source data point to the MOST RELEVANT form label.\n"
            "2. Output the FULL value for every field. Do NOT truncate values; "
            "the system enforces character limits itself.\n"
            "3. Ensure distinct fields for distinct data (e.g. Founder 1 vs Founder 2).\n"
            "4. NEVER merge two data points into one field.\n"
            "5. If a field type is 'email', ensure the mapped value is a valid email.\n"
            "6. Output ONLY a JSON object where keys are FORM LABELS and values are the STRINGS to type."
        )
        # Use the underlying LLM with a HumanMessage to avoid Pydantic errors
        response = await self.llm.ainvoke([HumanMessage(content=prompt)])
        content = response.completion
        # Basic cleanup of markdown if LLM returned it
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            mapping = json.loads(content)
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse mapping JSON: {e}. Content: {content}")
            raise MappingError(
                REASON_MAPPING_PARSE_ERROR,
                f"Mapping LLM returned unparseable JSON: {e}",
            )

        if not isinstance(mapping, dict):
            logger.error(f"Mapping JSON was not an object. Content: {content}")
            raise MappingError(
                REASON_MAPPING_PARSE_ERROR,
                "Mapping LLM returned JSON that is not an object",
            )
        if not mapping:
            logger.error("Mapping LLM returned an empty object; nothing to fill.")
            raise MappingError(
                REASON_MAPPING_EMPTY,
                "Mapping LLM returned an empty object; no fields were produced",
            )
        return mapping

    # -- Pass 2 -------------------------------------------------------------

    async def _fill_form_deterministically_internal(
        self,
        browser_config: Any,
        url: str,
        mapping: dict[str, Any],
        schema_fields: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Pass 2: strict, label-based deterministic filling with per-field reporting.

        Returns one result dict per mapped field (mapping order preserved),
        plus ``not_mapped`` entries for schema fields absent from the mapping.
        Labels that do not exist on the page are reported as
        ``label_not_found`` and are never forced into a similar field.
        """
        # Index the page schema by normalized label for exact matching.
        constraints: dict[str, dict[str, Any]] = {}
        for field in schema_fields:
            constraints[_normalize_label(field["label"])] = field

        # Build results strictly in mapping order. Labels that do not exist on
        # the page are recorded, never re-routed to a similar field.
        field_results: list[dict[str, Any] | None] = []
        planned: list[dict[str, Any]] = []
        reserved_slots: list[int] = []
        mapped_labels: set[str] = set()
        for label, original_value in mapping.items():
            mapped_labels.add(_normalize_label(label))
            original_text = "" if original_value is None else str(original_value)
            page_field = constraints.get(_normalize_label(label))
            if page_field is None:
                field_results.append(
                    {
                        "label": str(label),
                        "status": STATUS_LABEL_NOT_FOUND,
                        "original_value": original_text,
                        "filled_value": None,
                        "max_length": None,
                        "error": "No field with this exact label was found on the page",
                    }
                )
                continue

            max_length = page_field.get("max_length")
            filled_text = original_text
            truncated = False
            if isinstance(max_length, int) and max_length > 0 and len(original_text) > max_length:
                filled_text = original_text[:max_length]
                truncated = True

            planned.append(
                {
                    "label": str(label),
                    "original_value": original_text,
                    "filled_value": filled_text,
                    "max_length": max_length,
                    "truncated": truncated,
                }
            )
            # Reserve the slot so final results stay in mapping order; the
            # actual status is filled in after the browser interaction below.
            reserved_slots.append(len(field_results))
            field_results.append(None)

        if planned:
            executable_mapping = {item["label"]: item["filled_value"] for item in planned}
            mapping_str = json.dumps(executable_mapping, indent=2)
            task = (
                f"# TASK: Fill Application Form (DETERMINISTIC MODE)\n"
                f" ## 1. NAVIGATION\n"
                f" - **ACTION**: Navigate to {url} immediately.\n\n"
                f" ## 2. MAPPING TO EXECUTE\n"
                f" The following JSON provides the EXACT strings to type for each label:\n"
                f" ```json\n{mapping_str}\n```\n\n"
                f" ## 3. EXECUTION RULES\n"
                f" - **Strict Logic**: For each key in the JSON, find the element with that EXACT label and type the value.\n"
                f" - **ANTI-SLICING**: CLICK the field, WAIT 1 second, then TYPE to prevent first-letter cutoff.\n"
                f" - **ANTI-HANG**: After typing, immediately `Tab` out or click outside to force save.\n"
                f" - **No Guesswork**: DO NOT infer anything. ONLY type what is in the mapping JSON. "
                f"If an exact label does not exist, skip it and do not type into a similar field.\n"
                f" - **Verification**: Once all fields in the JSON are filled, you are DONE.\n\n"
                f" ## 4. COMPLETION\n"
                f" - Verify the form is filled according to the mapping and report success."
            )

            browser = self._create_browser(browser_config)
            agent = self._create_agent(task, browser)
            try:
                result = await agent.run(max_steps=30)
                final_result_str = str(result.final_result())

                # Preserve the existing free-text log for human inspection.
                try:
                    async with aiofiles.open('browser_output.log', 'a') as log_f:
                        await log_f.write(f"\n--- Run Result ({url}) ---\n")
                        await log_f.write(final_result_str)
                        await log_f.write("\n")
                except OSError as log_error:
                    logger.warning(f"Could not write browser_output.log: {log_error}")

                for slot, item in zip(reserved_slots, planned):
                    field_results[slot] = {
                        "label": item["label"],
                        "status": STATUS_TRUNCATED if item["truncated"] else STATUS_FILLED,
                        "original_value": item["original_value"],
                        "filled_value": item["filled_value"],
                        "max_length": item["max_length"],
                        "error": (
                            f"Value exceeded {item['max_length']} characters and was truncated before filling"
                            if item["truncated"]
                            else None
                        ),
                    }
            except Exception as fill_error:
                logger.error(f"Deterministic filling failed: {fill_error}")
                for slot, item in zip(reserved_slots, planned):
                    field_results[slot] = {
                        "label": item["label"],
                        "status": STATUS_FILL_ERROR,
                        "original_value": item["original_value"],
                        "filled_value": None,
                        "max_length": item["max_length"],
                        "error": str(fill_error),
                    }
            finally:
                await browser.kill()
        else:
            # Nothing typeable: do not launch the fill browser at all.
            logger.info("No typeable mapped fields; skipping deterministic fill browser session.")

        # Every field discovered on the page but absent from the mapping gets
        # an explicit "not_mapped" outcome, appended after the mapped fields.
        for page_field in schema_fields:
            if _normalize_label(page_field["label"]) in mapped_labels:
                continue
            field_results.append(
                {
                    "label": page_field["label"],
                    "status": STATUS_NOT_MAPPED,
                    "original_value": None,
                    "filled_value": None,
                    "max_length": page_field.get("max_length"),
                    "error": "Mapping stage did not produce a value for this field",
                }
            )

        return field_results

    # -- Public entry point -------------------------------------------------

    async def fill_form(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        """Fill a form at ``url`` with ``data`` and return a structured result.

        Returns a JSON-serializable dict::

            {
              "success": bool,
              "url": str,
              "failure_reason": str | None,
              "error": str | None,
              "mapping": {label: value, ...},   # raw LLM mapping, in order
              "field_results": [ {label, status, original_value,
                                  filled_value, max_length, error}, ... ]
            }

        The two-pass flow and browser configuration (disable_security,
        CHROME_PATH/BRAVE_PATH, USER_DATA_DIR) are unchanged. If the mapping
        stage returns unparseable JSON or an empty object, the filling stage
        is never started and a failure result is returned after the browser
        session has been closed.
        """
        logger.info(f"Starting Two-Pass browser agent to fill form at {url}")

        if not _BROWSER_USE_AVAILABLE:
            return {
                "success": False,
                "url": url,
                "failure_reason": REASON_UNEXPECTED_ERROR,
                "error": "browser-use is not installed; BrowserExecutor cannot run without the optional dependency",
                "mapping": {},
                "field_results": [],
            }

        # Get browser path and user data dir from env
        config_chrome_path = os.environ.get("CHROME_PATH") or os.environ.get("BRAVE_PATH")
        config_user_data_dir = os.environ.get("USER_DATA_DIR")

        browser_config = BrowserProfile(
            disable_security=True,
            executable_path=config_chrome_path,
            user_data_dir=config_user_data_dir,
            args=[
                "--allow-file-access-from-files",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )

        try:
            # PASS 1: Extract Schema (browser session closed inside the method)
            logger.info("Pass 1: Extracting Form Schema...")
            schema_text = await self._extract_schema(browser_config, url)
            logger.debug(f"Extracted Schema: {schema_text}")
            schema_fields = _parse_schema_fields(schema_text)

            # PASS 1.5: Map Data to Schema
            logger.info("Pass 1.5: Pre-mapping data to schema...")
            try:
                mapping = await self._map_data_to_schema(schema_text, data)
            except MappingError as mapping_error:
                # Do NOT enter the filling stage. Pass 1's browser session was
                # already closed by _extract_schema's finally block.
                logger.error(f"Mapping stage failed: {mapping_error.message}")
                return {
                    "success": False,
                    "url": url,
                    "failure_reason": mapping_error.reason,
                    "error": mapping_error.message,
                    "mapping": {},
                    "field_results": [],
                }

            logger.debug(f"Generated Mapping: {json.dumps(mapping, indent=2)}")

            # PASS 2: Deterministic Filling (opens and closes its own session)
            logger.info("Pass 2: Executing deterministic filling...")
            field_results = await self._fill_form_deterministically_internal(
                browser_config, url, mapping, schema_fields
            )

            failed_statuses = {STATUS_LABEL_NOT_FOUND, STATUS_FILL_ERROR}
            failures = [r for r in field_results if r["status"] in failed_statuses]
            success = not failures
            return {
                "success": success,
                "url": url,
                "failure_reason": REASON_FIELD_FAILURES if failures else None,
                "error": "; ".join(
                    f"{r['label']}: {r['error']}" for r in failures
                ) if failures else None,
                "mapping": mapping,
                "field_results": field_results,
            }

        except Exception as e:
            logger.error(f"Browser agent failed during Two-Pass execution: {e}")
            return {
                "success": False,
                "url": url,
                "failure_reason": REASON_UNEXPECTED_ERROR,
                "error": str(e),
                "mapping": {},
                "field_results": [],
            }
