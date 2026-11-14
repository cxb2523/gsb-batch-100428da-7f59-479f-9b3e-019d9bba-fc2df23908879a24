import sys
import time
import pathlib

import requests
from bs4 import BeautifulSoup

# Add the intelligence package to the path so we can import its pipelines
_INTELLIGENCE_DIR = pathlib.Path(__file__).parent.parent.parent / "intelligence"
if str(_INTELLIGENCE_DIR) not in sys.path:
    sys.path.insert(0, str(_INTELLIGENCE_DIR))

from intelligence.pipelines.entity import EntityPipeline
from intelligence.pipelines.schema import SchemaPipeline

from benchmark.adapters.base import BaseAdapter, CrawlResult


class PhantombotAdapter(BaseAdapter):
    """Adapter for Phantombot in direct mode — uses intelligence pipelines directly."""

    def __init__(self):
        self._entity_pipeline: EntityPipeline | None = None
        self._schema_pipeline: SchemaPipeline | None = None

    def name(self) -> str:
        return "phantombot"

    def version(self) -> str:
        return "0.1.0"

    def setup(self) -> None:
        self._entity_pipeline = EntityPipeline()
        self._schema_pipeline = SchemaPipeline()

    def teardown(self) -> None:
        self._entity_pipeline = None
        self._schema_pipeline = None

    def _ensure_pipelines(self) -> None:
        """Lazily initialize pipelines if setup() was not called."""
        if self._entity_pipeline is None:
            self._entity_pipeline = EntityPipeline()
        if self._schema_pipeline is None:
            self._schema_pipeline = SchemaPipeline()

    def crawl(self, url: str, timeout: int = 30) -> CrawlResult:
        start = time.monotonic()
        try:
            response = requests.get(url, timeout=timeout, headers={
                "User-Agent": "PhantombotBenchmark/1.0",
            })
            elapsed = time.monotonic() - start
            html = response.text
            text = self._html_to_text(html)
            markdown = self._html_to_markdown(html)
            return CrawlResult(
                url=url,
                status_code=response.status_code,
                html=html,
                markdown=markdown,
                text=text,
                metadata={"content_type": response.headers.get("content-type", "")},
                elapsed_seconds=elapsed,
                success=response.status_code == 200,
                error=None if response.status_code == 200 else f"HTTP {response.status_code}",
            )
        except requests.Timeout:
            elapsed = time.monotonic() - start
            return CrawlResult(
                url=url, status_code=0, html="", markdown="", text="",
                metadata={}, elapsed_seconds=elapsed, success=False,
                error="Timeout exceeded",
            )
        except requests.RequestException as e:
            elapsed = time.monotonic() - start
            return CrawlResult(
                url=url, status_code=0, html="", markdown="", text="",
                metadata={}, elapsed_seconds=elapsed, success=False,
                error=str(e),
            )

    def extract_entities(self, html: str, url: str) -> list[dict]:
        self._ensure_pipelines()
        entities = self._entity_pipeline.extract(html, url)
        return [{"text": e.entity_text, "type": e.entity_type} for e in entities]

    def extract_schemas(self, html: str, url: str) -> list[dict]:
        self._ensure_pipelines()
        schemas = self._schema_pipeline.extract(html, url)
        return [{"type": s.schema_type, "data": s.schema_data} for s in schemas]

    def _html_to_text(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)

    def _html_to_markdown(self, html: str) -> str:
        """Simple HTML-to-markdown conversion for benchmark purposes."""
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        lines = []
        for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
            tag_name = element.name
            text = element.get_text(strip=True)
            if not text:
                continue
            if tag_name.startswith("h"):
                level = int(tag_name[1])
                lines.append(f"{'#' * level} {text}")
            elif tag_name == "li":
                lines.append(f"- {text}")
            else:
                lines.append(text)
        return "\n\n".join(lines)
