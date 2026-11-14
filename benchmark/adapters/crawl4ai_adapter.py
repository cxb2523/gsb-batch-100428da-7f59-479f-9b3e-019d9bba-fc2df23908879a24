import json
import sys
import time
import pathlib

import requests
import spacy
from bs4 import BeautifulSoup

from benchmark.adapters.base import BaseAdapter, CrawlResult

# Add intelligence to path for SchemaPipeline reuse
_INTELLIGENCE_DIR = pathlib.Path(__file__).parent.parent.parent / "intelligence"
if str(_INTELLIGENCE_DIR) not in sys.path:
    sys.path.insert(0, str(_INTELLIGENCE_DIR))

from intelligence.pipelines.schema import SchemaPipeline

SUPPORTED_ENTITY_TYPES = {"PERSON", "ORG", "GPE", "PRODUCT", "MONEY", "LOC", "FAC", "NORP", "EVENT"}


class Crawl4AIAdapter(BaseAdapter):
    """Adapter for Crawl4AI. Uses requests for crawling (sync fallback)
    and SpaCy for entity extraction since Crawl4AI has no native NER."""

    def __init__(self, spacy_model: str = "en_core_web_sm"):
        self._spacy_model_name = spacy_model
        self._nlp = None
        self._schema_pipeline: SchemaPipeline | None = None

    def name(self) -> str:
        return "crawl4ai"

    def version(self) -> str:
        try:
            from crawl4ai.__version__ import __version__
            return str(__version__)
        except (ImportError, AttributeError):
            return "not-installed"

    def setup(self) -> None:
        self._nlp = spacy.load(self._spacy_model_name)
        self._schema_pipeline = SchemaPipeline()

    def teardown(self) -> None:
        self._nlp = None
        self._schema_pipeline = None

    def _ensure_loaded(self) -> None:
        if self._nlp is None:
            self._nlp = spacy.load(self._spacy_model_name)
        if self._schema_pipeline is None:
            self._schema_pipeline = SchemaPipeline()

    def crawl(self, url: str, timeout: int = 30) -> CrawlResult:
        """Crawl using requests as a synchronous fallback.
        In a full benchmark run, the runner can invoke the async Crawl4AI API
        separately. This keeps the adapter testable without Playwright."""
        start = time.monotonic()
        try:
            response = requests.get(url, timeout=timeout, headers={
                "User-Agent": "Crawl4AIBenchmark/1.0",
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
        """Run SpaCy NER on extracted text (Crawl4AI has no native NER)."""
        self._ensure_loaded()
        if not html or not html.strip():
            return []
        text = self._html_to_text(html)
        if not text.strip():
            return []
        doc = self._nlp(text)
        entities = []
        seen = set()
        for ent in doc.ents:
            if ent.label_ not in SUPPORTED_ENTITY_TYPES:
                continue
            key = (ent.text.strip(), ent.label_)
            if key in seen:
                continue
            seen.add(key)
            entities.append({"text": ent.text.strip(), "type": ent.label_})
        return entities

    def extract_schemas(self, html: str, url: str) -> list[dict]:
        """Use the same SchemaPipeline as Phantombot for fair comparison."""
        self._ensure_loaded()
        schemas = self._schema_pipeline.extract(html, url)
        return [{"type": s.schema_type, "data": s.schema_data} for s in schemas]

    def _html_to_text(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)

    def _html_to_markdown(self, html: str) -> str:
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
