"""
Competitive Intelligence Monitor using CocoIndex

Tracks competitor mentions across the web using Tavily AI Search.
Extracts competitive events using DeepSeek LLM: product launches, partnerships,
funding rounds, key hires, and strategic moves.
"""

import cocoindex
import os
import sys
import functools
import re
from collections.abc import Mapping
from psycopg_pool import ConnectionPool
from datetime import timedelta, datetime
from typing import Any, AsyncIterator, NamedTuple
import dataclasses

from tavily import TavilyClient
from cocoindex.op import (
    NON_EXISTENCE,
    SourceSpec,
    NO_ORDINAL,
    source_connector,
    PartialSourceRow,
    PartialSourceRowData,
)

import collection_outcomes


# ============================================================================
# Data Models
# ============================================================================

@dataclasses.dataclass
class CompetitiveEvent:
    """A competitive intelligence event extracted from text.

    Examples:
    - Product Launch: "OpenAI released GPT-5 with multimodal capabilities"
    - Partnership: "Anthropic partnered with Google Cloud for enterprise AI"
    - Funding: "Mistral AI raised $400M Series B led by Andreessen Horowitz"
    - Key Hire: "Former Meta AI director joined Cohere as Chief Scientist"
    - Strategic Move: "Microsoft acquired AI startup Inflection for $650M"
    """

    event_type: str  # "product_launch", "partnership", "funding", "key_hire", "acquisition", "other"
    competitor: str  # Company name (e.g., "OpenAI", "Anthropic", "Google AI")
    description: str  # Brief description of the event
    significance: str  # "high", "medium", "low" - based on market impact
    related_companies: list[str]  # Other companies mentioned (partners, acquirers, etc.)


class _ArticleKey(NamedTuple):
    """Row key type for articles."""
    url: str


@dataclasses.dataclass
class _Article:
    """Article value type."""
    title: str
    content: str
    source: str
    published_at: datetime
    score: float


# ============================================================================
# Data Sources
# ============================================================================

class TavilySearchSource(SourceSpec):
    """Fetches competitive intelligence using Tavily AI Search API."""
    competitor: str
    days_back: int = 7
    max_results: int = 10
    query_terms: str = "(funding OR partnership OR product launch OR acquisition OR executive hire)"


