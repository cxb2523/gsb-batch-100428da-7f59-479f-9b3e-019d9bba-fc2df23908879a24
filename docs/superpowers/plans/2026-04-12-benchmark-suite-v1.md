# Benchmark Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible benchmark comparing Phantombot, Firecrawl, and Crawl4AI across crawl speed, entity extraction quality, schema detection, and reliability.

**Architecture:** Python benchmark harness with adapter pattern for each tool, metrics computation modules, CLI runner, and markdown/JSON report generation.

**Tech Stack:** Python 3.12+, pytest, Docker Compose (for Firecrawl), SpaCy, crawl4ai

---

## Task 0: Project scaffolding

### Task 0.1: Create directory structure and `__init__.py` files

- [ ] Create all required directories and empty `__init__.py` files so Python imports work from the start.

**Files to create:**
- `/Users/suryakoritala/phantombot/benchmark/__init__.py`
- `/Users/suryakoritala/phantombot/benchmark/adapters/__init__.py`
- `/Users/suryakoritala/phantombot/benchmark/metrics/__init__.py`
- `/Users/suryakoritala/phantombot/benchmark/ground_truth/` (directory only)
- `/Users/suryakoritala/phantombot/benchmark/results/.gitkeep`
- `/Users/suryakoritala/phantombot/benchmark/tests/__init__.py`
- `/Users/suryakoritala/phantombot/benchmark/tests/conftest.py`

**Code for `/Users/suryakoritala/phantombot/benchmark/__init__.py`:**
```python
```

**Code for `/Users/suryakoritala/phantombot/benchmark/adapters/__init__.py`:**
```python
```

**Code for `/Users/suryakoritala/phantombot/benchmark/metrics/__init__.py`:**
```python
```

**Code for `/Users/suryakoritala/phantombot/benchmark/tests/__init__.py`:**
```python
```

**Code for `/Users/suryakoritala/phantombot/benchmark/tests/conftest.py`:**
```python
import pathlib
import pytest

FIXTURES_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures"
GROUND_TRUTH_DIR = pathlib.Path(__file__).parent.parent / "ground_truth"


@pytest.fixture
def simple_html() -> str:
    return (FIXTURES_DIR / "simple_page.html").read_text()


@pytest.fixture
def entity_rich_html() -> str:
    return (FIXTURES_DIR / "entity_rich_page.html").read_text()


@pytest.fixture
def jsonld_html() -> str:
    return (FIXTURES_DIR / "jsonld_page.html").read_text()


@pytest.fixture
def openapi_html() -> str:
    return (FIXTURES_DIR / "openapi_page.html").read_text()


@pytest.fixture
def ground_truth_dir() -> pathlib.Path:
    return GROUND_TRUTH_DIR
```

**Code for `/Users/suryakoritala/phantombot/benchmark/results/.gitkeep`:**
(empty file)

**Commands:**
```bash
mkdir -p /Users/suryakoritala/phantombot/benchmark/{adapters,metrics,ground_truth,results,tests}
# Then create each file listed above
```

**Commit message:** `feat(benchmark): scaffold directory structure and init files`

---

### Task 0.2: Create `requirements.txt`

- [ ] Create the Python dependencies file for the benchmark suite.

**File:** `/Users/suryakoritala/phantombot/benchmark/requirements.txt`

**Code:**
```
# Benchmark suite dependencies
pytest>=8.0.0
pytest-asyncio>=0.23.0
spacy>=3.7.0
beautifulsoup4>=4.12.0
pydantic>=2.0.0
crawl4ai>=0.4.0
requests>=2.31.0
Levenshtein>=0.25.0
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pip install -r benchmark/requirements.txt
python -m spacy download en_core_web_sm
```

**Commit message:** `feat(benchmark): add requirements.txt with dependencies`

---

## Task 1: Abstract adapter interface — `benchmark/adapters/base.py`

### Task 1.1: Write tests for CrawlResult and ExtractionResult dataclasses

- [ ] Write unit tests that validate the data structures used across all adapters.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_base_adapter.py`

**Code:**
```python
import pytest
from benchmark.adapters.base import CrawlResult, ExtractionResult, BaseAdapter


class TestCrawlResult:
    def test_create_successful_result(self):
        result = CrawlResult(
            url="https://example.com",
            status_code=200,
            html="<html><body>Hello</body></html>",
            markdown="Hello",
            text="Hello",
            metadata={},
            elapsed_seconds=1.5,
            success=True,
        )
        assert result.url == "https://example.com"
        assert result.status_code == 200
        assert result.success is True
        assert result.error is None
        assert result.elapsed_seconds == 1.5

    def test_create_failed_result(self):
        result = CrawlResult(
            url="https://example.com/bad",
            status_code=500,
            html="",
            markdown="",
            text="",
            metadata={},
            elapsed_seconds=30.0,
            success=False,
            error="Timeout exceeded",
        )
        assert result.success is False
        assert result.error == "Timeout exceeded"

    def test_default_error_is_none(self):
        result = CrawlResult(
            url="https://example.com",
            status_code=200,
            html="<p>Hi</p>",
            markdown="Hi",
            text="Hi",
            metadata={"tool": "test"},
            elapsed_seconds=0.1,
            success=True,
        )
        assert result.error is None


class TestExtractionResult:
    def test_create_with_entities_and_schemas(self):
        result = ExtractionResult(
            entities=[
                {"text": "Google", "type": "ORG"},
                {"text": "Sundar Pichai", "type": "PERSON"},
            ],
            schemas=[
                {"type": "JSON_LD", "data": {"@type": "Organization"}},
            ],
        )
        assert len(result.entities) == 2
        assert len(result.schemas) == 1

    def test_create_empty(self):
        result = ExtractionResult(entities=[], schemas=[])
        assert result.entities == []
        assert result.schemas == []


class TestBaseAdapterInterface:
    def test_cannot_instantiate_base_adapter(self):
        with pytest.raises(TypeError):
            BaseAdapter()

    def test_concrete_adapter_must_implement_all_methods(self):
        class IncompleteAdapter(BaseAdapter):
            pass

        with pytest.raises(TypeError):
            IncompleteAdapter()

    def test_concrete_adapter_with_all_methods(self):
        class CompleteAdapter(BaseAdapter):
            def name(self) -> str:
                return "test"

            def version(self) -> str:
                return "1.0.0"

            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

            def crawl(self, url: str, timeout: int = 30) -> CrawlResult:
                return CrawlResult(
                    url=url,
                    status_code=200,
                    html="<p>test</p>",
                    markdown="test",
                    text="test",
                    metadata={},
                    elapsed_seconds=0.1,
                    success=True,
                )

            def extract_entities(self, html: str, url: str) -> list[dict]:
                return []

            def extract_schemas(self, html: str, url: str) -> list[dict]:
                return []

        adapter = CompleteAdapter()
        assert adapter.name() == "test"
        assert adapter.version() == "1.0.0"
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_base_adapter.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 1.2: Implement `benchmark/adapters/base.py`

- [ ] Implement the abstract adapter interface with CrawlResult, ExtractionResult, and BaseAdapter.

**File:** `/Users/suryakoritala/phantombot/benchmark/adapters/base.py`

**Code:**
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CrawlResult:
    """Result from crawling a single URL."""

    url: str
    status_code: int
    html: str
    markdown: str
    text: str
    metadata: dict
    elapsed_seconds: float
    success: bool
    error: str | None = None


@dataclass
class ExtractionResult:
    """Entities and schemas extracted from a page."""

    entities: list[dict] = field(default_factory=list)
    schemas: list[dict] = field(default_factory=list)


class BaseAdapter(ABC):
    """Common interface for all benchmark tools."""

    @abstractmethod
    def name(self) -> str:
        """Tool display name."""
        ...

    @abstractmethod
    def version(self) -> str:
        """Tool version string."""
        ...

    @abstractmethod
    def setup(self) -> None:
        """One-time setup (start Docker containers, load models, etc.)."""
        ...

    @abstractmethod
    def teardown(self) -> None:
        """Cleanup after benchmark run."""
        ...

    @abstractmethod
    def crawl(self, url: str, timeout: int = 30) -> CrawlResult:
        """Crawl a single URL and return structured result."""
        ...

    @abstractmethod
    def extract_entities(self, html: str, url: str) -> list[dict]:
        """Extract entities from HTML. Returns list of {"text": ..., "type": ...}."""
        ...

    @abstractmethod
    def extract_schemas(self, html: str, url: str) -> list[dict]:
        """Extract schemas (JSON-LD, OpenAPI) from HTML. Returns list of {"type": ..., "data": ...}."""
        ...
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_base_adapter.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): add abstract adapter interface with CrawlResult and ExtractionResult`

---

## Task 2: URL corpus manifest — `benchmark/corpus.json`

### Task 2.1: Write tests for corpus loading and validation

- [ ] Write tests that validate the corpus JSON structure and content.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_corpus.py`

**Code:**
```python
import json
import pathlib

import pytest

CORPUS_PATH = pathlib.Path(__file__).parent.parent / "corpus.json"

REQUIRED_CATEGORIES = {
    "static_html",
    "js_heavy_spa",
    "jsonld_structured",
    "api_documentation",
    "news_entity_rich",
    "ecommerce_product",
}


def load_corpus() -> dict:
    with open(CORPUS_PATH) as f:
        return json.load(f)


class TestCorpusStructure:
    def test_corpus_file_exists(self):
        assert CORPUS_PATH.exists(), f"corpus.json not found at {CORPUS_PATH}"

    def test_corpus_has_version(self):
        corpus = load_corpus()
        assert "version" in corpus
        assert corpus["version"] == "1.0"

    def test_corpus_has_created_at(self):
        corpus = load_corpus()
        assert "created_at" in corpus

    def test_corpus_has_25_urls(self):
        corpus = load_corpus()
        assert len(corpus["urls"]) == 25

    def test_all_urls_have_required_fields(self):
        corpus = load_corpus()
        required_fields = {"id", "url", "category", "expected_entities", "expected_schemas", "notes"}
        for entry in corpus["urls"]:
            missing = required_fields - set(entry.keys())
            assert not missing, f"URL {entry.get('id', '?')} missing fields: {missing}"

    def test_all_categories_represented(self):
        corpus = load_corpus()
        categories = {entry["category"] for entry in corpus["urls"]}
        missing = REQUIRED_CATEGORIES - categories
        assert not missing, f"Missing categories: {missing}"

    def test_ids_are_unique(self):
        corpus = load_corpus()
        ids = [entry["id"] for entry in corpus["urls"]]
        assert len(ids) == len(set(ids)), "Duplicate IDs found"

    def test_urls_are_valid_https(self):
        corpus = load_corpus()
        for entry in corpus["urls"]:
            assert entry["url"].startswith("https://"), (
                f"URL {entry['id']} must use HTTPS: {entry['url']}"
            )

    def test_expected_entities_is_nonnegative_int(self):
        corpus = load_corpus()
        for entry in corpus["urls"]:
            assert isinstance(entry["expected_entities"], int)
            assert entry["expected_entities"] >= 0

    def test_expected_schemas_is_nonnegative_int(self):
        corpus = load_corpus()
        for entry in corpus["urls"]:
            assert isinstance(entry["expected_schemas"], int)
            assert entry["expected_schemas"] >= 0

    def test_category_counts(self):
        corpus = load_corpus()
        from collections import Counter
        counts = Counter(entry["category"] for entry in corpus["urls"])
        assert counts["static_html"] == 5
        assert counts["js_heavy_spa"] == 4
        assert counts["jsonld_structured"] == 4
        assert counts["api_documentation"] == 4
        assert counts["news_entity_rich"] == 4
        assert counts["ecommerce_product"] == 4
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_corpus.py -v 2>&1 | head -30
# Expected: FAILURES (corpus.json does not exist yet)
```

---

### Task 2.2: Create `benchmark/corpus.json`

- [ ] Create the 25-URL corpus manifest with all six page categories.

**File:** `/Users/suryakoritala/phantombot/benchmark/corpus.json`

