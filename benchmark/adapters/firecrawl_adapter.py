import json
import sys
import time
import pathlib

import requests
import spacy
from bs4 import BeautifulSoup

from benchmark.adapters.base import BaseAdapter, CrawlResult

_INTELLIGENCE_DIR = pathlib.Path(__file__).parent.parent.parent / "intelligence"
if str(_INTELLIGENCE_DIR) not in sys.path:
    sys.path.insert(0, str(_INTELLIGENCE_DIR))

from intelligence.pipelines.schema import SchemaPipeline

SUPPORTED_ENTITY_TYPES = {"PERSON", "ORG", "GPE", "PRODUCT", "MONEY", "LOC", "FAC", "NORP", "EVENT"}

FIRECRAWL_BASE_URL = "http://localhost:3002"


class FirecrawlAdapter(BaseAdapter):
    """Adapter for self-hosted Firecrawl. Calls the local Docker API.
    Uses SpaCy for entity extraction since Firecrawl has no native NER."""

    def __init__(self, base_url: str = FIRECRAWL_BASE_URL, spacy_model: str = "en_core_web_sm"):
        self._base_url = base_url
        self._spacy_model_name = spacy_model
        self._nlp = None
        self._schema_pipeline: SchemaPipeline | None = None

    def name(self) -> str:
        return "firecrawl"

    def version(self) -> str:
        try:
            resp = requests.get(f"{self._base_url}/v1/health", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("version", "unknown")
        except requests.RequestException:
            pass
        return "unknown"

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
        """Crawl via the Firecrawl self-hosted /v1/scrape endpoint."""
        start = time.monotonic()
        try:
            response = requests.post(
                f"{self._base_url}/v1/scrape",
                json={
                    "url": url,
                    "formats": ["markdown", "html", "rawHtml"],
                },
                timeout=timeout,
            )
            elapsed = time.monotonic() - start
            data = response.json()

            if not data.get("success", False):
                return CrawlResult(
                    url=url, status_code=0, html="", markdown="", text="",
                    metadata=data, elapsed_seconds=elapsed, success=False,
                    error=data.get("error", "Firecrawl scrape failed"),
                )

            page_data = data.get("data", {})
            html = page_data.get("rawHtml", page_data.get("html", ""))
            markdown = page_data.get("markdown", "")
            text = self._html_to_text(html)
            status_code = page_data.get("metadata", {}).get("statusCode", 200)

            return CrawlResult(
                url=url,
                status_code=status_code,
                html=html,
                markdown=markdown,
                text=text,
                metadata=page_data.get("metadata", {}),
                elapsed_seconds=elapsed,
                success=True,
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
        """Run SpaCy NER on extracted text (Firecrawl has no native NER)."""
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