@source_connector(
    spec_cls=TavilySearchSource,
    key_type=_ArticleKey,
    value_type=_Article,
)
class TavilySearchConnector:
    """Custom source connector for Tavily AI Search.

    One connector instance serves one competitor.  Search problems are recorded
    as that competitor's outcome instead of being raised, so a single failing
    competitor cannot abort the update or drop the other competitors' data.
    """

    _spec: TavilySearchSource
    _api_key: str
    _articles: dict[str, _Article]
    _ordinals: dict[str, Any]

    # The engine learns values from list() and only calls get_value() for rows
    # it still needs; a bounded cache keeps memory flat for large result sets.
    _MAX_CACHED_VALUES = 512

    def __init__(self, spec: TavilySearchSource, api_key: str):
        self._spec = spec
        self._api_key = api_key
        self._articles = {}
        self._ordinals = {}

    @staticmethod
    async def create(spec: TavilySearchSource) -> "TavilySearchConnector":
        """Create a Tavily connector from the spec."""
        api_key = os.getenv("TAVILY_API_KEY", "")
        if not api_key:
            raise ValueError("TAVILY_API_KEY environment variable is required")
        return TavilySearchConnector(spec, api_key)

    def _search_query(self) -> str:
        return f"{self._spec.competitor} AND {self._spec.query_terms}"

    def _search(self) -> list[Mapping[str, Any]]:
        """Run the Tavily search and return the raw result mappings."""
        client = TavilyClient(api_key=self._api_key)
        response = client.search(
            query=self._search_query(),
            search_depth="advanced",
            max_results=self._spec.max_results,
            include_raw_content=True,
            include_domains=[],
            exclude_domains=[],
        )
        results = response.get("results") if isinstance(response, Mapping) else None
        if not results:
            return []
        return [result for result in results if isinstance(result, Mapping)]

    @staticmethod
    def _published_at(pub_date: Any) -> tuple[datetime | None, Any]:
        """Parse a Tavily published date.

        Returns the parsed datetime (``None`` when the field is missing or
        malformed) and the ordinal to expose to CocoIndex.  A missing date is
        not an error: the article is still ingested with ``NO_ORDINAL``, which
        is the connector's existing ordering fallback.
        """
        if isinstance(pub_date, datetime):
            parsed = pub_date
        elif isinstance(pub_date, (int, float)):
            try:
                parsed = datetime.fromtimestamp(pub_date)
            except (OverflowError, OSError, ValueError):
                parsed = None
        elif isinstance(pub_date, str) and pub_date.strip():
            try:
                parsed = datetime.fromisoformat(pub_date.strip())
            except ValueError:
                parsed = None
        else:
            parsed = None

        if parsed is None:
            return None, NO_ORDINAL
        try:
            return parsed, int(parsed.timestamp())
        except (OverflowError, OSError, ValueError):
            return None, NO_ORDINAL

    @staticmethod
    def _article_from_result(result: Mapping[str, Any], published_at: datetime | None) -> _Article:
        return _Article(
            title=result.get("title") or "",
            content=result.get("raw_content") or result.get("content") or "",
            source=result.get("domain") or "Unknown",
            published_at=published_at or datetime.now(),
            score=result.get("score", 0.0) or 0.0,
        )

    def _remember(self, results: list[Mapping[str, Any]]) -> list[tuple[str, _Article, Any]]:
        """Cache usable results by URL and return ``(url, article, ordinal)``."""
        usable: list[tuple[str, _Article, Any]] = []
        for result in results:
            url = result.get("url")
            if not url:
                continue
            published_at, ordinal = self._published_at(result.get("published_date"))
            article = self._article_from_result(result, published_at)
            self._articles[url] = article
            self._ordinals[url] = ordinal
            usable.append((url, article, ordinal))

        if len(self._articles) > self._MAX_CACHED_VALUES:
            evicted = list(self._articles)[: len(self._articles) - self._MAX_CACHED_VALUES]
            for url in evicted:
                self._articles.pop(url, None)
                self._ordinals.pop(url, None)
        return usable

    def _record_outcome(
        self,
        status: str,
        *,
        exc: BaseException | None = None,
        result_count: int = 0,
        missing_published_date_count: int = 0,
        phase: str = "list",
    ) -> None:
        """Persist this competitor's outcome for the parent process to read.

        Recording is best-effort: a bookkeeping failure must not lose the
        articles that were actually fetched.  It is reported on stderr, which
        the parent already captures.
        """
        competitor = self._spec.competitor
        reason = collection_outcomes.summarize_reason(exc)
        if exc is not None:
            print(
                f"[tavily] competitor {competitor!r} {phase} failed: {reason}",
                file=sys.stderr,
            )
        try:
            collection_outcomes.write_outcome(
                competitor,
                status,
                failure_reason=reason,
                error_type=type(exc).__name__ if exc is not None else None,
                result_count=result_count,
                missing_published_date_count=missing_published_date_count,
                phase=phase,
                query=self._search_query(),
            )
        except Exception as write_exc:  # noqa: BLE001 - bookkeeping must not lose data
            print(
                f"[tavily] failed to record collection outcome for {competitor!r}: {write_exc}",
                file=sys.stderr,
            )

    async def list(
        self,
    ) -> AsyncIterator[PartialSourceRow[_ArticleKey, _Article]]:
        """List articles from Tavily search.

        Outcomes:
        - request error -> ``failed`` outcome, no rows yielded for this competitor
        - no usable results -> ``empty`` outcome, no rows yielded
        - otherwise -> ``collected`` outcome, rows yielded (values included so
          the engine does not need a second search per article)

        Rows without a ``published_date`` are still yielded with ``NO_ORDINAL``.

        CocoIndex treats a source's row set as authoritative, so a competitor
        that yields no rows contributes nothing this round.  That is the price
        of not fabricating data for a failed search: the other competitors are
        still indexed, and the recorded ``failed`` outcome tells the caller the
        gap was a request problem rather than "no longer covered".
        """
        try:
            results = self._search()
        except Exception as exc:  # noqa: BLE001 - isolate one competitor's failure
            self._record_outcome(collection_outcomes.STATUS_FAILED, exc=exc)
            return

        usable = self._remember(results)
        missing_dates = sum(1 for _, _, ordinal in usable if ordinal == NO_ORDINAL)

        if not usable:
            self._record_outcome(
                collection_outcomes.STATUS_EMPTY,
                result_count=0,
                missing_published_date_count=missing_dates,
            )
            return

        self._record_outcome(
            collection_outcomes.STATUS_COLLECTED,
            result_count=len(usable),
            missing_published_date_count=missing_dates,
        )
        for url, article, ordinal in usable:
            yield PartialSourceRow(
                key=_ArticleKey(url=url),
                data=PartialSourceRowData(ordinal=ordinal, value=article),
            )

    async def get_value(
        self, key: _ArticleKey
    ) -> PartialSourceRowData[_Article]:
        """Get a specific article by URL.

        Values fetched by ``list()`` are reused.  If a fallback search is
        needed and fails, the failure is recorded for this competitor and the
        row is reported as missing rather than aborting the whole update.
        """
        if key.url in self._articles:
            return PartialSourceRowData(
                value=self._articles[key.url],
                ordinal=self._ordinals.get(key.url, NO_ORDINAL),
            )

        try:
            results = self._search()
        except Exception as exc:  # noqa: BLE001 - isolate one competitor's failure
            self._record_outcome(
                collection_outcomes.STATUS_FAILED, exc=exc, phase="get_value"
            )
            return PartialSourceRowData(
                value=NON_EXISTENCE,
                ordinal=NO_ORDINAL,
                content_version_fp=None,
            )

        self._remember(results)
        if key.url in self._articles:
            return PartialSourceRowData(
                value=self._articles[key.url],
                ordinal=self._ordinals.get(key.url, NO_ORDINAL),
            )

        return PartialSourceRowData(
            value=NON_EXISTENCE,
            ordinal=NO_ORDINAL,
            content_version_fp=None,
        )

    def provides_ordinal(self) -> bool:
        """Indicate that this source provides ordinal information."""
        return True