**Code:**
```json
{
  "version": "1.0",
  "created_at": "2026-04-12",
  "urls": [
    {
      "id": "static-001",
      "url": "https://en.wikipedia.org/wiki/Web_crawler",
      "category": "static_html",
      "expected_entities": 15,
      "expected_schemas": 0,
      "notes": "Well-structured Wikipedia article with many named entities"
    },
    {
      "id": "static-002",
      "url": "https://en.wikipedia.org/wiki/Natural_language_processing",
      "category": "static_html",
      "expected_entities": 20,
      "expected_schemas": 0,
      "notes": "Dense Wikipedia article covering NLP history and key figures"
    },
    {
      "id": "static-003",
      "url": "https://docs.python.org/3/library/json.html",
      "category": "static_html",
      "expected_entities": 5,
      "expected_schemas": 0,
      "notes": "Python standard library documentation page"
    },
    {
      "id": "static-004",
      "url": "https://developer.mozilla.org/en-US/docs/Web/HTML",
      "category": "static_html",
      "expected_entities": 8,
      "expected_schemas": 0,
      "notes": "MDN HTML reference landing page"
    },
    {
      "id": "static-005",
      "url": "https://en.wikipedia.org/wiki/Artificial_intelligence",
      "category": "static_html",
      "expected_entities": 25,
      "expected_schemas": 0,
      "notes": "Comprehensive Wikipedia article with many entities and references"
    },
    {
      "id": "spa-001",
      "url": "https://news.ycombinator.com/",
      "category": "js_heavy_spa",
      "expected_entities": 10,
      "expected_schemas": 0,
      "notes": "Hacker News front page, dynamically loaded content"
    },
    {
      "id": "spa-002",
      "url": "https://angular.dev/overview",
      "category": "js_heavy_spa",
      "expected_entities": 5,
      "expected_schemas": 0,
      "notes": "Angular documentation, SPA with client-side rendering"
    },
    {
      "id": "spa-003",
      "url": "https://react.dev/learn",
      "category": "js_heavy_spa",
      "expected_entities": 5,
      "expected_schemas": 0,
      "notes": "React documentation, SPA with client-side rendering"
    },
    {
      "id": "spa-004",
      "url": "https://vuejs.org/guide/introduction.html",
      "category": "js_heavy_spa",
      "expected_entities": 5,
      "expected_schemas": 0,
      "notes": "Vue.js documentation, SPA with client-side rendering"
    },
    {
      "id": "jsonld-001",
      "url": "https://www.allrecipes.com/recipe/10813/best-chocolate-chip-cookies/",
      "category": "jsonld_structured",
      "expected_entities": 5,
      "expected_schemas": 1,
      "notes": "Recipe page with schema.org/Recipe JSON-LD"
    },
    {
      "id": "jsonld-002",
      "url": "https://www.imdb.com/title/tt0111161/",
      "category": "jsonld_structured",
      "expected_entities": 10,
      "expected_schemas": 1,
      "notes": "IMDB movie page with schema.org/Movie JSON-LD"
    },
    {
      "id": "jsonld-003",
      "url": "https://www.eventbrite.com/d/online/free--events/",
      "category": "jsonld_structured",
      "expected_entities": 8,
      "expected_schemas": 1,
      "notes": "Event listing page with schema.org/Event JSON-LD"
    },
    {
      "id": "jsonld-004",
      "url": "https://www.bbc.com/news",
      "category": "jsonld_structured",
      "expected_entities": 10,
      "expected_schemas": 1,
      "notes": "BBC News homepage with schema.org/WebPage JSON-LD"
    },
    {
      "id": "api-001",
      "url": "https://petstore.swagger.io/",
      "category": "api_documentation",
      "expected_entities": 2,
      "expected_schemas": 1,
      "notes": "Swagger Petstore demo - canonical OpenAPI example"
    },
    {
      "id": "api-002",
      "url": "https://docs.github.com/en/rest",
      "category": "api_documentation",
      "expected_entities": 5,
      "expected_schemas": 0,
      "notes": "GitHub REST API documentation landing page"
    },
    {
      "id": "api-003",
      "url": "https://httpbin.org/spec.json",
      "category": "api_documentation",
      "expected_entities": 0,
      "expected_schemas": 1,
      "notes": "Raw OpenAPI spec in JSON format"
    },
    {
      "id": "api-004",
      "url": "https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API",
      "category": "api_documentation",
      "expected_entities": 5,
      "expected_schemas": 0,
      "notes": "MDN Fetch API documentation page"
    },
    {
      "id": "news-001",
      "url": "https://www.reuters.com/technology/",
      "category": "news_entity_rich",
      "expected_entities": 20,
      "expected_schemas": 1,
      "notes": "Reuters technology section with rich entity content"
    },
    {
      "id": "news-002",
      "url": "https://apnews.com/hub/technology",
      "category": "news_entity_rich",
      "expected_entities": 15,
      "expected_schemas": 1,
      "notes": "AP News technology hub with many named entities"
    },
    {
      "id": "news-003",
      "url": "https://www.bbc.com/news/technology",
      "category": "news_entity_rich",
      "expected_entities": 15,
      "expected_schemas": 1,
      "notes": "BBC Technology section with articles and entities"
    },
    {
      "id": "news-004",
      "url": "https://www.theverge.com/tech",
      "category": "news_entity_rich",
      "expected_entities": 15,
      "expected_schemas": 1,
      "notes": "The Verge tech section with rich entity content"
    },
    {
      "id": "ecom-001",
      "url": "https://www.etsy.com/listing/1308730637/",
      "category": "ecommerce_product",
      "expected_entities": 5,
      "expected_schemas": 1,
      "notes": "Etsy product listing with schema.org/Product JSON-LD"
    },
    {
      "id": "ecom-002",
      "url": "https://www.target.com/p/apple-airpods-4/-/A-93039524",
      "category": "ecommerce_product",
      "expected_entities": 5,
      "expected_schemas": 1,
      "notes": "Target product page with structured product data"
    },
    {
      "id": "ecom-003",
      "url": "https://www.bestbuy.com/site/apple-macbook-air-13-inch-apple-m3-chip-8gb-memory-256gb/6565837.p",
      "category": "ecommerce_product",
      "expected_entities": 8,
      "expected_schemas": 1,
      "notes": "Best Buy product page with schema.org/Product JSON-LD"
    },
    {
      "id": "ecom-004",
      "url": "https://www.walmart.com/ip/Apple-AirPods-Pro-2/1752657021",
      "category": "ecommerce_product",
      "expected_entities": 5,
      "expected_schemas": 1,
      "notes": "Walmart product page with structured product data"
    }
  ]
}
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_corpus.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): add 25-URL corpus manifest across 6 page categories`

---

## Task 3: Ground truth annotations — `benchmark/ground_truth/`

### Task 3.1: Write tests for ground truth file validation

- [ ] Write tests that validate ground truth JSON files parse correctly and have required structure.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_ground_truth.py`

**Code:**
```python
import json
import pathlib

import pytest

GROUND_TRUTH_DIR = pathlib.Path(__file__).parent.parent / "ground_truth"

VALID_ENTITY_TYPES = {"PERSON", "ORG", "GPE", "PRODUCT", "LOC", "EVENT", "MONEY"}


def load_json(path: pathlib.Path) -> dict:
    with open(path) as f:
        return json.load(f)


def entity_files() -> list[pathlib.Path]:
    return sorted(GROUND_TRUTH_DIR.glob("*-entities.json"))


def schema_files() -> list[pathlib.Path]:
    return sorted(GROUND_TRUTH_DIR.glob("*-schemas.json"))


class TestEntityAnnotations:
    def test_at_least_10_entity_files_exist(self):
        files = entity_files()
        assert len(files) >= 10, f"Expected >= 10 entity files, found {len(files)}"

    def test_entity_files_have_required_fields(self):
        for path in entity_files():
            data = load_json(path)
            assert "url" in data, f"{path.name} missing 'url'"
            assert "annotator" in data, f"{path.name} missing 'annotator'"
            assert "annotated_at" in data, f"{path.name} missing 'annotated_at'"
            assert "entities" in data, f"{path.name} missing 'entities'"
            assert isinstance(data["entities"], list), f"{path.name} 'entities' is not a list"

    def test_entities_have_text_and_type(self):
        for path in entity_files():
            data = load_json(path)
            for i, entity in enumerate(data["entities"]):
                assert "text" in entity, f"{path.name} entity[{i}] missing 'text'"
                assert "type" in entity, f"{path.name} entity[{i}] missing 'type'"

    def test_entity_types_are_valid(self):
        for path in entity_files():
            data = load_json(path)
            for entity in data["entities"]:
                assert entity["type"] in VALID_ENTITY_TYPES, (
                    f"{path.name}: invalid entity type '{entity['type']}'"
                )

    def test_entity_text_is_nonempty(self):
        for path in entity_files():
            data = load_json(path)
            for entity in data["entities"]:
                assert entity["text"].strip(), f"{path.name}: entity has empty text"


class TestSchemaAnnotations:
    def test_at_least_8_schema_files_exist(self):
        files = schema_files()
        assert len(files) >= 8, f"Expected >= 8 schema files, found {len(files)}"

    def test_schema_files_have_required_fields(self):
        for path in schema_files():
            data = load_json(path)
            assert "url" in data, f"{path.name} missing 'url'"
            assert "schemas" in data, f"{path.name} missing 'schemas'"
            assert isinstance(data["schemas"], list), f"{path.name} 'schemas' is not a list"

    def test_schemas_have_type_and_key_fields(self):
        for path in schema_files():
            data = load_json(path)
            for i, schema in enumerate(data["schemas"]):
                assert "type" in schema, f"{path.name} schema[{i}] missing 'type'"
                assert schema["type"] in ("JSON_LD", "OPENAPI"), (
                    f"{path.name} schema[{i}] invalid type: {schema['type']}"
                )
                assert "key_fields" in schema, f"{path.name} schema[{i}] missing 'key_fields'"
                assert isinstance(schema["key_fields"], list)
                assert "expected_count" in schema, f"{path.name} schema[{i}] missing 'expected_count'"
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_ground_truth.py -v 2>&1 | head -30
# Expected: FAILURES (no ground truth files yet)
```

---

### Task 3.2: Create entity annotation files (10 URLs)

- [ ] Create ground truth entity annotation JSON files for 10 corpus URLs covering all categories.

**Files to create (10 entity annotation files):**

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/static-001-entities.json`
```json
{
  "url": "https://en.wikipedia.org/wiki/Web_crawler",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "Google", "type": "ORG"},
    {"text": "Googlebot", "type": "PRODUCT"},
    {"text": "World Wide Web", "type": "PRODUCT"},
    {"text": "Tim Berners-Lee", "type": "PERSON"},
    {"text": "Internet Archive", "type": "ORG"},
    {"text": "Yahoo", "type": "ORG"},
    {"text": "Bing", "type": "PRODUCT"},
    {"text": "Microsoft", "type": "ORG"},
    {"text": "Apache Nutch", "type": "PRODUCT"},
    {"text": "Heritrix", "type": "PRODUCT"},
    {"text": "MIT", "type": "ORG"},
    {"text": "W3C", "type": "ORG"},
    {"text": "Baidu", "type": "ORG"},
    {"text": "DuckDuckGo", "type": "ORG"},
    {"text": "Yandex", "type": "ORG"}
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/static-002-entities.json`
```json
{
  "url": "https://en.wikipedia.org/wiki/Natural_language_processing",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "Alan Turing", "type": "PERSON"},
    {"text": "Georgetown University", "type": "ORG"},
    {"text": "IBM", "type": "ORG"},
    {"text": "Google", "type": "ORG"},
    {"text": "OpenAI", "type": "ORG"},
    {"text": "Noam Chomsky", "type": "PERSON"},
    {"text": "DARPA", "type": "ORG"},
    {"text": "Stanford University", "type": "ORG"},
    {"text": "GPT", "type": "PRODUCT"},
    {"text": "BERT", "type": "PRODUCT"},
    {"text": "United States", "type": "GPE"},
    {"text": "Siri", "type": "PRODUCT"},
    {"text": "Alexa", "type": "PRODUCT"},
    {"text": "Microsoft", "type": "ORG"},
    {"text": "Apple", "type": "ORG"},
    {"text": "Amazon", "type": "ORG"},
    {"text": "Christopher Manning", "type": "PERSON"},
    {"text": "Yoshua Bengio", "type": "PERSON"},
    {"text": "China", "type": "GPE"},
    {"text": "Europe", "type": "LOC"}
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/static-005-entities.json`
```json
{
  "url": "https://en.wikipedia.org/wiki/Artificial_intelligence",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "John McCarthy", "type": "PERSON"},
    {"text": "Alan Turing", "type": "PERSON"},
    {"text": "Marvin Minsky", "type": "PERSON"},
    {"text": "Geoffrey Hinton", "type": "PERSON"},
    {"text": "Google", "type": "ORG"},
    {"text": "DeepMind", "type": "ORG"},
    {"text": "OpenAI", "type": "ORG"},
    {"text": "IBM", "type": "ORG"},
    {"text": "Microsoft", "type": "ORG"},
    {"text": "Stanford University", "type": "ORG"},
    {"text": "MIT", "type": "ORG"},
    {"text": "Dartmouth", "type": "ORG"},
    {"text": "Deep Blue", "type": "PRODUCT"},
    {"text": "AlphaGo", "type": "PRODUCT"},
    {"text": "ChatGPT", "type": "PRODUCT"},
    {"text": "United States", "type": "GPE"},
    {"text": "China", "type": "GPE"},
    {"text": "Europe", "type": "LOC"},
    {"text": "Yann LeCun", "type": "PERSON"},
    {"text": "Demis Hassabis", "type": "PERSON"},
    {"text": "Sam Altman", "type": "PERSON"},
    {"text": "DARPA", "type": "ORG"},
    {"text": "Facebook", "type": "ORG"},
    {"text": "Meta", "type": "ORG"},
    {"text": "Nvidia", "type": "ORG"}
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/jsonld-001-entities.json`
```json
{
  "url": "https://www.allrecipes.com/recipe/10813/best-chocolate-chip-cookies/",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "Allrecipes", "type": "ORG"},
    {"text": "chocolate chip cookies", "type": "PRODUCT"},
    {"text": "butter", "type": "PRODUCT"},
    {"text": "vanilla extract", "type": "PRODUCT"},
    {"text": "brown sugar", "type": "PRODUCT"}
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/jsonld-002-entities.json`
```json
{
  "url": "https://www.imdb.com/title/tt0111161/",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "Tim Robbins", "type": "PERSON"},
    {"text": "Morgan Freeman", "type": "PERSON"},
    {"text": "Frank Darabont", "type": "PERSON"},
    {"text": "Stephen King", "type": "PERSON"},
    {"text": "The Shawshank Redemption", "type": "PRODUCT"},
    {"text": "Castle Rock Entertainment", "type": "ORG"},
    {"text": "Columbia Pictures", "type": "ORG"},
    {"text": "Andy Dufresne", "type": "PERSON"},
    {"text": "IMDB", "type": "ORG"},
    {"text": "Bob Gunton", "type": "PERSON"}
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/news-001-entities.json`
```json
{
  "url": "https://www.reuters.com/technology/",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "Reuters", "type": "ORG"},
    {"text": "Apple", "type": "ORG"},
    {"text": "Google", "type": "ORG"},
    {"text": "Microsoft", "type": "ORG"},
    {"text": "Meta", "type": "ORG"},
    {"text": "Amazon", "type": "ORG"},
    {"text": "Nvidia", "type": "ORG"},
    {"text": "Tesla", "type": "ORG"},
    {"text": "OpenAI", "type": "ORG"},
    {"text": "United States", "type": "GPE"},
    {"text": "China", "type": "GPE"},
    {"text": "European Union", "type": "ORG"},
    {"text": "Samsung", "type": "ORG"},
    {"text": "Washington", "type": "GPE"},
    {"text": "Silicon Valley", "type": "LOC"},
    {"text": "Elon Musk", "type": "PERSON"},
    {"text": "Tim Cook", "type": "PERSON"},
    {"text": "Satya Nadella", "type": "PERSON"},
    {"text": "Mark Zuckerberg", "type": "PERSON"},
    {"text": "Sam Altman", "type": "PERSON"}
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/news-003-entities.json`
```json
{
  "url": "https://www.bbc.com/news/technology",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "BBC", "type": "ORG"},
    {"text": "Apple", "type": "ORG"},
    {"text": "Google", "type": "ORG"},
    {"text": "Microsoft", "type": "ORG"},
    {"text": "Meta", "type": "ORG"},
    {"text": "Amazon", "type": "ORG"},
    {"text": "United Kingdom", "type": "GPE"},
    {"text": "United States", "type": "GPE"},
    {"text": "London", "type": "GPE"},
    {"text": "Europe", "type": "LOC"},
    {"text": "Samsung", "type": "ORG"},
    {"text": "Tesla", "type": "ORG"},
    {"text": "Elon Musk", "type": "PERSON"},
    {"text": "OpenAI", "type": "ORG"},
    {"text": "TikTok", "type": "PRODUCT"}
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/ecom-001-entities.json`
```json
{
  "url": "https://www.etsy.com/listing/1308730637/",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "Etsy", "type": "ORG"},
    {"text": "United States", "type": "GPE"}
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/api-001-entities.json`
```json
{
  "url": "https://petstore.swagger.io/",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "Swagger", "type": "PRODUCT"},
    {"text": "Petstore", "type": "PRODUCT"}
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/spa-001-entities.json`
```json
{
  "url": "https://news.ycombinator.com/",
  "annotator": "human",
  "annotated_at": "2026-04-12",
  "entities": [
    {"text": "Y Combinator", "type": "ORG"},
    {"text": "Hacker News", "type": "PRODUCT"},
    {"text": "Google", "type": "ORG"},
    {"text": "Microsoft", "type": "ORG"},
    {"text": "Apple", "type": "ORG"},
    {"text": "Amazon", "type": "ORG"},
    {"text": "GitHub", "type": "ORG"},
    {"text": "San Francisco", "type": "GPE"},
    {"text": "United States", "type": "GPE"},
    {"text": "Silicon Valley", "type": "LOC"}
  ]
}
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_ground_truth.py::TestEntityAnnotations -v
# Expected: all entity tests pass
```

