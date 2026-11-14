"""Portal source registry: config loading, engine dispatch and parallel fan-out."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from typing import Any
from collections.abc import Iterable

import paths
from gemsentry.constants import logger
from gemsentry.sources.base import BaseAdapter, UnsupportedAdapter
from gemsentry.sources.bhel import BHELAdapter
from gemsentry.sources.gepnic import GePNICAdapter
from gemsentry.sources.isro import ISROAdapter

# engine name -> adapter class. Adding a portal family means adding one entry.
ENGINES: dict[str, type[BaseAdapter]] = {
    "gepnic": GePNICAdapter,
    "isro": ISROAdapter,
    "bhel": BHELAdapter,
}

# GeM is not fanned out here: it has its own pipeline (cookie/CSRF handshake,
# paginated JSON API, PDF download and scoring) driven from gemsentry.pipeline.
NATIVE_ENGINES = frozenset({"gem"})

DEFAULT_MAX_WORKERS = 4
DEFAULT_SOURCE_TIMEOUT = 90

# Per-source fates of one fan-out. ``success`` and ``empty`` both mean the
# adapter returned normally; ``empty`` is just a zero-row success so callers
# can distinguish "the portal answered with nothing" from "we never heard back".
SOURCE_STATUS_SUCCESS = "success"
SOURCE_STATUS_EMPTY = "empty"
SOURCE_STATUS_FAILED = "failed"
SOURCE_STATUS_TIMEOUT = "timeout"
SOURCE_STATUS_SKIPPED = "skipped"

# Fates that mean the source produced no usable result this round.
SOURCE_PROBLEM_STATUSES = frozenset(
    {SOURCE_STATUS_FAILED, SOURCE_STATUS_TIMEOUT, SOURCE_STATUS_SKIPPED}
)


@dataclass
class SourceOutcome:
    """What happened to one configured source during a single fan-out."""

    source_id: str
    source_name: str
    status: str
    count: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FetchResult:
    """Deduplicated tender list plus the per-source outcome ledger.

    ``tenders`` is the exact list :meth:`SourceRegistry.fetch_from_all_active`
    has always returned, so existing callers can ignore the ledger entirely.
    """

    tenders: list[dict[str, Any]] = field(default_factory=list)
    outcomes: list[SourceOutcome] = field(default_factory=list)

    @property
    def has_problems(self) -> bool:
        return any(o.status in SOURCE_PROBLEM_STATUSES for o in self.outcomes)

    def outcomes_as_dicts(self) -> list[dict[str, Any]]:
        return [o.to_dict() for o in self.outcomes]


class SourceRegistry:
    """Loads ``config/sources.json`` and runs the adapters that exist.

    Sources whose engine has no implementation are kept visible (the dashboard
    lists them) but are never dispatched, so the run does not spawn a dozen
    workers that can only return an empty list.
    """

    def __init__(self, sources_path: str = paths.SOURCES_PATH):
        self.sources_path = sources_path
        self.sources: list[dict[str, Any]] = []
        self.adapters: dict[str, BaseAdapter] = {}
        self._lock = threading.Lock()
        # Per-source ledger from the most recent fan-out, keyed by source id.
        self.last_fetch_outcomes: dict[str, SourceOutcome] = {}
        self.reload_sources()

    # -- config ----------------------------------------------------------

    def reload_sources(self) -> None:
        if not os.path.exists(self.sources_path):
            logger.warning("Sources config not found at %s", self.sources_path)
            self.sources, self.adapters = [], {}
            return
        try:
            with open(self.sources_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.error("Failed to load sources from %s: %s", self.sources_path, exc)
            return

        self.sources = data.get("sources", [])
        self._build_adapters()
        logger.info(
            "Loaded %d source(s): %d runnable, %d native, %d awaiting an adapter",
            len(self.sources), len(self.runnable_adapters()),
            len(self._by_engine(NATIVE_ENGINES)), len(self.unsupported_sources()),
        )

    def _build_adapters(self) -> None:
        adapters: dict[str, BaseAdapter] = {}
        for source in self.sources:
            source_id = source.get("id")
            if not source_id:
                logger.warning("Skipping source with no id: %r", source.get("name"))
                continue
            engine = (source.get("engine") or "").lower()
            adapter_cls = ENGINES.get(engine, UnsupportedAdapter)
            try:
                adapters[source_id] = adapter_cls(source)
            except Exception as exc:
                logger.error("Could not build adapter for '%s': %s", source_id, exc)
        self.adapters = adapters

    def _save_sources(self) -> None:
        tmp_path = f"{self.sources_path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "sources": self.sources}, handle, indent=2)
            os.replace(tmp_path, self.sources_path)
            logger.info("Saved updated sources configuration")
        except OSError as exc:
            logger.error("Failed to save sources configuration: %s", exc)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    # -- queries ---------------------------------------------------------

    def get_all_sources(self) -> list[dict[str, Any]]:
        """Every configured source, annotated with its runtime capability."""
        annotated = []
        for source in self.sources:
            adapter = self.adapters.get(source.get("id"))
            engine = (source.get("engine") or "").lower()
            annotated.append({
                **source,
                "supported": bool(adapter and getattr(adapter, "implemented", False)),
                "native": engine in NATIVE_ENGINES,
            })
        return annotated

    def get_active_sources(self) -> list[dict[str, Any]]:
        return [s for s in self.sources if s.get("enabled", True)]

    def _by_engine(self, engines: Iterable[str]) -> list[dict[str, Any]]:
        wanted = set(engines)
        return [s for s in self.sources if (s.get("engine") or "").lower() in wanted]

    def unsupported_sources(self) -> list[dict[str, Any]]:
        """Enabled sources whose engine has no adapter and isn't native."""
        return [
            s for s in self.get_active_sources()
            if (s.get("engine") or "").lower() not in ENGINES
            and (s.get("engine") or "").lower() not in NATIVE_ENGINES
        ]

    def runnable_adapters(self) -> list[BaseAdapter]:
        """Enabled sources that have a real adapter to run."""
        enabled = {s.get("id") for s in self.get_active_sources()}
        return [
            adapter for source_id, adapter in self.adapters.items()
            if source_id in enabled and getattr(adapter, "implemented", False)
        ]

    def toggle_source(self, source_id: str, enabled: bool) -> bool:
        with self._lock:
            for source in self.sources:
                if source.get("id") == source_id:
                    if enabled and (source.get("engine") or "").lower() not in {*ENGINES, *NATIVE_ENGINES}:
                        raise ValueError("This portal has no working adapter and cannot be enabled.")
                    source["enabled"] = bool(enabled)
                    self._save_sources()
                    self._build_adapters()
                    return True
        return False

    # -- fan-out ---------------------------------------------------------

    def fetch_from_all_active(
        self,
        keywords: list[str],
        max_workers: int = DEFAULT_MAX_WORKERS,
        max_pages: int = 5,
        timeout: float | None = DEFAULT_SOURCE_TIMEOUT,
    ) -> list[dict[str, Any]]:
        """Query every runnable portal in parallel and merge the results.

        Backwards-compatible list interface; the per-source outcome ledger is
        available via :meth:`get_last_fetch_outcomes` (or
        :meth:`fetch_with_outcomes`). A failed or timed-out portal never raises
        here -- its tenders are simply absent and its fate is recorded.

        Results are de-duplicated on ``tender_id`` -- the same tender is often
        published on both a parent portal (CPPP) and the buying organisation's
        own site. First successful adapter to return a tender keeps it.
        """
        return self.fetch_with_outcomes(
            keywords, max_workers=max_workers, max_pages=max_pages, timeout=timeout
        ).tenders

    def fetch_with_outcomes(
        self,
        keywords: list[str],
        max_workers: int = DEFAULT_MAX_WORKERS,
        max_pages: int = 5,
        timeout: float | None = DEFAULT_SOURCE_TIMEOUT,
    ) -> FetchResult:
        """Fan out with an explicit per-source outcome ledger.

        Each adapter gets one :class:`SourceOutcome`:

        * ``success`` / ``empty`` -- returned normally, with the row count;
        * ``failed`` -- raised an exception (only that source is affected);
        * ``timeout`` -- still running when the shared deadline elapsed and
          now cancelled, so it cannot hold up the portals that did finish;
        * ``skipped`` -- enabled source with no runnable adapter.

        Returns a :class:`FetchResult` whose ``tenders`` list is de-duplicated
        on ``tender_id`` (first arrival wins).
        """
        adapters = self.runnable_adapters()
        skipped = self.unsupported_sources()

        outcomes: dict[str, SourceOutcome] = {}
        if skipped:
            logger.info(
                "Skipping %d source(s) with no adapter yet: %s",
                len(skipped), ", ".join(sorted(s.get("id", "?") for s in skipped)),
            )
            for source in skipped:
                source_id = source.get("id", "?")
                reason = source.get("blocked_reason") or "no adapter implemented for this engine"
                outcomes[source_id] = SourceOutcome(
                    source_id=source_id,
                    source_name=source.get("name", source_id),
                    status=SOURCE_STATUS_SKIPPED,
                    reason=reason,
                )
                logger.info("[%s] skipped: %s", source_id, reason)

        if not adapters:
            logger.info("No runnable portal adapters enabled; multi-source fetch skipped.")
            self.last_fetch_outcomes = outcomes
            return FetchResult(tenders=[], outcomes=list(outcomes.values()))

        logger.info("Querying %d portal(s) in parallel: %s",
                    len(adapters), ", ".join(a.source_id for a in adapters))

        merged: dict[str, dict[str, Any]] = {}
        pool = ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(adapters))))
        try:
            futures = {
                pool.submit(self._run_adapter, adapter, keywords, max_pages): adapter
                for adapter in adapters
            }
            # One shared deadline for the whole fan-out: a single unresponsive
            # portal must not hold up the scrape run.
            done, pending = wait(futures, timeout=timeout)

            for future in pending:
                adapter = futures[future]
                reason = f"timed out after {timeout}s"
                logger.error("[%s] %s", adapter.source_id, reason)
                outcomes[adapter.source_id] = SourceOutcome(
                    source_id=adapter.source_id,
                    source_name=adapter.source_name,
                    status=SOURCE_STATUS_TIMEOUT,
                    reason=reason,
                )
                # Cancel what can still be cancelled; a thread already running
                # keeps running but its result is never collected.
                future.cancel()

            for future in done:
                adapter = futures[future]
                try:
                    tenders = future.result()
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    logger.error("[%s] worker failed: %s", adapter.source_id, reason)
                    outcomes[adapter.source_id] = SourceOutcome(
                        source_id=adapter.source_id,
                        source_name=adapter.source_name,
                        status=SOURCE_STATUS_FAILED,
                        reason=reason,
                    )
                    continue
                for tender in tenders:
                    # First portal to return a tender_id keeps the row.
                    merged.setdefault(tender.get("tender_id"), tender)
                outcomes[adapter.source_id] = SourceOutcome(
                    source_id=adapter.source_id,
                    source_name=adapter.source_name,
                    status=SOURCE_STATUS_SUCCESS if tenders else SOURCE_STATUS_EMPTY,
                    count=len(tenders),
                )
        finally:
            # wait=False: timed-out workers may still be mid-request and must
            # not block the caller from receiving the portals that finished.
            pool.shutdown(wait=False, cancel_futures=True)

        # Deterministic ledger order: dispatched adapters first, then skips.
        ordered = [outcomes[a.source_id] for a in adapters if a.source_id in outcomes]
        ordered.extend(
            outcomes[s.get("id", "?")]
            for s in skipped
            if s.get("id", "?") in outcomes
        )
        self.last_fetch_outcomes = outcomes

        succeeded = sum(1 for o in ordered if o.status in (SOURCE_STATUS_SUCCESS, SOURCE_STATUS_EMPTY))
        logger.info(
            "Multi-source aggregation complete: %d unique tender(s) from %d/%d source(s) "
            "(%d failed, %d timed out, %d skipped).",
            len(merged), succeeded, len(ordered),
            sum(1 for o in ordered if o.status == SOURCE_STATUS_FAILED),
            sum(1 for o in ordered if o.status == SOURCE_STATUS_TIMEOUT),
            sum(1 for o in ordered if o.status == SOURCE_STATUS_SKIPPED),
        )
        return FetchResult(tenders=list(merged.values()), outcomes=ordered)

    def get_last_fetch_outcomes(self) -> list[SourceOutcome]:
        """Per-source ledger from the most recent fan-out (empty if never run)."""
        return list(self.last_fetch_outcomes.values())

    @staticmethod
    def _run_adapter(adapter: BaseAdapter, keywords: list[str], max_pages: int) -> list[dict[str, Any]]:
        tenders = adapter.fetch_active_tenders(keywords, max_pages=max_pages)
        logger.info("[%s] returned %d tender(s)", adapter.source_id, len(tenders))
        return tenders