# ============================================================================
# Pipeline Definition
# ============================================================================

def _scope_field_name(prefix: str, value: str) -> str:
    """Build a CocoIndex-safe scope field name from user-provided text."""
    clean = re.sub(r"\W+", "_", value.strip())
    if not clean or clean[0].isdigit():
        clean = f"_{clean}"
    return f"{prefix}_{clean}"


@cocoindex.flow_def(name="CompetitiveIntelligence")
def competitive_intelligence_flow(
    flow_builder: cocoindex.FlowBuilder, data_scope: cocoindex.DataScope
) -> None:
    """Main pipeline for competitive intelligence monitoring."""

    # Environment configuration
    competitors = os.getenv("COMPETITORS", "OpenAI,Anthropic").split(",")
    refresh_interval = int(os.getenv("REFRESH_INTERVAL_SECONDS", "3600"))
    search_days_back = int(os.getenv("SEARCH_DAYS_BACK", "7"))
    max_results = int(os.getenv("MAX_RESULTS_PER_COMPETITOR", "10"))
    query_terms = os.getenv(
        "EVENT_QUERY",
        "(funding OR partnership OR product launch OR acquisition OR executive hire)",
    )

    # Add Tavily search source for each competitor
    for competitor in competitors:
        competitor_clean = competitor.strip()
        data_scope[_scope_field_name("articles", competitor_clean)] = flow_builder.add_source(
            TavilySearchSource(
                competitor=competitor_clean,
                days_back=search_days_back,
                max_results=max_results,
                query_terms=query_terms,
            ),
            refresh_interval=timedelta(seconds=refresh_interval),
        )

    articles_index = data_scope.add_collector()
    events_index = data_scope.add_collector()

    # Process each competitor's articles
    for competitor in competitors:
        competitor_clean = competitor.strip()
        articles = data_scope[_scope_field_name("articles", competitor_clean)]

        with articles.row() as article:
            # Extract competitive events from articles using GPT-4o-mini via OpenRouter
            article["events"] = article["content"].transform(
                cocoindex.functions.ExtractByLlm(
                    llm_spec=cocoindex.LlmSpec(
                        api_type=cocoindex.LlmApiType.OPENAI,
                        model="openai/gpt-4o-mini",
                        address="https://openrouter.ai/api/v1",
                    ),
                    output_type=list[CompetitiveEvent],
                    instruction=(
                        "Extract competitive intelligence events from this article. "
                        "Focus on: product launches, partnerships, funding rounds, key hires, "
                        "acquisitions, and other strategic moves. Return an empty list if no events found."
                    ),
                )
            )

            # Collect articles
            articles_index.collect(
                id=article["url"],
                title=article["title"],
                content=article["content"],
                url=article["url"],
                source=article["source"],
                published_at=article["published_at"],
                score=article["score"],
            )

            # Collect extracted events
            with article["events"].row() as event:
                events_index.collect(
                    article_id=article["url"],
                    event_type=event["event_type"],
                    competitor=event["competitor"],
                    description=event["description"],
                    significance=event["significance"],
                    related_companies=event["related_companies"],
                )

    # Export to PostgreSQL
    articles_index.export(
        "intel_articles",
        cocoindex.targets.Postgres(),
        primary_key_fields=["id"],
    )

    events_index.export(
        "intel_events",
        cocoindex.targets.Postgres(),
        primary_key_fields=["article_id", "event_type", "competitor", "description"],
    )