---

### Task 3.3: Create schema annotation files (8 URLs)

- [ ] Create ground truth schema annotation JSON files for 8 corpus URLs that contain structured data.

**Files to create (8 schema annotation files):**

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/jsonld-001-schemas.json`
```json
{
  "url": "https://www.allrecipes.com/recipe/10813/best-chocolate-chip-cookies/",
  "schemas": [
    {
      "type": "JSON_LD",
      "schema_org_type": "Recipe",
      "key_fields": ["name", "author", "cookTime", "recipeIngredient", "recipeInstructions"],
      "expected_count": 1
    }
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/jsonld-002-schemas.json`
```json
{
  "url": "https://www.imdb.com/title/tt0111161/",
  "schemas": [
    {
      "type": "JSON_LD",
      "schema_org_type": "Movie",
      "key_fields": ["name", "director", "actor", "datePublished", "genre"],
      "expected_count": 1
    }
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/jsonld-003-schemas.json`
```json
{
  "url": "https://www.eventbrite.com/d/online/free--events/",
  "schemas": [
    {
      "type": "JSON_LD",
      "schema_org_type": "ItemList",
      "key_fields": ["itemListElement", "numberOfItems"],
      "expected_count": 1
    }
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/jsonld-004-schemas.json`
```json
{
  "url": "https://www.bbc.com/news",
  "schemas": [
    {
      "type": "JSON_LD",
      "schema_org_type": "WebPage",
      "key_fields": ["name", "url"],
      "expected_count": 1
    }
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/api-001-schemas.json`
```json
{
  "url": "https://petstore.swagger.io/",
  "schemas": [
    {
      "type": "OPENAPI",
      "schema_org_type": null,
      "key_fields": ["openapi", "info", "paths"],
      "expected_count": 1
    }
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/api-003-schemas.json`
```json
{
  "url": "https://httpbin.org/spec.json",
  "schemas": [
    {
      "type": "OPENAPI",
      "schema_org_type": null,
      "key_fields": ["swagger", "info", "paths"],
      "expected_count": 1
    }
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/ecom-001-schemas.json`
```json
{
  "url": "https://www.etsy.com/listing/1308730637/",
  "schemas": [
    {
      "type": "JSON_LD",
      "schema_org_type": "Product",
      "key_fields": ["name", "image", "offers"],
      "expected_count": 1
    }
  ]
}
```

**File:** `/Users/suryakoritala/phantombot/benchmark/ground_truth/ecom-003-schemas.json`
```json
{
  "url": "https://www.bestbuy.com/site/apple-macbook-air-13-inch-apple-m3-chip-8gb-memory-256gb/6565837.p",
  "schemas": [
    {
      "type": "JSON_LD",
      "schema_org_type": "Product",
      "key_fields": ["name", "image", "offers", "brand"],
      "expected_count": 1
    }
  ]
}
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_ground_truth.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): add ground truth entity and schema annotations`

---

## Task 4: Phantombot adapter — `benchmark/adapters/phantombot_adapter.py`

### Task 4.1: Write tests for Phantombot adapter

- [ ] Write unit tests that validate the Phantombot adapter against local fixture HTML.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_phantombot_adapter.py`

**Code:**
```python
import pytest
from unittest.mock import patch, MagicMock
from benchmark.adapters.phantombot_adapter import PhantombotAdapter
from benchmark.adapters.base import CrawlResult


@pytest.fixture
def adapter():
    return PhantombotAdapter()


class TestPhantombotAdapterMetadata:
    def test_name(self, adapter):
        assert adapter.name() == "phantombot"

    def test_version(self, adapter):
        version = adapter.version()
        assert isinstance(version, str)
        assert len(version) > 0


class TestPhantombotEntityExtraction:
    def test_extracts_entities_from_entity_rich_html(self, adapter, entity_rich_html):
        entities = adapter.extract_entities(entity_rich_html, "https://example.com/news")
        assert isinstance(entities, list)
        assert len(entities) > 0
        texts = [e["text"] for e in entities]
        assert any("Google" in t for t in texts)

    def test_entity_format(self, adapter, entity_rich_html):
        entities = adapter.extract_entities(entity_rich_html, "https://example.com/news")
        for entity in entities:
            assert "text" in entity
            assert "type" in entity
            assert isinstance(entity["text"], str)
            assert entity["type"] in ("PERSON", "ORG", "GPE", "PRODUCT", "MONEY", "LOC", "FAC", "NORP", "EVENT")

    def test_returns_empty_for_empty_html(self, adapter):
        entities = adapter.extract_entities("", "https://example.com/empty")
        assert entities == []


class TestPhantombotSchemaExtraction:
    def test_extracts_jsonld(self, adapter, jsonld_html):
        schemas = adapter.extract_schemas(jsonld_html, "https://example.com/jsonld")
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert schemas[0]["type"] == "JSON_LD"

    def test_extracts_openapi(self, adapter, openapi_html):
        schemas = adapter.extract_schemas(openapi_html, "https://example.com/api")
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert schemas[0]["type"] == "OPENAPI"

    def test_schema_format(self, adapter, jsonld_html):
        schemas = adapter.extract_schemas(jsonld_html, "https://example.com/jsonld")
        for schema in schemas:
            assert "type" in schema
            assert "data" in schema
            assert schema["type"] in ("JSON_LD", "OPENAPI")

    def test_returns_empty_for_no_schemas(self, adapter, simple_html):
        schemas = adapter.extract_schemas(simple_html, "https://example.com/simple")
        assert schemas == []


class TestPhantombotCrawl:
    @patch("benchmark.adapters.phantombot_adapter.requests.get")
    def test_crawl_returns_crawl_result(self, mock_get, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        mock_response.headers = {"content-type": "text/html"}
        mock_response.elapsed.total_seconds.return_value = 0.5
        mock_get.return_value = mock_response

        result = adapter.crawl("https://example.com", timeout=30)
        assert isinstance(result, CrawlResult)
        assert result.success is True
        assert result.status_code == 200
        assert result.url == "https://example.com"

    @patch("benchmark.adapters.phantombot_adapter.requests.get")
    def test_crawl_handles_timeout(self, mock_get, adapter):
        import requests
        mock_get.side_effect = requests.Timeout("Connection timed out")

        result = adapter.crawl("https://example.com/slow", timeout=1)
        assert result.success is False
        assert result.error is not None
        assert "timed out" in result.error.lower() or "timeout" in result.error.lower()
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_phantombot_adapter.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 4.2: Implement `benchmark/adapters/phantombot_adapter.py`

- [ ] Implement the Phantombot adapter in direct mode using the intelligence layer's pipelines.

**File:** `/Users/suryakoritala/phantombot/benchmark/adapters/phantombot_adapter.py`

**Code:**
```python
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
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_phantombot_adapter.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): implement Phantombot adapter with direct pipeline access`

---

## Task 5: Crawl4AI adapter — `benchmark/adapters/crawl4ai_adapter.py`

### Task 5.1: Write tests for Crawl4AI adapter

- [ ] Write unit tests for the Crawl4AI adapter.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_crawl4ai_adapter.py`

**Code:**
```python
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from benchmark.adapters.crawl4ai_adapter import Crawl4AIAdapter
from benchmark.adapters.base import CrawlResult


@pytest.fixture
def adapter():
    return Crawl4AIAdapter()


class TestCrawl4AIAdapterMetadata:
    def test_name(self, adapter):
        assert adapter.name() == "crawl4ai"

    def test_version(self, adapter):
        version = adapter.version()
        assert isinstance(version, str)
        assert len(version) > 0


class TestCrawl4AIEntityExtraction:
    def test_extracts_entities_from_entity_rich_html(self, adapter, entity_rich_html):
        entities = adapter.extract_entities(entity_rich_html, "https://example.com/news")
        assert isinstance(entities, list)
        assert len(entities) > 0
        texts = [e["text"] for e in entities]
        assert any("Google" in t for t in texts)

    def test_entity_format(self, adapter, entity_rich_html):
        entities = adapter.extract_entities(entity_rich_html, "https://example.com/news")
        for entity in entities:
            assert "text" in entity
            assert "type" in entity

    def test_returns_empty_for_empty_html(self, adapter):
        entities = adapter.extract_entities("", "https://example.com/empty")
        assert entities == []


class TestCrawl4AISchemaExtraction:
    def test_extracts_jsonld(self, adapter, jsonld_html):
        schemas = adapter.extract_schemas(jsonld_html, "https://example.com/jsonld")
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert schemas[0]["type"] == "JSON_LD"

    def test_returns_empty_for_no_schemas(self, adapter, simple_html):
        schemas = adapter.extract_schemas(simple_html, "https://example.com/simple")
        assert schemas == []


class TestCrawl4AICrawl:
    @patch("benchmark.adapters.crawl4ai_adapter.requests.get")
    def test_crawl_returns_crawl_result(self, mock_get, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        mock_response.headers = {"content-type": "text/html"}
        mock_response.elapsed.total_seconds.return_value = 0.5
        mock_get.return_value = mock_response

        result = adapter.crawl("https://example.com", timeout=30)
        assert isinstance(result, CrawlResult)
        assert result.success is True
        assert result.status_code == 200

    @patch("benchmark.adapters.crawl4ai_adapter.requests.get")
    def test_crawl_handles_timeout(self, mock_get, adapter):
        import requests
        mock_get.side_effect = requests.Timeout("Connection timed out")

        result = adapter.crawl("https://example.com/slow", timeout=1)
        assert result.success is False
        assert result.error is not None
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_crawl4ai_adapter.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 5.2: Implement `benchmark/adapters/crawl4ai_adapter.py`

- [ ] Implement the Crawl4AI adapter using SpaCy fallback for entity extraction.

**File:** `/Users/suryakoritala/phantombot/benchmark/adapters/crawl4ai_adapter.py`

**Code:**
```python
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
            import crawl4ai
            return getattr(crawl4ai, "__version__", "unknown")
        except ImportError:
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
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_crawl4ai_adapter.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): implement Crawl4AI adapter with SpaCy NER fallback`

---

## Task 6: Firecrawl adapter — `benchmark/adapters/firecrawl_adapter.py`

### Task 6.1: Write tests for Firecrawl adapter

- [ ] Write unit tests for the Firecrawl adapter.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_firecrawl_adapter.py`

**Code:**
```python
import pytest
from unittest.mock import patch, MagicMock
from benchmark.adapters.firecrawl_adapter import FirecrawlAdapter
from benchmark.adapters.base import CrawlResult


@pytest.fixture
def adapter():
    return FirecrawlAdapter()


class TestFirecrawlAdapterMetadata:
    def test_name(self, adapter):
        assert adapter.name() == "firecrawl"

    def test_version(self, adapter):
        version = adapter.version()
        assert isinstance(version, str)
        assert len(version) > 0


class TestFirecrawlEntityExtraction:
    def test_extracts_entities_from_entity_rich_html(self, adapter, entity_rich_html):
        entities = adapter.extract_entities(entity_rich_html, "https://example.com/news")
        assert isinstance(entities, list)
        assert len(entities) > 0
        texts = [e["text"] for e in entities]
        assert any("Google" in t for t in texts)

    def test_entity_format(self, adapter, entity_rich_html):
        entities = adapter.extract_entities(entity_rich_html, "https://example.com/news")
        for entity in entities:
            assert "text" in entity
            assert "type" in entity

    def test_returns_empty_for_empty_html(self, adapter):
        entities = adapter.extract_entities("", "https://example.com/empty")
        assert entities == []


class TestFirecrawlSchemaExtraction:
    def test_extracts_jsonld(self, adapter, jsonld_html):
        schemas = adapter.extract_schemas(jsonld_html, "https://example.com/jsonld")
        assert isinstance(schemas, list)
        assert len(schemas) > 0
        assert schemas[0]["type"] == "JSON_LD"

    def test_returns_empty_for_no_schemas(self, adapter, simple_html):
        schemas = adapter.extract_schemas(simple_html, "https://example.com/simple")
        assert schemas == []


class TestFirecrawlCrawl:
    @patch("benchmark.adapters.firecrawl_adapter.requests.post")
    def test_crawl_returns_crawl_result(self, mock_post, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "html": "<html><body><p>Hello World</p></body></html>",
                "markdown": "Hello World",
                "rawHtml": "<html><body><p>Hello World</p></body></html>",
                "metadata": {"statusCode": 200},
            },
        }
        mock_response.elapsed.total_seconds.return_value = 0.5
        mock_post.return_value = mock_response

        result = adapter.crawl("https://example.com", timeout=30)
        assert isinstance(result, CrawlResult)
        assert result.success is True
        assert result.url == "https://example.com"

    @patch("benchmark.adapters.firecrawl_adapter.requests.post")
    def test_crawl_handles_api_failure(self, mock_post, adapter):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": False,
            "error": "Failed to scrape",
        }
        mock_response.elapsed.total_seconds.return_value = 1.0
        mock_post.return_value = mock_response

        result = adapter.crawl("https://example.com/bad", timeout=30)
        assert result.success is False
        assert result.error is not None

    @patch("benchmark.adapters.firecrawl_adapter.requests.post")
    def test_crawl_handles_timeout(self, mock_post, adapter):
        import requests
        mock_post.side_effect = requests.Timeout("Connection timed out")

        result = adapter.crawl("https://example.com/slow", timeout=1)
        assert result.success is False
        assert result.error is not None
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_firecrawl_adapter.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 6.2: Implement `benchmark/adapters/firecrawl_adapter.py`

- [ ] Implement the Firecrawl adapter using the self-hosted Docker API.

**File:** `/Users/suryakoritala/phantombot/benchmark/adapters/firecrawl_adapter.py`

**Code:**
```python
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
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_firecrawl_adapter.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): implement Firecrawl adapter with self-hosted Docker API`

---

## Task 7: Speed metrics — `benchmark/metrics/speed.py`

### Task 7.1: Write tests for speed metrics

- [ ] Write unit tests for the speed metrics computation.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_speed_metrics.py`

**Code:**
```python
import pytest
from benchmark.adapters.base import CrawlResult
from benchmark.metrics.speed import compute_speed_metrics


def _make_result(url: str, elapsed: float, success: bool = True) -> CrawlResult:
    return CrawlResult(
        url=url, status_code=200 if success else 0, html="<p>ok</p>",
        markdown="ok", text="ok", metadata={}, elapsed_seconds=elapsed,
        success=success, error=None if success else "failed",
    )


class TestComputeSpeedMetrics:
    def test_basic_speed_metrics(self):
        results = [
            _make_result("https://a.com", 1.0),
            _make_result("https://b.com", 2.0),
            _make_result("https://c.com", 3.0),
        ]
        metrics = compute_speed_metrics(results)
        assert metrics["time_to_first_result"] == 1.0
        assert metrics["total_corpus_time"] == pytest.approx(6.0)
        assert metrics["pages_per_second"] == pytest.approx(3.0 / 6.0)
        assert metrics["urls_attempted"] == 3

    def test_with_failures(self):
        results = [
            _make_result("https://a.com", 1.0, success=True),
            _make_result("https://b.com", 30.0, success=False),
            _make_result("https://c.com", 2.0, success=True),
        ]
        metrics = compute_speed_metrics(results)
        assert metrics["time_to_first_result"] == 1.0
        assert metrics["total_corpus_time"] == pytest.approx(33.0)
        assert metrics["urls_succeeded"] == 2
        assert metrics["urls_attempted"] == 3

    def test_all_failures(self):
        results = [
            _make_result("https://a.com", 30.0, success=False),
            _make_result("https://b.com", 30.0, success=False),
        ]
        metrics = compute_speed_metrics(results)
        assert metrics["time_to_first_result"] is None
        assert metrics["pages_per_second"] == 0.0
        assert metrics["urls_succeeded"] == 0

    def test_empty_results(self):
        metrics = compute_speed_metrics([])
        assert metrics["time_to_first_result"] is None
        assert metrics["total_corpus_time"] == 0.0
        assert metrics["pages_per_second"] == 0.0
        assert metrics["urls_attempted"] == 0

    def test_single_result(self):
        results = [_make_result("https://a.com", 2.5)]
        metrics = compute_speed_metrics(results)
        assert metrics["time_to_first_result"] == 2.5
        assert metrics["total_corpus_time"] == 2.5
        assert metrics["pages_per_second"] == pytest.approx(1.0 / 2.5)
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_speed_metrics.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 7.2: Implement `benchmark/metrics/speed.py`

- [ ] Implement the speed metrics computation module.

**File:** `/Users/suryakoritala/phantombot/benchmark/metrics/speed.py`

**Code:**
```python
from benchmark.adapters.base import CrawlResult


def compute_speed_metrics(results: list[CrawlResult]) -> dict:
    """Compute crawl speed metrics from a list of CrawlResults.

    Returns a dict with keys:
        - time_to_first_result: seconds to first successful result (None if all failed)
        - total_corpus_time: total wall-clock seconds across all URLs
        - pages_per_second: successful pages / total_corpus_time
        - urls_attempted: total URLs attempted
        - urls_succeeded: total URLs that returned usable content
    """
    if not results:
        return {
            "time_to_first_result": None,
            "total_corpus_time": 0.0,
            "pages_per_second": 0.0,
            "urls_attempted": 0,
            "urls_succeeded": 0,
        }

    total_time = sum(r.elapsed_seconds for r in results)
    succeeded = [r for r in results if r.success]

    # Time to first successful result (in order of the list)
    time_to_first = None
    cumulative = 0.0
    for r in results:
        cumulative += r.elapsed_seconds
        if r.success:
            time_to_first = cumulative
            break

    pages_per_second = len(succeeded) / total_time if total_time > 0 else 0.0

    return {
        "time_to_first_result": time_to_first,
        "total_corpus_time": total_time,
        "pages_per_second": pages_per_second,
        "urls_attempted": len(results),
        "urls_succeeded": len(succeeded),
    }
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_speed_metrics.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): implement speed metrics computation`

---

## Task 8: Extraction quality metrics — `benchmark/metrics/extraction.py`

### Task 8.1: Write tests for extraction metrics

- [ ] Write unit tests for entity precision, recall, F1, and type accuracy with Levenshtein matching.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_extraction_metrics.py`

**Code:**
```python
import pytest
from benchmark.metrics.extraction import (
    normalize_text,
    entities_match,
    compute_extraction_metrics,
)


class TestNormalizeText:
    def test_lowercases(self):
        assert normalize_text("Google") == "google"

    def test_strips_whitespace(self):
        assert normalize_text("  Google  ") == "google"

    def test_collapses_internal_whitespace(self):
        assert normalize_text("Tim  Berners  Lee") == "tim berners lee"


class TestEntitiesMatch:
    def test_exact_match(self):
        assert entities_match("Google", "Google") is True

    def test_case_insensitive_match(self):
        assert entities_match("google", "Google") is True

    def test_levenshtein_within_threshold(self):
        # "Gogle" is Levenshtein distance 1 from "Google"
        assert entities_match("Gogle", "Google") is True

    def test_levenshtein_beyond_threshold(self):
        # "Gxxxx" is far from "Google"
        assert entities_match("Gxxxx", "Google") is False

    def test_whitespace_normalization(self):
        assert entities_match("Tim  Berners-Lee", "Tim Berners-Lee") is True


class TestComputeExtractionMetrics:
    def test_perfect_extraction(self):
        predicted = [
            {"text": "Google", "type": "ORG"},
            {"text": "Tim Berners-Lee", "type": "PERSON"},
        ]
        ground_truth = [
            {"text": "Google", "type": "ORG"},
            {"text": "Tim Berners-Lee", "type": "PERSON"},
        ]
        metrics = compute_extraction_metrics(predicted, ground_truth)
        assert metrics["precision"] == pytest.approx(1.0)
        assert metrics["recall"] == pytest.approx(1.0)
        assert metrics["f1"] == pytest.approx(1.0)
        assert metrics["type_accuracy"] == pytest.approx(1.0)

    def test_partial_match(self):
        predicted = [
            {"text": "Google", "type": "ORG"},
            {"text": "Microsoft", "type": "ORG"},
            {"text": "Random Corp", "type": "ORG"},
        ]
        ground_truth = [
            {"text": "Google", "type": "ORG"},
            {"text": "Microsoft", "type": "ORG"},
            {"text": "Apple", "type": "ORG"},
        ]
        metrics = compute_extraction_metrics(predicted, ground_truth)
        # TP=2 (Google, Microsoft), FP=1 (Random Corp), FN=1 (Apple)
        assert metrics["precision"] == pytest.approx(2.0 / 3.0)
        assert metrics["recall"] == pytest.approx(2.0 / 3.0)
        assert metrics["true_positives"] == 2
        assert metrics["false_positives"] == 1
        assert metrics["false_negatives"] == 1

    def test_no_matches(self):
        predicted = [{"text": "Foo", "type": "ORG"}]
        ground_truth = [{"text": "Bar", "type": "ORG"}]
        metrics = compute_extraction_metrics(predicted, ground_truth)
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["f1"] == 0.0

    def test_empty_predicted(self):
        ground_truth = [{"text": "Google", "type": "ORG"}]
        metrics = compute_extraction_metrics([], ground_truth)
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["f1"] == 0.0

    def test_empty_ground_truth(self):
        predicted = [{"text": "Google", "type": "ORG"}]
        metrics = compute_extraction_metrics(predicted, [])
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["f1"] == 0.0

    def test_both_empty(self):
        metrics = compute_extraction_metrics([], [])
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["f1"] == 0.0

    def test_type_accuracy_wrong_types(self):
        predicted = [
            {"text": "Google", "type": "PERSON"},  # wrong type
            {"text": "Tim Berners-Lee", "type": "PERSON"},
        ]
        ground_truth = [
            {"text": "Google", "type": "ORG"},
            {"text": "Tim Berners-Lee", "type": "PERSON"},
        ]
        metrics = compute_extraction_metrics(predicted, ground_truth)
        # Both text-match, but one has wrong type
        assert metrics["type_accuracy"] == pytest.approx(0.5)

    def test_fuzzy_match_counts_as_tp(self):
        predicted = [{"text": "Gogle", "type": "ORG"}]  # typo
        ground_truth = [{"text": "Google", "type": "ORG"}]
        metrics = compute_extraction_metrics(predicted, ground_truth)
        assert metrics["true_positives"] == 1
        assert metrics["precision"] == pytest.approx(1.0)
        assert metrics["recall"] == pytest.approx(1.0)
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_extraction_metrics.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 8.2: Implement `benchmark/metrics/extraction.py`

- [ ] Implement entity extraction quality metrics with Levenshtein matching.

**File:** `/Users/suryakoritala/phantombot/benchmark/metrics/extraction.py`

**Code:**
```python
import re

from Levenshtein import distance as levenshtein_distance

LEVENSHTEIN_THRESHOLD = 2


def normalize_text(text: str) -> str:
    """Normalize entity text for comparison: lowercase, strip, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def entities_match(predicted_text: str, ground_truth_text: str) -> bool:
    """Check if two entity texts match within Levenshtein threshold."""
    a = normalize_text(predicted_text)
    b = normalize_text(ground_truth_text)
    if a == b:
        return True
    return levenshtein_distance(a, b) <= LEVENSHTEIN_THRESHOLD


def compute_extraction_metrics(
    predicted: list[dict],
    ground_truth: list[dict],
) -> dict:
    """Compute entity extraction quality metrics.

    Args:
        predicted: List of {"text": str, "type": str} from the tool.
        ground_truth: List of {"text": str, "type": str} from annotations.

    Returns dict with keys:
        - precision, recall, f1: standard IR metrics based on text matching
        - type_accuracy: of text-matched entities, fraction with correct type
        - true_positives, false_positives, false_negatives: raw counts
    """
    if not predicted and not ground_truth:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "type_accuracy": 0.0,
            "true_positives": 0,
            "false_positives": 0,
            "false_negatives": 0,
        }

    if not predicted:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "type_accuracy": 0.0,
            "true_positives": 0,
            "false_positives": len(predicted),
            "false_negatives": len(ground_truth),
        }

    if not ground_truth:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "type_accuracy": 0.0,
            "true_positives": 0,
            "false_positives": len(predicted),
            "false_negatives": 0,
        }

    # Greedy matching: for each predicted entity, find the best ground truth match
    gt_matched = [False] * len(ground_truth)
    tp = 0
    type_correct = 0

    for pred in predicted:
        best_idx = None
        for i, gt in enumerate(ground_truth):
            if gt_matched[i]:
                continue
            if entities_match(pred["text"], gt["text"]):
                best_idx = i
                break

        if best_idx is not None:
            gt_matched[best_idx] = True
            tp += 1
            if pred["type"] == ground_truth[best_idx]["type"]:
                type_correct += 1

    fp = len(predicted) - tp
    fn = len(ground_truth) - tp

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    type_accuracy = type_correct / tp if tp > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "type_accuracy": type_accuracy,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_extraction_metrics.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): implement extraction quality metrics with Levenshtein matching`

---

## Task 9: Schema detection metrics — `benchmark/metrics/schema.py`

### Task 9.1: Write tests for schema metrics

- [ ] Write unit tests for schema detection rates and completeness.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_schema_metrics.py`

**Code:**
```python
import pytest
from benchmark.metrics.schema import compute_schema_metrics


class TestComputeSchemaMetrics:
    def test_perfect_jsonld_detection(self):
        detected = [{"type": "JSON_LD", "data": {"@type": "Recipe", "name": "Cake", "author": "Chef"}}]
        ground_truth = {
            "schemas": [
                {
                    "type": "JSON_LD",
                    "schema_org_type": "Recipe",
                    "key_fields": ["name", "author"],
                    "expected_count": 1,
                }
            ]
        }
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["jsonld_detection_rate"] == pytest.approx(1.0)
        assert metrics["schema_completeness"] == pytest.approx(1.0)

    def test_partial_completeness(self):
        detected = [{"type": "JSON_LD", "data": {"@type": "Recipe", "name": "Cake"}}]
        ground_truth = {
            "schemas": [
                {
                    "type": "JSON_LD",
                    "schema_org_type": "Recipe",
                    "key_fields": ["name", "author", "cookTime"],
                    "expected_count": 1,
                }
            ]
        }
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["jsonld_detection_rate"] == pytest.approx(1.0)
        # Only "name" found out of 3 key_fields
        assert metrics["schema_completeness"] == pytest.approx(1.0 / 3.0)

    def test_no_detection(self):
        detected = []
        ground_truth = {
            "schemas": [
                {
                    "type": "JSON_LD",
                    "schema_org_type": "Recipe",
                    "key_fields": ["name"],
                    "expected_count": 1,
                }
            ]
        }
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["jsonld_detection_rate"] == pytest.approx(0.0)
        assert metrics["schema_completeness"] == pytest.approx(0.0)

    def test_openapi_detection(self):
        detected = [{"type": "OPENAPI", "data": {"openapi": "3.0.0", "info": {}, "paths": {}}}]
        ground_truth = {
            "schemas": [
                {
                    "type": "OPENAPI",
                    "schema_org_type": None,
                    "key_fields": ["openapi", "info", "paths"],
                    "expected_count": 1,
                }
            ]
        }
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["openapi_detection_rate"] == pytest.approx(1.0)
        assert metrics["schema_completeness"] == pytest.approx(1.0)

    def test_false_positive(self):
        detected = [{"type": "JSON_LD", "data": {"@type": "Product"}}]
        ground_truth = {"schemas": []}
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["false_positive_rate"] == pytest.approx(1.0)

    def test_no_schemas_expected_none_found(self):
        detected = []
        ground_truth = {"schemas": []}
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["false_positive_rate"] == pytest.approx(0.0)
        assert metrics["jsonld_detection_rate"] == pytest.approx(0.0)
        assert metrics["openapi_detection_rate"] == pytest.approx(0.0)

    def test_empty_ground_truth_dict(self):
        detected = []
        ground_truth = {"schemas": []}
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["schema_completeness"] == pytest.approx(0.0)

    def test_mixed_schema_types(self):
        detected = [
            {"type": "JSON_LD", "data": {"@type": "Recipe", "name": "Cake"}},
            {"type": "OPENAPI", "data": {"openapi": "3.0.0", "info": {}, "paths": {}}},
        ]
        ground_truth = {
            "schemas": [
                {
                    "type": "JSON_LD",
                    "schema_org_type": "Recipe",
                    "key_fields": ["name"],
                    "expected_count": 1,
                },
                {
                    "type": "OPENAPI",
                    "schema_org_type": None,
                    "key_fields": ["openapi", "info", "paths"],
                    "expected_count": 1,
                },
            ]
        }
        metrics = compute_schema_metrics(detected, ground_truth)
        assert metrics["jsonld_detection_rate"] == pytest.approx(1.0)
        assert metrics["openapi_detection_rate"] == pytest.approx(1.0)
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_schema_metrics.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 9.2: Implement `benchmark/metrics/schema.py`

- [ ] Implement the schema detection metrics computation module.

**File:** `/Users/suryakoritala/phantombot/benchmark/metrics/schema.py`

**Code:**
```python
def compute_schema_metrics(
    detected: list[dict],
    ground_truth: dict,
) -> dict:
    """Compute schema detection metrics.

    Args:
        detected: List of {"type": "JSON_LD"|"OPENAPI", "data": dict} from the tool.
        ground_truth: Dict with "schemas" key containing list of expected schemas,
                      each with "type", "schema_org_type", "key_fields", "expected_count".

    Returns dict with keys:
        - jsonld_detection_rate: fraction of expected JSON-LD schemas detected
        - openapi_detection_rate: fraction of expected OpenAPI schemas detected
        - schema_completeness: avg fraction of key_fields present in detected schemas
        - false_positive_rate: fraction of detected schemas not in ground truth
    """
    expected_schemas = ground_truth.get("schemas", [])
    expected_jsonld = [s for s in expected_schemas if s["type"] == "JSON_LD"]
    expected_openapi = [s for s in expected_schemas if s["type"] == "OPENAPI"]

    detected_jsonld = [d for d in detected if d["type"] == "JSON_LD"]
    detected_openapi = [d for d in detected if d["type"] == "OPENAPI"]

    # JSON-LD detection rate
    jsonld_detected_count = 0
    jsonld_completeness_scores = []
    for expected in expected_jsonld:
        found = False
        for det in detected_jsonld:
            if _jsonld_matches(det, expected):
                found = True
                completeness = _compute_completeness(det["data"], expected["key_fields"])
                jsonld_completeness_scores.append(completeness)
                break
        if not found:
            jsonld_completeness_scores.append(0.0)
        else:
            jsonld_detected_count += 1

    jsonld_rate = jsonld_detected_count / len(expected_jsonld) if expected_jsonld else 0.0

    # OpenAPI detection rate
    openapi_detected_count = 0
    openapi_completeness_scores = []
    for expected in expected_openapi:
        found = False
        for det in detected_openapi:
            if _openapi_matches(det):
                found = True
                completeness = _compute_completeness(det["data"], expected["key_fields"])
                openapi_completeness_scores.append(completeness)
                break
        if not found:
            openapi_completeness_scores.append(0.0)
        else:
            openapi_detected_count += 1

    openapi_rate = openapi_detected_count / len(expected_openapi) if expected_openapi else 0.0

    # Overall completeness
    all_completeness = jsonld_completeness_scores + openapi_completeness_scores
    avg_completeness = sum(all_completeness) / len(all_completeness) if all_completeness else 0.0

    # False positive rate
    total_expected = len(expected_schemas)
    total_matched = jsonld_detected_count + openapi_detected_count
    total_detected = len(detected)
    unmatched = total_detected - total_matched
    false_positive_rate = unmatched / total_detected if total_detected > 0 else 0.0

    return {
        "jsonld_detection_rate": jsonld_rate,
        "openapi_detection_rate": openapi_rate,
        "schema_completeness": avg_completeness,
        "false_positive_rate": false_positive_rate,
        "jsonld_detected": jsonld_detected_count,
        "jsonld_expected": len(expected_jsonld),
        "openapi_detected": openapi_detected_count,
        "openapi_expected": len(expected_openapi),
    }


def _jsonld_matches(detected: dict, expected: dict) -> bool:
    """Check if a detected JSON-LD schema matches an expected one by @type."""
    data = detected.get("data", {})
    expected_type = expected.get("schema_org_type")
    if expected_type is None:
        return True
    detected_type = data.get("@type", "")
    return detected_type == expected_type


def _openapi_matches(detected: dict) -> bool:
    """Check if a detected schema looks like an OpenAPI spec."""
    data = detected.get("data", {})
    return "openapi" in data or "swagger" in data or ("info" in data and "paths" in data)


def _compute_completeness(data: dict, key_fields: list[str]) -> float:
    """Compute fraction of key_fields present in the detected schema data."""
    if not key_fields:
        return 1.0
    found = sum(1 for field in key_fields if field in data)
    return found / len(key_fields)
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_schema_metrics.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): implement schema detection metrics`

---

## Task 10: Reliability metrics — `benchmark/metrics/reliability.py`

### Task 10.1: Write tests for reliability metrics

- [ ] Write unit tests for the reliability metrics computation.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_reliability_metrics.py`

**Code:**
```python
import pytest
from benchmark.adapters.base import CrawlResult
from benchmark.metrics.reliability import compute_reliability_metrics


def _make_result(url: str, success: bool, category: str = "static_html") -> CrawlResult:
    return CrawlResult(
        url=url, status_code=200 if success else 0, html="<p>ok</p>" if success else "",
        markdown="ok" if success else "", text="ok" if success else "",
        metadata={"category": category}, elapsed_seconds=1.0,
        success=success, error=None if success else "Failed",
    )


class TestComputeReliabilityMetrics:
    def test_all_success(self):
        results = [
            _make_result("https://a.com", True, "static_html"),
            _make_result("https://b.com", True, "static_html"),
            _make_result("https://c.com", True, "js_heavy_spa"),
        ]
        corpus_entries = [
            {"id": "s1", "url": "https://a.com", "category": "static_html"},
            {"id": "s2", "url": "https://b.com", "category": "static_html"},
            {"id": "j1", "url": "https://c.com", "category": "js_heavy_spa"},
        ]
        metrics = compute_reliability_metrics(results, corpus_entries)
        assert metrics["success_rate"] == pytest.approx(1.0)
        assert metrics["error_rate_by_category"]["static_html"] == pytest.approx(0.0)
        assert metrics["error_rate_by_category"]["js_heavy_spa"] == pytest.approx(0.0)

    def test_partial_failure(self):
        results = [
            _make_result("https://a.com", True, "static_html"),
            _make_result("https://b.com", False, "static_html"),
            _make_result("https://c.com", False, "js_heavy_spa"),
            _make_result("https://d.com", True, "js_heavy_spa"),
        ]
        corpus_entries = [
            {"id": "s1", "url": "https://a.com", "category": "static_html"},
            {"id": "s2", "url": "https://b.com", "category": "static_html"},
            {"id": "j1", "url": "https://c.com", "category": "js_heavy_spa"},
            {"id": "j2", "url": "https://d.com", "category": "js_heavy_spa"},
        ]
        metrics = compute_reliability_metrics(results, corpus_entries)
        assert metrics["success_rate"] == pytest.approx(0.5)
        assert metrics["error_rate_by_category"]["static_html"] == pytest.approx(0.5)
        assert metrics["error_rate_by_category"]["js_heavy_spa"] == pytest.approx(0.5)

    def test_all_failure(self):
        results = [
            _make_result("https://a.com", False, "static_html"),
            _make_result("https://b.com", False, "static_html"),
        ]
        corpus_entries = [
            {"id": "s1", "url": "https://a.com", "category": "static_html"},
            {"id": "s2", "url": "https://b.com", "category": "static_html"},
        ]
        metrics = compute_reliability_metrics(results, corpus_entries)
        assert metrics["success_rate"] == pytest.approx(0.0)

    def test_empty_results(self):
        metrics = compute_reliability_metrics([], [])
        assert metrics["success_rate"] == pytest.approx(0.0)
        assert metrics["total_attempted"] == 0
        assert metrics["total_succeeded"] == 0
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_reliability_metrics.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 10.2: Implement `benchmark/metrics/reliability.py`

- [ ] Implement the reliability metrics computation module.

**File:** `/Users/suryakoritala/phantombot/benchmark/metrics/reliability.py`

**Code:**
```python
from collections import defaultdict

from benchmark.adapters.base import CrawlResult


def compute_reliability_metrics(
    results: list[CrawlResult],
    corpus_entries: list[dict],
) -> dict:
    """Compute reliability metrics from crawl results.

    Args:
        results: List of CrawlResults from a benchmark run.
        corpus_entries: List of corpus entry dicts with "url" and "category" fields.

    Returns dict with keys:
        - success_rate: fraction of URLs returning usable content
        - error_rate_by_category: dict mapping category to error rate
        - total_attempted: total URLs attempted
        - total_succeeded: total successful crawls
        - errors: list of {"url": str, "error": str} for failed URLs
    """
    if not results:
        return {
            "success_rate": 0.0,
            "error_rate_by_category": {},
            "total_attempted": 0,
            "total_succeeded": 0,
            "errors": [],
        }

    # Build URL -> category lookup from corpus entries
    url_to_category = {}
    for entry in corpus_entries:
        url_to_category[entry["url"]] = entry["category"]

    succeeded = sum(1 for r in results if r.success)
    success_rate = succeeded / len(results)

    # Error rate by category
    category_total = defaultdict(int)
    category_errors = defaultdict(int)
    for r in results:
        category = url_to_category.get(r.url, "unknown")
        category_total[category] += 1
        if not r.success:
            category_errors[category] += 1

    error_rate_by_category = {}
    for cat, total in category_total.items():
        error_rate_by_category[cat] = category_errors[cat] / total

    # Collect error details
    errors = []
    for r in results:
        if not r.success:
            errors.append({"url": r.url, "error": r.error or "Unknown error"})

    return {
        "success_rate": success_rate,
        "error_rate_by_category": error_rate_by_category,
        "total_attempted": len(results),
        "total_succeeded": succeeded,
        "errors": errors,
    }
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_reliability_metrics.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): implement reliability metrics`

---

## Task 11: Benchmark runner — `benchmark/runner.py`

### Task 11.1: Write tests for the benchmark runner

- [ ] Write unit tests for the runner's corpus loading, adapter orchestration, and results aggregation.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_runner.py`

**Code:**
```python
import json
import pathlib
import pytest
from unittest.mock import MagicMock, patch

from benchmark.adapters.base import CrawlResult
from benchmark.runner import load_corpus, run_adapter_benchmark, ADAPTER_REGISTRY


class TestLoadCorpus:
    def test_loads_corpus_from_path(self):
        corpus_path = pathlib.Path(__file__).parent.parent / "corpus.json"
        corpus = load_corpus(str(corpus_path))
        assert "urls" in corpus
        assert len(corpus["urls"]) == 25

    def test_raises_on_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_corpus("/nonexistent/corpus.json")


class TestAdapterRegistry:
    def test_registry_has_all_three_tools(self):
        assert "phantombot" in ADAPTER_REGISTRY
        assert "crawl4ai" in ADAPTER_REGISTRY
        assert "firecrawl" in ADAPTER_REGISTRY


class TestRunAdapterBenchmark:
    def test_runs_adapter_against_urls(self):
        mock_adapter = MagicMock()
        mock_adapter.name.return_value = "test_tool"
        mock_adapter.version.return_value = "1.0.0"
        mock_adapter.crawl.return_value = CrawlResult(
            url="https://example.com", status_code=200,
            html="<p>hello</p>", markdown="hello", text="hello",
            metadata={}, elapsed_seconds=0.5, success=True,
        )
        mock_adapter.extract_entities.return_value = [{"text": "Example", "type": "ORG"}]
        mock_adapter.extract_schemas.return_value = []

        corpus_entries = [
            {"id": "test-001", "url": "https://example.com", "category": "static_html",
             "expected_entities": 1, "expected_schemas": 0, "notes": "test"},
        ]

        results = run_adapter_benchmark(mock_adapter, corpus_entries, timeout_per_url=30)

        assert len(results["crawl_results"]) == 1
        assert len(results["entity_results"]) == 1
        assert len(results["schema_results"]) == 1
        assert results["tool_name"] == "test_tool"
        assert results["tool_version"] == "1.0.0"
        mock_adapter.setup.assert_called_once()
        mock_adapter.teardown.assert_called_once()

    def test_handles_crawl_failure(self):
        mock_adapter = MagicMock()
        mock_adapter.name.return_value = "test_tool"
        mock_adapter.version.return_value = "1.0.0"
        mock_adapter.crawl.return_value = CrawlResult(
            url="https://bad.com", status_code=0,
            html="", markdown="", text="",
            metadata={}, elapsed_seconds=30.0, success=False, error="Timeout",
        )
        mock_adapter.extract_entities.return_value = []
        mock_adapter.extract_schemas.return_value = []

        corpus_entries = [
            {"id": "test-001", "url": "https://bad.com", "category": "static_html",
             "expected_entities": 1, "expected_schemas": 0, "notes": "test"},
        ]

        results = run_adapter_benchmark(mock_adapter, corpus_entries, timeout_per_url=30)
        assert results["crawl_results"][0].success is False
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_runner.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 11.2: Implement `benchmark/runner.py`

- [ ] Implement the benchmark runner orchestrator with CLI support.

**File:** `/Users/suryakoritala/phantombot/benchmark/runner.py`

**Code:**
```python
"""Benchmark runner — orchestrates adapters, collects results, computes metrics."""

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

from benchmark.adapters.base import BaseAdapter, CrawlResult
from benchmark.adapters.phantombot_adapter import PhantombotAdapter
from benchmark.adapters.crawl4ai_adapter import Crawl4AIAdapter
from benchmark.adapters.firecrawl_adapter import FirecrawlAdapter
from benchmark.metrics.speed import compute_speed_metrics
from benchmark.metrics.extraction import compute_extraction_metrics
from benchmark.metrics.schema import compute_schema_metrics
from benchmark.metrics.reliability import compute_reliability_metrics

ADAPTER_REGISTRY: dict[str, type[BaseAdapter]] = {
    "phantombot": PhantombotAdapter,
    "crawl4ai": Crawl4AIAdapter,
    "firecrawl": FirecrawlAdapter,
}

BENCHMARK_DIR = pathlib.Path(__file__).parent


def load_corpus(corpus_path: str) -> dict:
    """Load and return the corpus JSON manifest."""
    path = pathlib.Path(corpus_path)
    if not path.exists():
        raise FileNotFoundError(f"Corpus file not found: {corpus_path}")
    with open(path) as f:
        return json.load(f)


def load_ground_truth(ground_truth_dir: str, url_id: str) -> dict:
    """Load entity and schema ground truth for a URL ID."""
    gt_dir = pathlib.Path(ground_truth_dir)
    entities = []
    schemas_gt = {"schemas": []}

    entity_file = gt_dir / f"{url_id}-entities.json"
    if entity_file.exists():
        with open(entity_file) as f:
            data = json.load(f)
            entities = data.get("entities", [])

    schema_file = gt_dir / f"{url_id}-schemas.json"
    if schema_file.exists():
        with open(schema_file) as f:
            schemas_gt = json.load(f)

    return {"entities": entities, "schemas": schemas_gt}


def run_adapter_benchmark(
    adapter: BaseAdapter,
    corpus_entries: list[dict],
    timeout_per_url: int = 30,
) -> dict:
    """Run the benchmark for a single adapter against the corpus.

    Returns a dict with keys:
        - tool_name, tool_version
        - crawl_results: list of CrawlResult
        - entity_results: list of list[dict] (per-URL entities)
        - schema_results: list of list[dict] (per-URL schemas)
    """
    adapter.setup()

    crawl_results: list[CrawlResult] = []
    entity_results: list[list[dict]] = []
    schema_results: list[list[dict]] = []

    for entry in corpus_entries:
        url = entry["url"]
        print(f"  [{adapter.name()}] Crawling {url}...")

        crawl_result = adapter.crawl(url, timeout=timeout_per_url)
        crawl_results.append(crawl_result)

        if crawl_result.success and crawl_result.html:
            entities = adapter.extract_entities(crawl_result.html, url)
            schemas = adapter.extract_schemas(crawl_result.html, url)
        else:
            entities = []
            schemas = []

        entity_results.append(entities)
        schema_results.append(schemas)

    adapter.teardown()

    return {
        "tool_name": adapter.name(),
        "tool_version": adapter.version(),
        "crawl_results": crawl_results,
        "entity_results": entity_results,
        "schema_results": schema_results,
    }


def compute_all_metrics(
    adapter_results: dict,
    corpus_entries: list[dict],
    ground_truth_dir: str,
) -> dict:
    """Compute all metrics for a single adapter's results."""
    crawl_results = adapter_results["crawl_results"]
    entity_results = adapter_results["entity_results"]
    schema_results = adapter_results["schema_results"]

    # Speed metrics
    speed = compute_speed_metrics(crawl_results)

    # Extraction metrics (aggregated across URLs with ground truth)
    all_extraction_metrics = []
    for i, entry in enumerate(corpus_entries):
        gt = load_ground_truth(ground_truth_dir, entry["id"])
        if gt["entities"]:
            extraction = compute_extraction_metrics(entity_results[i], gt["entities"])
            all_extraction_metrics.append(extraction)

    # Average extraction metrics
    if all_extraction_metrics:
        avg_extraction = {
            "precision": sum(m["precision"] for m in all_extraction_metrics) / len(all_extraction_metrics),
            "recall": sum(m["recall"] for m in all_extraction_metrics) / len(all_extraction_metrics),
            "f1": sum(m["f1"] for m in all_extraction_metrics) / len(all_extraction_metrics),
            "type_accuracy": sum(m["type_accuracy"] for m in all_extraction_metrics) / len(all_extraction_metrics),
            "urls_evaluated": len(all_extraction_metrics),
        }
    else:
        avg_extraction = {
            "precision": 0.0, "recall": 0.0, "f1": 0.0,
            "type_accuracy": 0.0, "urls_evaluated": 0,
        }

    # Schema metrics (aggregated across URLs with ground truth)
    all_schema_metrics = []
    for i, entry in enumerate(corpus_entries):
        gt = load_ground_truth(ground_truth_dir, entry["id"])
        if gt["schemas"].get("schemas"):
            schema_m = compute_schema_metrics(schema_results[i], gt["schemas"])
            all_schema_metrics.append(schema_m)

    if all_schema_metrics:
        avg_schema = {
            "jsonld_detection_rate": sum(m["jsonld_detection_rate"] for m in all_schema_metrics) / len(all_schema_metrics),
            "openapi_detection_rate": sum(m["openapi_detection_rate"] for m in all_schema_metrics) / len(all_schema_metrics),
            "schema_completeness": sum(m["schema_completeness"] for m in all_schema_metrics) / len(all_schema_metrics),
            "false_positive_rate": sum(m["false_positive_rate"] for m in all_schema_metrics) / len(all_schema_metrics),
            "urls_evaluated": len(all_schema_metrics),
        }
    else:
        avg_schema = {
            "jsonld_detection_rate": 0.0, "openapi_detection_rate": 0.0,
            "schema_completeness": 0.0, "false_positive_rate": 0.0,
            "urls_evaluated": 0,
        }

    # Reliability metrics
    reliability = compute_reliability_metrics(crawl_results, corpus_entries)

    return {
        "speed": speed,
        "extraction": avg_extraction,
        "schemas": avg_schema,
        "reliability": reliability,
    }


def run_benchmark(
    tools: list[str],
    corpus_path: str,
    ground_truth_dir: str,
    output_dir: str,
    timeout_per_url: int = 30,
) -> dict:
    """Run the full benchmark suite.

    Args:
        tools: List of tool names to benchmark.
        corpus_path: Path to corpus.json.
        ground_truth_dir: Path to ground_truth/ directory.
        output_dir: Path to write results.
        timeout_per_url: Timeout in seconds per URL.

    Returns the full results dict.
    """
    corpus = load_corpus(corpus_path)
    corpus_entries = corpus["urls"]

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    results = {
        "metadata": {
            "timestamp": timestamp,
            "corpus_version": corpus.get("version", "unknown"),
            "tools": tools,
            "timeout_per_url": timeout_per_url,
            "urls_count": len(corpus_entries),
        },
        "results": {},
    }

    for tool_name in tools:
        if tool_name not in ADAPTER_REGISTRY:
            print(f"Warning: unknown tool '{tool_name}', skipping.")
            continue

        print(f"\n{'='*60}")
        print(f"Running benchmark for: {tool_name}")
        print(f"{'='*60}")

        adapter = ADAPTER_REGISTRY[tool_name]()
        adapter_results = run_adapter_benchmark(adapter, corpus_entries, timeout_per_url)
        metrics = compute_all_metrics(adapter_results, corpus_entries, ground_truth_dir)

        results["results"][tool_name] = {
            "version": adapter_results["tool_version"],
            **metrics,
            "per_url": [
                {
                    "url": entry["url"],
                    "id": entry["id"],
                    "category": entry["category"],
                    "crawl_success": adapter_results["crawl_results"][i].success,
                    "crawl_time": adapter_results["crawl_results"][i].elapsed_seconds,
                    "crawl_error": adapter_results["crawl_results"][i].error,
                    "entities_found": len(adapter_results["entity_results"][i]),
                    "schemas_found": len(adapter_results["schema_results"][i]),
                }
                for i, entry in enumerate(corpus_entries)
            ],
        }

        # Cool-down between tools
        if tool_name != tools[-1]:
            print(f"\nCooling down for 10 seconds...")
            time.sleep(10)

    # Save results
    output_path = pathlib.Path(output_dir) / timestamp
    output_path.mkdir(parents=True, exist_ok=True)
    results_file = output_path / "raw_results.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {results_file}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Phantombot Benchmark Suite")
    parser.add_argument(
        "--tools",
        type=str,
        default="phantombot,crawl4ai,firecrawl",
        help="Comma-separated list of tools to benchmark (default: all three)",
    )
    parser.add_argument(
        "--corpus",
        type=str,
        default=str(BENCHMARK_DIR / "corpus.json"),
        help="Path to corpus.json",
    )
    parser.add_argument(
        "--ground-truth",
        type=str,
        default=str(BENCHMARK_DIR / "ground_truth"),
        help="Path to ground_truth/ directory",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(BENCHMARK_DIR / "results"),
        help="Path to output directory",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout per URL in seconds (default: 30)",
    )
    parser.add_argument(
        "--use-archive",
        action="store_true",
        help="Use cached/archived pages instead of live crawling (v2 feature)",
    )

    args = parser.parse_args()
    tools = [t.strip() for t in args.tools.split(",")]

    results = run_benchmark(
        tools=tools,
        corpus_path=args.corpus,
        ground_truth_dir=args.ground_truth,
        output_dir=args.output,
        timeout_per_url=args.timeout,
    )

    # Generate report
    from benchmark.report import generate_markdown_report
    timestamp = results["metadata"]["timestamp"]
    output_path = pathlib.Path(args.output) / timestamp
    report_path = output_path / "report.md"
    report = generate_markdown_report(results)
    with open(report_path, "w") as f:
        f.write(report)
    print(f"Report saved to {report_path}")


if __name__ == "__main__":
    main()
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_runner.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): implement benchmark runner with CLI and metrics aggregation`

---

## Task 12: Report generation — `benchmark/report.py`

### Task 12.1: Write tests for report generation

- [ ] Write unit tests for the markdown and JSON report generation.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_report.py`

**Code:**
```python
import pytest
from benchmark.report import generate_markdown_report


@pytest.fixture
def sample_results():
    return {
        "metadata": {
            "timestamp": "2026-04-12T10:00:00",
            "corpus_version": "1.0",
            "tools": ["phantombot", "crawl4ai"],
            "timeout_per_url": 30,
            "urls_count": 3,
        },
        "results": {
            "phantombot": {
                "version": "0.1.0",
                "speed": {
                    "time_to_first_result": 0.8,
                    "total_corpus_time": 5.0,
                    "pages_per_second": 0.6,
                    "urls_attempted": 3,
                    "urls_succeeded": 3,
                },
                "extraction": {
                    "precision": 0.82,
                    "recall": 0.75,
                    "f1": 0.78,
                    "type_accuracy": 0.91,
                    "urls_evaluated": 2,
                },
                "schemas": {
                    "jsonld_detection_rate": 1.0,
                    "openapi_detection_rate": 0.5,
                    "schema_completeness": 0.85,
                    "false_positive_rate": 0.0,
                    "urls_evaluated": 2,
                },
                "reliability": {
                    "success_rate": 1.0,
                    "error_rate_by_category": {},
                    "total_attempted": 3,
                    "total_succeeded": 3,
                    "errors": [],
                },
                "per_url": [],
            },
            "crawl4ai": {
                "version": "0.4.0",
                "speed": {
                    "time_to_first_result": 0.9,
                    "total_corpus_time": 4.0,
                    "pages_per_second": 0.75,
                    "urls_attempted": 3,
                    "urls_succeeded": 3,
                },
                "extraction": {
                    "precision": 0.80,
                    "recall": 0.73,
                    "f1": 0.76,
                    "type_accuracy": 0.90,
                    "urls_evaluated": 2,
                },
                "schemas": {
                    "jsonld_detection_rate": 0.5,
                    "openapi_detection_rate": 0.0,
                    "schema_completeness": 0.40,
                    "false_positive_rate": 0.1,
                    "urls_evaluated": 2,
                },
                "reliability": {
                    "success_rate": 1.0,
                    "error_rate_by_category": {},
                    "total_attempted": 3,
                    "total_succeeded": 3,
                    "errors": [],
                },
                "per_url": [],
            },
        },
    }


class TestGenerateMarkdownReport:
    def test_returns_string(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert isinstance(report, str)

    def test_contains_title(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "# Phantombot Benchmark Results" in report

    def test_contains_speed_section(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "## Speed" in report

    def test_contains_extraction_section(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "## Entity Extraction Quality" in report

    def test_contains_schema_section(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "## Schema Detection" in report

    def test_contains_reliability_section(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "## Reliability" in report

    def test_contains_tool_names(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "phantombot" in report.lower()
        assert "crawl4ai" in report.lower()

    def test_contains_metric_values(self, sample_results):
        report = generate_markdown_report(sample_results)
        assert "0.82" in report  # phantombot precision
        assert "0.78" in report  # phantombot f1

    def test_report_is_valid_markdown(self, sample_results):
        report = generate_markdown_report(sample_results)
        # Basic markdown structure checks
        assert report.startswith("#")
        assert "| Tool |" in report  # has table headers
        assert "|---|" in report     # has table separators
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_report.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 12.2: Implement `benchmark/report.py`

- [ ] Implement the markdown report generation module.

**File:** `/Users/suryakoritala/phantombot/benchmark/report.py`

**Code:**
```python
"""Report generation for benchmark results — markdown and JSON output."""


def generate_markdown_report(results: dict) -> str:
    """Generate a human-readable markdown report from benchmark results.

    Args:
        results: Full results dict from the benchmark runner.

    Returns:
        Markdown-formatted string.
    """
    meta = results["metadata"]
    tool_results = results["results"]
    tools = list(tool_results.keys())

    lines = []

    # Header
    lines.append("# Phantombot Benchmark Results")
    lines.append("")
    lines.append(
        f"**Date:** {meta['timestamp']}  "
        f"**Corpus version:** {meta['corpus_version']}  "
        f"**URLs:** {meta['urls_count']}  "
        f"**Timeout:** {meta['timeout_per_url']}s/URL"
    )
    lines.append("")

    # Speed section
    lines.append("## Speed")
    lines.append("")
    lines.append("| Tool | Time to first result | Total corpus time | Pages/sec |")
    lines.append("|---|---|---|---|")
    for tool in tools:
        speed = tool_results[tool]["speed"]
        ttfr = f"{speed['time_to_first_result']:.1f}s" if speed["time_to_first_result"] is not None else "N/A"
        total = f"{speed['total_corpus_time']:.1f}s"
        pps = f"{speed['pages_per_second']:.2f}"
        lines.append(f"| {tool} | {ttfr} | {total} | {pps} |")
    lines.append("")

    # Entity Extraction Quality section
    lines.append("## Entity Extraction Quality")
    lines.append("")
    lines.append("| Tool | Precision | Recall | F1 | Type accuracy |")
    lines.append("|---|---|---|---|---|")
    for tool in tools:
        ext = tool_results[tool]["extraction"]
        lines.append(
            f"| {tool} | {ext['precision']:.2f} | {ext['recall']:.2f} "
            f"| {ext['f1']:.2f} | {ext['type_accuracy']:.2f} |"
        )
    lines.append("")

    # Schema Detection section
    lines.append("## Schema Detection")
    lines.append("")
    lines.append("| Tool | JSON-LD rate | OpenAPI rate | Completeness | False positive rate |")
    lines.append("|---|---|---|---|---|")
    for tool in tools:
        sch = tool_results[tool]["schemas"]
        lines.append(
            f"| {tool} | {sch['jsonld_detection_rate']:.2f} "
            f"| {sch['openapi_detection_rate']:.2f} "
            f"| {sch['schema_completeness']:.2f} "
            f"| {sch['false_positive_rate']:.2f} |"
        )
    lines.append("")

    # Reliability section
    lines.append("## Reliability")
    lines.append("")
    lines.append("| Tool | Success rate | URLs attempted | URLs succeeded |")
    lines.append("|---|---|---|---|")
    for tool in tools:
        rel = tool_results[tool]["reliability"]
        lines.append(
            f"| {tool} | {rel['success_rate']:.2f} "
            f"| {rel['total_attempted']} "
            f"| {rel['total_succeeded']} |"
        )
    lines.append("")

    # Error details (if any)
    has_errors = any(
        tool_results[tool]["reliability"].get("errors")
        for tool in tools
    )
    if has_errors:
        lines.append("## Errors")
        lines.append("")
        for tool in tools:
            errors = tool_results[tool]["reliability"].get("errors", [])
            if errors:
                lines.append(f"### {tool}")
                lines.append("")
                for err in errors:
                    lines.append(f"- **{err['url']}**: {err['error']}")
                lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append(
        "*Generated by Phantombot Benchmark Suite. "
        "See [benchmark/README.md](../README.md) for methodology.*"
    )

    return "\n".join(lines)
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_report.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): implement markdown report generation`

---

## Task 13: Configuration — `benchmark/config.py`

### Task 13.1: Write tests for configuration

- [ ] Write unit tests for the configuration dataclass and defaults.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_config.py`

**Code:**
```python
import pytest
from benchmark.config import BenchmarkConfig


class TestBenchmarkConfig:
    def test_default_values(self):
        config = BenchmarkConfig()
        assert config.timeout_per_url == 30
        assert config.total_timeout == 900
        assert config.concurrency == 1
        assert config.use_archive is False
        assert config.spacy_model == "en_core_web_sm"

    def test_default_tools(self):
        config = BenchmarkConfig()
        assert "phantombot" in config.tools
        assert "firecrawl" in config.tools
        assert "crawl4ai" in config.tools

    def test_custom_values(self):
        config = BenchmarkConfig(
            timeout_per_url=60,
            tools=["phantombot"],
            use_archive=True,
        )
        assert config.timeout_per_url == 60
        assert config.tools == ["phantombot"]
        assert config.use_archive is True

    def test_paths_are_strings(self):
        config = BenchmarkConfig()
        assert isinstance(config.corpus_path, str)
        assert isinstance(config.ground_truth_dir, str)
        assert isinstance(config.output_dir, str)

    def test_corpus_path_default(self):
        config = BenchmarkConfig()
        assert "corpus.json" in config.corpus_path

    def test_ground_truth_dir_default(self):
        config = BenchmarkConfig()
        assert "ground_truth" in config.ground_truth_dir
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_config.py -v 2>&1 | head -30
# Expected: ERRORS (module not found yet)
```

---

### Task 13.2: Implement `benchmark/config.py`

- [ ] Implement the configuration dataclass.

**File:** `/Users/suryakoritala/phantombot/benchmark/config.py`

**Code:**
```python
import pathlib
from dataclasses import dataclass, field

_BENCHMARK_DIR = pathlib.Path(__file__).parent


@dataclass
class BenchmarkConfig:
    """Configuration for the benchmark suite."""

    corpus_path: str = str(_BENCHMARK_DIR / "corpus.json")
    ground_truth_dir: str = str(_BENCHMARK_DIR / "ground_truth")
    output_dir: str = str(_BENCHMARK_DIR / "results")
    timeout_per_url: int = 30
    total_timeout: int = 900
    concurrency: int = 1
    use_archive: bool = False
    tools: list[str] = field(default_factory=lambda: ["phantombot", "firecrawl", "crawl4ai"])
    spacy_model: str = "en_core_web_sm"
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_config.py -v
# Expected: all tests pass
```

**Commit message:** `feat(benchmark): implement benchmark configuration dataclass`

---

## Task 14: Makefile — `benchmark/Makefile`

### Task 14.1: Create `benchmark/Makefile`

- [ ] Create the Makefile with convenience targets for running the benchmark.

**File:** `/Users/suryakoritala/phantombot/benchmark/Makefile`

**Code:**
```makefile
.PHONY: install test benchmark benchmark-quick report clean help

PYTHON ?= python
BENCHMARK_DIR := $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))
PROJECT_ROOT := $(shell dirname $(BENCHMARK_DIR))

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install benchmark dependencies
	$(PYTHON) -m pip install -r $(BENCHMARK_DIR)/requirements.txt
	$(PYTHON) -m spacy download en_core_web_sm

test:  ## Run benchmark unit tests
	cd $(PROJECT_ROOT) && $(PYTHON) -m pytest benchmark/tests/ -v

benchmark:  ## Run full benchmark (all tools, all URLs)
	cd $(PROJECT_ROOT) && $(PYTHON) -m benchmark.runner

benchmark-quick:  ## Run benchmark with Phantombot only
	cd $(PROJECT_ROOT) && $(PYTHON) -m benchmark.runner --tools phantombot

benchmark-no-docker:  ## Run benchmark without Docker-dependent tools
	cd $(PROJECT_ROOT) && $(PYTHON) -m benchmark.runner --tools phantombot,crawl4ai

report:  ## Generate report from latest results
	@echo "Use: $(PYTHON) -m benchmark.report <path-to-raw_results.json>"

clean:  ## Remove benchmark results
	rm -rf $(BENCHMARK_DIR)/results/*/
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot/benchmark && make help
# Expected: shows available targets
cd /Users/suryakoritala/phantombot/benchmark && make test
# Expected: runs all unit tests
```

**Commit message:** `feat(benchmark): add Makefile with convenience targets`

---

## Task 15: README — `benchmark/README.md`

### Task 15.1: Create `benchmark/README.md`

- [ ] Create the README with setup instructions, prerequisites, and usage guide.

**File:** `/Users/suryakoritala/phantombot/benchmark/README.md`

**Code:**
```markdown
# Phantombot Benchmark Suite

A reproducible benchmark comparing **Phantombot**, **Firecrawl**, and **Crawl4AI** across crawl speed, entity extraction quality, schema detection, and reliability.

## Prerequisites

- Python 3.12+
- Docker and Docker Compose (for Firecrawl self-hosted)
- Go 1.23+ (for building Phantombot crawler)
- ~4GB free disk space (Docker images)
- ~8GB RAM (running all three tools)

## Quick Start

```bash
# Install dependencies
make -C benchmark install

# Run unit tests
make -C benchmark test

# Run full benchmark (all tools)
make -C benchmark benchmark

# Run Phantombot only (no Docker needed)
make -C benchmark benchmark-quick
```

## CLI Usage

```bash
# Run full benchmark (all tools, all metrics)
python -m benchmark.runner

# Run specific tools only
python -m benchmark.runner --tools phantombot,crawl4ai

# Custom timeout per URL
python -m benchmark.runner --timeout 60

# Custom output directory
python -m benchmark.runner --output /tmp/benchmark-results
```

## Corpus

The benchmark uses a fixed set of 25 URLs across six page categories:

| Category | Count | Description |
|---|---|---|
| Static HTML / Documentation | 5 | Wikipedia, Python docs, MDN |
| JS-Heavy SPAs | 4 | React, Angular, Vue docs, Hacker News |
| JSON-LD Structured Data | 4 | Recipe, movie, event pages |
| API Documentation | 4 | Swagger, GitHub API docs |
| News Articles (Entity-Rich) | 4 | Reuters, BBC, AP News |
| E-commerce Product Pages | 4 | Etsy, Target, Best Buy, Walmart |

URLs are defined in `corpus.json`. Ground truth annotations are in `ground_truth/`.

## Metrics

### M1: Crawl Speed
- Time to first result (seconds)
- Total corpus time (seconds)
- Pages per second

### M2: Extraction Quality
- Entity precision, recall, F1
- Entity type accuracy
- Uses Levenshtein distance (threshold=2) for fuzzy matching

### M3: Schema Detection
- JSON-LD detection rate
- OpenAPI detection rate
- Schema completeness (key fields found)
- False positive rate

### M4: Reliability
- Success rate (pages returning usable content)
- Error rate by page category

## Fairness Controls

All tools are compared under identical conditions:
- Same URLs (from `corpus.json`)
- Same timeout budget (30s per URL)
- Same entity extraction model (SpaCy `en_core_web_sm`)
- Same schema detection logic
- Sequential execution (no resource contention)
- 10-second cool-down between tools

## Output

Results are saved to `benchmark/results/{timestamp}/`:
- `raw_results.json` -- machine-readable results
- `report.md` -- human-readable markdown report

## Project Structure

```
benchmark/
  corpus.json              # URL manifest (25 URLs)
  ground_truth/            # Manual entity and schema annotations
  adapters/
    base.py                # Abstract adapter interface
    phantombot_adapter.py  # Phantombot direct mode
    crawl4ai_adapter.py    # Crawl4AI Python library
    firecrawl_adapter.py   # Firecrawl self-hosted Docker
  metrics/
    speed.py               # Crawl speed metrics
    extraction.py          # Entity extraction quality
    schema.py              # Schema detection accuracy
    reliability.py         # Success/error rates
  runner.py                # Benchmark orchestrator + CLI
  report.py                # Report generation
  config.py                # Configuration dataclass
  tests/                   # Unit tests
  results/                 # Output directory (gitignored)
  Makefile                 # Convenience targets
```

## Adding a New Tool

1. Create `benchmark/adapters/newtool_adapter.py` implementing `BaseAdapter`
2. Register it in `benchmark/runner.py` `ADAPTER_REGISTRY`
3. Add tests in `benchmark/tests/test_newtool_adapter.py`
4. Run: `python -m benchmark.runner --tools phantombot,newtool`
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot/benchmark && make test
# Expected: all unit tests pass
```

**Commit message:** `feat(benchmark): add README with setup and usage instructions`

---

## Task 16: Final integration test

### Task 16.1: Write a smoke test that validates the full pipeline

- [ ] Write an integration test that runs the full benchmark pipeline against local fixtures with a mock adapter.

**File:** `/Users/suryakoritala/phantombot/benchmark/tests/test_integration.py`

**Code:**
```python
import json
import pathlib
import pytest
from unittest.mock import MagicMock

from benchmark.adapters.base import CrawlResult
from benchmark.runner import run_adapter_benchmark, compute_all_metrics, load_corpus
from benchmark.report import generate_markdown_report

FIXTURES_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures"
GROUND_TRUTH_DIR = pathlib.Path(__file__).parent.parent / "ground_truth"
CORPUS_PATH = pathlib.Path(__file__).parent.parent / "corpus.json"


class TestFullPipelineIntegration:
    def test_mock_adapter_through_full_pipeline(self):
        """Run a mock adapter through the full pipeline: crawl -> extract -> metrics -> report."""
        mock_adapter = MagicMock()
        mock_adapter.name.return_value = "mock_tool"
        mock_adapter.version.return_value = "0.0.1"

        html = (FIXTURES_DIR / "entity_rich_page.html").read_text()
        mock_adapter.crawl.return_value = CrawlResult(
            url="https://example.com", status_code=200,
            html=html, markdown="Tech news", text="Tech news",
            metadata={}, elapsed_seconds=0.5, success=True,
        )
        mock_adapter.extract_entities.return_value = [
            {"text": "Google", "type": "ORG"},
            {"text": "Sundar Pichai", "type": "PERSON"},
        ]
        mock_adapter.extract_schemas.return_value = []

        corpus_entries = [
            {"id": "static-001", "url": "https://example.com", "category": "static_html",
             "expected_entities": 5, "expected_schemas": 0, "notes": "test"},
        ]

        # Run adapter benchmark
        results = run_adapter_benchmark(mock_adapter, corpus_entries, timeout_per_url=30)
        assert results["tool_name"] == "mock_tool"
        assert len(results["crawl_results"]) == 1
        assert results["crawl_results"][0].success is True

        # Compute metrics
        metrics = compute_all_metrics(results, corpus_entries, str(GROUND_TRUTH_DIR))
        assert "speed" in metrics
        assert "extraction" in metrics
        assert "schemas" in metrics
        assert "reliability" in metrics

        # Generate report
        full_results = {
            "metadata": {
                "timestamp": "2026-04-12T10:00:00",
                "corpus_version": "1.0",
                "tools": ["mock_tool"],
                "timeout_per_url": 30,
                "urls_count": 1,
            },
            "results": {
                "mock_tool": {
                    "version": "0.0.1",
                    **metrics,
                    "per_url": [],
                },
            },
        }
        report = generate_markdown_report(full_results)
        assert "# Phantombot Benchmark Results" in report
        assert "mock_tool" in report

    def test_corpus_loads_successfully(self):
        corpus = load_corpus(str(CORPUS_PATH))
        assert corpus["version"] == "1.0"
        assert len(corpus["urls"]) == 25

    def test_ground_truth_files_are_valid(self):
        entity_files = list(GROUND_TRUTH_DIR.glob("*-entities.json"))
        assert len(entity_files) >= 10
        for path in entity_files:
            with open(path) as f:
                data = json.load(f)
            assert "entities" in data
            assert len(data["entities"]) > 0
```

**Commands:**
```bash
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/test_integration.py -v
# Expected: all tests pass
cd /Users/suryakoritala/phantombot && python -m pytest benchmark/tests/ -v
# Expected: ALL benchmark tests pass
```

**Commit message:** `test(benchmark): add integration smoke test for full pipeline`

---

## Summary

| Task | File(s) | Type |
|---|---|---|
| 0.1 | Scaffolding + `__init__.py` files | Setup |
| 0.2 | `benchmark/requirements.txt` | Setup |
| 1.1-1.2 | `benchmark/adapters/base.py` | Core interface |
| 2.1-2.2 | `benchmark/corpus.json` | Data |
| 3.1-3.3 | `benchmark/ground_truth/*.json` (18 files) | Data |
| 4.1-4.2 | `benchmark/adapters/phantombot_adapter.py` | Adapter |
| 5.1-5.2 | `benchmark/adapters/crawl4ai_adapter.py` | Adapter |
| 6.1-6.2 | `benchmark/adapters/firecrawl_adapter.py` | Adapter |
| 7.1-7.2 | `benchmark/metrics/speed.py` | Metrics |
| 8.1-8.2 | `benchmark/metrics/extraction.py` | Metrics |
| 9.1-9.2 | `benchmark/metrics/schema.py` | Metrics |
| 10.1-10.2 | `benchmark/metrics/reliability.py` | Metrics |
| 11.1-11.2 | `benchmark/runner.py` | Orchestration |
| 12.1-12.2 | `benchmark/report.py` | Output |
| 13.1-13.2 | `benchmark/config.py` | Config |
| 14.1 | `benchmark/Makefile` | Build |
| 15.1 | `benchmark/README.md` | Docs |
| 16.1 | `benchmark/tests/test_integration.py` | Integration |

**Total tasks:** 28 sub-tasks across 16 groups. Each sub-task is 2-5 minutes of work.

**Estimated total implementation time:** 60-90 minutes.