# ============================================================================
# Query Handlers
# ============================================================================

@functools.cache
def connection_pool() -> ConnectionPool:
    """Get a connection pool to the database."""
    return ConnectionPool(os.environ["COCOINDEX_DATABASE_URL"])


@competitive_intelligence_flow.query_handler()
def search_by_competitor(competitor: str, event_type: str | None = None, limit: int = 20) -> cocoindex.QueryOutput:
    """Find recent competitive intelligence about a specific competitor."""
    events_table = cocoindex.utils.get_target_default_name(
        competitive_intelligence_flow, "intel_events"
    )
    articles_table = cocoindex.utils.get_target_default_name(
        competitive_intelligence_flow, "intel_articles"
    )

    with connection_pool().connection() as conn:
        with conn.cursor() as cur:
            sql = f"""
                SELECT
                    e.competitor,
                    e.event_type,
                    e.description,
                    e.significance,
                    e.related_companies,
                    a.title,
                    a.url,
                    a.source,
                    a.published_at
                FROM {events_table} e
                JOIN {articles_table} a ON e.article_id = a.id
                WHERE LOWER(e.competitor) LIKE LOWER(%s)
            """
            params = [f"%{competitor}%"]

            if event_type:
                sql += " AND e.event_type = %s"
                params.append(event_type)

            sql += " ORDER BY a.published_at DESC LIMIT %s"
            params.append(limit)

            cur.execute(sql, params)

            results = []
            for row in cur.fetchall():
                results.append({
                    "competitor": row[0],
                    "event_type": row[1],
                    "description": row[2],
                    "significance": row[3],
                    "related_companies": row[4],
                    "article_title": row[5],
                    "url": row[6],
                    "source": row[7],
                    "published_at": row[8].isoformat() if row[8] else None,
                })

            return cocoindex.QueryOutput(results=results)


@competitive_intelligence_flow.query_handler()
def get_trending_competitors(days: int = 7) -> cocoindex.QueryOutput:
    """Get competitors ranked by recent news volume and significance."""
    events_table = cocoindex.utils.get_target_default_name(
        competitive_intelligence_flow, "intel_events"
    )
    articles_table = cocoindex.utils.get_target_default_name(
        competitive_intelligence_flow, "intel_articles"
    )

    with connection_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    e.competitor,
                    COUNT(*) as total_events,
                    SUM(CASE
                        WHEN e.significance = 'high' THEN 3
                        WHEN e.significance = 'medium' THEN 2
                        ELSE 1
                    END) as weighted_score,
                    COUNT(DISTINCT e.event_type) as event_types,
                    array_agg(DISTINCT e.event_type) as events
                FROM {events_table} e
                JOIN {articles_table} a ON e.article_id = a.id
                WHERE a.published_at >= NOW() - (%s * INTERVAL '1 day')
                GROUP BY e.competitor
                ORDER BY weighted_score DESC
                """,
                (days,),
            )

            results = []
            for row in cur.fetchall():
                results.append({
                    "competitor": row[0],
                    "total_events": row[1],
                    "weighted_score": row[2],
                    "event_types_count": row[3],
                    "event_types": row[4],
                })

            return cocoindex.QueryOutput(results=results)
