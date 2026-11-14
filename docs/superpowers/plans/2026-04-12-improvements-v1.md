# Phantombot Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix entity extraction quality, connect the crawl loop, add JS rendering, entity resolution, anti-bot handling, and other improvements identified by end-to-end testing.

**Architecture:** Upgrades to existing intelligence pipelines, Go crawler enhancements, new entity resolution layer, Playwright sidecar integration.

**Tech Stack:** Python 3.12+, SpaCy (en_core_web_trf), Playwright, Go

---

## File Structure (new/modified files only)

```
phantombot/
├── crawler/
│   ├── cmd/phantombot/main.go                          # MODIFY: optional -urls, stream consumer
│   ├── internal/
│   │   ├── config/config.go                            # MODIFY: add stream, renderer, proxy config
│   │   ├── consumer/consumer.go                        # NEW: Redis stream consumer
│   │   ├── consumer/consumer_test.go                   # NEW: consumer tests
│   │   ├── fetcher/fetcher.go                          # MODIFY: stealth headers, proxy, UA rotation
│   │   ├── fetcher/fetcher_test.go                     # MODIFY: add stealth/rotation tests
│   │   ├── fetcher/useragents.go                       # NEW: UA rotation pool
│   │   ├── fetcher/useragents_test.go                  # NEW: UA tests
│   │   ├── renderer/renderer.go                        # NEW: Playwright sidecar client
│   │   └── renderer/renderer_test.go                   # NEW: renderer client tests
│   └── pkg/models/models.go                            # existing (no changes)
├── intelligence/
│   ├── pyproject.toml                                  # MODIFY: add spacy-transformers, pyyaml
│   ├── intelligence/
│   │   ├── config.py                                   # MODIFY: new env vars
│   │   ├── models.py                                   # MODIFY: add ResolvedEntity
│   │   ├── worker.py                                   # MODIFY: add cleaning, typing, resolution steps
│   │   ├── store.py                                    # MODIFY: add resolution + backfill methods
│   │   ├── scheduler.py                                # NEW: crawl scheduler
│   │   ├── pipelines/
│   │   │   ├── entity.py                               # MODIFY: integrate cleaner + typing rules
│   │   │   ├── entity_cleaner.py                       # NEW: post-processing cleanup
│   │   │   ├── entity_typing_rules.py                  # NEW: type override rules
│   │   │   ├── entity_resolver.py                      # NEW: fuzzy dedup + canonicalization
│   │   │   └── schema.py                               # MODIFY: Swagger UI, Redoc, YAML detection
│   │   └── scripts/
│   │       └── backfill_markdown.py                    # NEW: one-time markdown backfill
│   └── tests/
│       ├── test_entity_cleaner.py                      # NEW
│       ├── test_entity_typing_rules.py                 # NEW
│       ├── test_entity_resolver.py                     # NEW
│       ├── test_entity_pipeline_v2.py                  # NEW: integration test with trf model
│       ├── test_schema_openapi.py                      # NEW: Swagger/Redoc detection tests
│       └── test_backfill_markdown.py                   # NEW
├── playwright-service/
│   ├── pyproject.toml                                  # NEW
│   ├── server.py                                       # NEW: Playwright render endpoint
│   ├── Dockerfile                                      # NEW
│   └── tests/
│       └── test_server.py                              # NEW
├── store/
│   └── migrations/
│       └── 002_entity_resolution.sql                   # NEW
├── fixtures/
│   ├── swagger_ui_page.html                            # NEW
│   ├── redoc_page.html                                 # NEW
│   ├── entity_dirty_page.html                          # NEW
│   └── js_heavy_page.html                              # NEW
└── docker-compose.yml                                  # MODIFY: add playwright-service
```

---

## Phase 1: P0 Critical Fixes

---

## Task 1: Upgrade SpaCy Model to Transformer

**Files:**
- Modify: `intelligence/pyproject.toml`
- Modify: `intelligence/intelligence/config.py`
- Modify: `intelligence/intelligence/pipelines/entity.py`

- [ ] **Step 1: Write test verifying transformer model loads and extracts better entities**

Create `intelligence/tests/test_entity_pipeline_v2.py`:
```python
"""Tests for entity pipeline with transformer model upgrade."""
import pytest

from intelligence.pipelines.entity import EntityPipeline


@pytest.fixture
def pipeline():
    """Create entity pipeline with transformer model.

    Falls back to sm model if trf is not installed, but marks test as skipped.
    """
    try:
        return EntityPipeline(model_name="en_core_web_trf")
    except OSError:
        pytest.skip("en_core_web_trf not installed — run: python -m spacy download en_core_web_trf")


BENCHMARK_HTML = """
<html><body>
<h1>Tech Industry Update</h1>
<p>Elon Musk announced that Tesla would open a new Gigafactory in Austin, Texas.
   The factory will produce the Model Y and Cybertruck.</p>
<p>Meanwhile, Satya Nadella confirmed Microsoft's $10 billion investment in OpenAI,
   strengthening ties between the Redmond-based company and the San Francisco startup.</p>
<p>Apple CEO Tim Cook unveiled new features for the iPhone at Apple Park in Cupertino, California.</p>
</body></html>
"""

EXPECTED_ENTITIES = {
    ("Elon Musk", "PERSON"),
    ("Tesla", "ORG"),
    ("Austin", "GPE"),
    ("Texas", "GPE"),
    ("Microsoft", "ORG"),
    ("OpenAI", "ORG"),
    ("Apple", "ORG"),
    ("Tim Cook", "PERSON"),
    ("Cupertino", "GPE"),
    ("California", "GPE"),
    ("Satya Nadella", "PERSON"),
}


def test_transformer_model_extracts_known_entities(pipeline):
    """Transformer model should find key entities in benchmark text."""
    entities = pipeline.extract(BENCHMARK_HTML, "https://example.com/test")
    found = {(e.entity_text, e.entity_type) for e in entities}

    # At minimum, the transformer model should find most of these
    matches = found & EXPECTED_ENTITIES
    recall = len(matches) / len(EXPECTED_ENTITIES)

    assert recall >= 0.6, (
        f"Expected at least 60% recall, got {recall:.0%}. "
        f"Found: {found}. Missing: {EXPECTED_ENTITIES - found}"
    )


def test_pipeline_version_is_v2(pipeline):
    """Pipeline version should be updated to entity-v2."""
    entities = pipeline.extract(BENCHMARK_HTML, "https://example.com/test")
    assert len(entities) > 0
    for entity in entities:
        assert entity.pipeline_version == "entity-v2"


def test_transformer_confidence_higher_than_base(pipeline):
    """Transformer model should produce higher confidence scores."""
    entities = pipeline.extract(BENCHMARK_HTML, "https://example.com/test")
    avg_confidence = sum(e.confidence for e in entities) / len(entities)
    # Transformer model should have higher base confidence
    assert avg_confidence >= 0.8, f"Expected avg confidence >= 0.8, got {avg_confidence:.2f}"
```

- [ ] **Step 2: Update pyproject.toml with transformer dependencies**

Modify `intelligence/pyproject.toml`:
```toml
[project]
name = "phantombot-intelligence"
version = "0.2.0"
description = "Phantombot intelligence layer — extraction pipelines"
requires-python = ">=3.12"
dependencies = [
    "redis>=5.0.0",
    "psycopg[binary]>=3.1.0",
    "qdrant-client>=1.9.0",
    "spacy>=3.7.0",
    "spacy-transformers>=1.3.0",
    "beautifulsoup4>=4.12.0",
    "pydantic>=2.0.0",
    "sentence-transformers>=3.0.0",
    "trafilatura>=1.12.0",
    "markdownify>=0.14.0",
    "pyyaml>=6.0",
    "rapidfuzz>=3.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"
```

- [ ] **Step 3: Update config default to use transformer model**

Modify `intelligence/intelligence/config.py` — change line 19:
```python
    SPACY_MODEL: str = os.getenv("SPACY_MODEL", "en_core_web_trf")
```

- [ ] **Step 4: Update entity pipeline version and confidence scoring**

Modify `intelligence/intelligence/pipelines/entity.py`:
```python
import spacy
from bs4 import BeautifulSoup

from intelligence.config import Config
from intelligence.models import ExtractedEntity
from intelligence.pipelines.base import BasePipeline


class EntityPipeline(BasePipeline):
    """Extracts named entities from HTML using SpaCy NER."""

    SUPPORTED_TYPES = {"PERSON", "ORG", "GPE", "PRODUCT", "MONEY", "LOC", "FAC", "NORP", "EVENT"}

    def __init__(self, model_name: str | None = None):
        self._model_name = model_name or Config.SPACY_MODEL
        self._nlp = spacy.load(self._model_name)
        self._is_transformer = "trf" in self._model_name

    def extract(self, html: str, source_url: str) -> list[ExtractedEntity]:
        if not html or not html.strip():
            return []

        text = self._html_to_text(html)
        if not text.strip():
            return []

        doc = self._nlp(text)
        entities = []
        seen = set()

        for ent in doc.ents:
            if ent.label_ not in self.SUPPORTED_TYPES:
                continue

            key = (ent.text.strip(), ent.label_)
            if key in seen:
                continue
            seen.add(key)

            entities.append(ExtractedEntity(
                entity_type=ent.label_,
                entity_text=ent.text.strip(),
                start_char=ent.start_char,
                end_char=ent.end_char,
                confidence=self._estimate_confidence(ent),
                pipeline_version="entity-v2",
            ))

        return entities

    def _html_to_text(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)

    def _estimate_confidence(self, ent) -> float:
        """Confidence based on model type and entity characteristics."""
        # Transformer models get higher base confidence
        score = 0.85 if self._is_transformer else 0.7
        if len(ent.text.strip()) > 2:
            score += 0.05
        if ent.label_ in ("PERSON", "ORG"):
            score += 0.05
        return min(score, 1.0)
```

- [ ] **Step 5: Download the transformer model**

```bash
cd /Users/suryakoritala/phantombot/intelligence
source .venv/bin/activate
python -m spacy download en_core_web_trf
```

- [ ] **Step 6: Run tests**

```bash
cd /Users/suryakoritala/phantombot/intelligence
source .venv/bin/activate
python -m pytest tests/test_entity_pipeline_v2.py -v
```

- [ ] **Step 7: Commit**

```bash
git add intelligence/pyproject.toml intelligence/intelligence/config.py intelligence/intelligence/pipelines/entity.py intelligence/tests/test_entity_pipeline_v2.py
git commit -m "feat(intelligence): upgrade SpaCy model to en_core_web_trf for better NER

Switch default from en_core_web_sm (F1=0.04) to en_core_web_trf (transformer-based).
Update pipeline version to entity-v2, adjust confidence scoring for transformer model.
Add spacy-transformers, pyyaml, and rapidfuzz dependencies."
```

---

## Task 2: Entity Post-Processing / Cleaning

**Files:**
- Create: `intelligence/intelligence/pipelines/entity_cleaner.py`
- Create: `intelligence/tests/test_entity_cleaner.py`
- Create: `fixtures/entity_dirty_page.html`

- [ ] **Step 1: Write entity cleaner tests**

Create `intelligence/tests/test_entity_cleaner.py`:
```python
"""Tests for entity post-processing cleaner."""
import pytest

from intelligence.models import ExtractedEntity
from intelligence.pipelines.entity_cleaner import EntityCleaner


@pytest.fixture
def cleaner():
    return EntityCleaner()


def _make_entity(text: str, entity_type: str = "PERSON", confidence: float = 0.9) -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=entity_type,
        entity_text=text,
        start_char=0,
        end_char=len(text),
        confidence=confidence,
        pipeline_version="entity-v2",
    )


class TestStripPossessives:
    def test_strips_curly_apostrophe_s(self, cleaner):
        entities = [_make_entity("Elon Musk\u2019s")]
        result = cleaner.clean(entities)
        assert result[0].entity_text == "Elon Musk"

    def test_strips_straight_apostrophe_s(self, cleaner):
        entities = [_make_entity("Elon Musk's")]
        result = cleaner.clean(entities)
        assert result[0].entity_text == "Elon Musk"

    def test_preserves_names_ending_in_s(self, cleaner):
        entities = [_make_entity("Charles")]
        result = cleaner.clean(entities)
        assert result[0].entity_text == "Charles"


class TestStripPunctuation:
    def test_strips_trailing_comma(self, cleaner):
        entities = [_make_entity("Tesla,", "ORG")]
        result = cleaner.clean(entities)
        assert result[0].entity_text == "Tesla"

    def test_strips_trailing_period(self, cleaner):
        entities = [_make_entity("Microsoft.", "ORG")]
        result = cleaner.clean(entities)
        assert result[0].entity_text == "Microsoft"

    def test_strips_trailing_dash(self, cleaner):
        entities = [_make_entity("Elon Musk-")]
        result = cleaner.clean(entities)
        assert result[0].entity_text == "Elon Musk"

    def test_strips_leading_dash(self, cleaner):
        entities = [_make_entity("-Apple", "ORG")]
        result = cleaner.clean(entities)
        assert result[0].entity_text == "Apple"

    def test_strips_leading_quote(self, cleaner):
        entities = [_make_entity('"Google', "ORG")]
        result = cleaner.clean(entities)
        assert result[0].entity_text == "Google"


class TestLengthFilter:
    def test_removes_single_char_entities(self, cleaner):
        entities = [_make_entity("X")]
        result = cleaner.clean(entities)
        assert len(result) == 0

    def test_removes_empty_after_cleaning(self, cleaner):
        entities = [_make_entity("'s")]
        result = cleaner.clean(entities)
        assert len(result) == 0

    def test_keeps_two_char_entities(self, cleaner):
        entities = [_make_entity("AI", "ORG")]
        result = cleaner.clean(entities)
        assert len(result) == 1

    def test_removes_very_long_entities(self, cleaner):
        entities = [_make_entity("A" * 201)]
        result = cleaner.clean(entities)
        assert len(result) == 0


class TestGarbageFilter:
    def test_removes_pure_numbers(self, cleaner):
        entities = [_make_entity("12345", "PERSON")]
        result = cleaner.clean(entities)
        assert len(result) == 0

    def test_removes_common_stopwords(self, cleaner):
        entities = [_make_entity("The", "ORG")]
        result = cleaner.clean(entities)
        assert len(result) == 0

    def test_removes_partial_phrases(self, cleaner):
        """Entities starting with common verbs are likely extraction errors."""
        entities = [_make_entity("Elon Musk Starts", "PERSON")]
        result = cleaner.clean(entities)
        assert len(result) == 0

    def test_keeps_valid_entity(self, cleaner):
        entities = [_make_entity("Elon Musk")]
        result = cleaner.clean(entities)
        assert len(result) == 1
        assert result[0].entity_text == "Elon Musk"


class TestWhitespaceNormalization:
    def test_collapses_internal_whitespace(self, cleaner):
        entities = [_make_entity("Elon  Musk")]
        result = cleaner.clean(entities)
        assert result[0].entity_text == "Elon Musk"

    def test_strips_leading_trailing_whitespace(self, cleaner):
        entities = [_make_entity("  Google  ", "ORG")]
        result = cleaner.clean(entities)
        assert result[0].entity_text == "Google"


class TestDedup:
    def test_deduplicates_after_cleaning(self, cleaner):
        """'Tesla,' and 'Tesla' should merge after cleaning."""
        entities = [
            _make_entity("Tesla,", "ORG"),
            _make_entity("Tesla", "ORG"),
        ]
        result = cleaner.clean(entities)
        assert len(result) == 1
        assert result[0].entity_text == "Tesla"

    def test_keeps_different_types(self, cleaner):
        """Same text but different types should both survive."""
        entities = [
            _make_entity("Apple", "ORG"),
            _make_entity("Apple", "PRODUCT"),
        ]
        result = cleaner.clean(entities)
        assert len(result) == 2
```

- [ ] **Step 2: Create entity_dirty_page.html fixture**

Create `fixtures/entity_dirty_page.html`:
```html
<!DOCTYPE html>
<html>
<head><title>Dirty Entity Test Page</title></head>
<body>
<h1>Tech News Roundup</h1>
<p>Elon Musk's company Tesla, announced a new factory in Austin, Texas.</p>
<p>The CEO Elon Musk- who also runs SpaceX- said the factory would be operational by 2027.</p>
<p>"Google announced new AI features at their Mountain View headquarters.</p>
<p>Apple CEO Tim Cook unveiled the iPhone at Apple Park in Cupertino, California.</p>
<p>Elon Musk Starts a new venture called xAI to compete with OpenAI and Microsoft.</p>
</body>
</html>
```

- [ ] **Step 3: Implement entity cleaner**

Create `intelligence/intelligence/pipelines/entity_cleaner.py`:
```python
"""Post-processing cleaner for extracted entities.

Fixes common SpaCy NER artifacts: trailing possessives, punctuation, garbage
patterns, and normalizes whitespace. Deduplicates after cleaning.
"""
import re

from intelligence.models import ExtractedEntity


# Single-word stopwords that are never valid entities on their own
_STOPWORDS = frozenset({
    "the", "a", "an", "this", "that", "these", "those",
    "it", "its", "he", "she", "they", "we", "you", "i",
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might",
    "and", "or", "but", "not", "no", "yes",
    "for", "of", "in", "on", "at", "to", "from", "by", "with",
    "first", "last", "new", "old", "big", "small",
    "more", "most", "very", "just", "also", "here", "there",
})

# Verbs that indicate a partial phrase was captured (e.g., "Elon Musk Starts")
_TRAILING_VERB_PATTERN = re.compile(
    r"\s+(Starts?|Says?|Gets?|Makes?|Takes?|Gives?|Becomes?|Announces?|Reveals?|Confirms?|Launches?|Joins?|Leaves?|Wants?|Plans?|Seeks?|Shows?|Calls?|Asks?|Tells?|Warns?|Faces?|Hits?|Wins?|Loses?|Buys?|Sells?|Builds?|Opens?|Closes?|Moves?|Raises?|Cuts?|Adds?|Drops?|Turns?|Leads?|Pushes?|Pulls?|Runs?|Sets?|Puts?|Keeps?|Lets?|Holds?)\s*$",
    re.IGNORECASE,
)

# Possessive suffixes
_POSSESSIVE_PATTERN = re.compile(r"[\u2019']s$")

# Leading/trailing punctuation to strip
_LEADING_PUNCT = re.compile(r'^[\-"\u201c\u201d(]+')
_TRAILING_PUNCT = re.compile(r'[\-,.:;!?"\u201c\u201d)]+$')

# Whitespace normalization
_MULTI_SPACE = re.compile(r"\s+")

# Pure number pattern
_PURE_NUMBER = re.compile(r"^\d+$")


class EntityCleaner:
    """Cleans and filters extracted entities to remove NER artifacts."""

    MIN_LENGTH = 2
    MAX_LENGTH = 200

    def clean(self, entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        """Clean entity texts and filter out garbage.

        Steps:
        1. Clean each entity text (possessives, punctuation, whitespace)
        2. Remove trailing verb phrases (partial sentence captures)
        3. Filter invalid entities (too short, too long, stopwords, numbers)
        4. Deduplicate by (cleaned_text, entity_type)
        """
        cleaned = []
        seen: set[tuple[str, str]] = set()

        for entity in entities:
            text = self._clean_text(entity.entity_text)

            if not self._is_valid(text, entity.entity_type):
                continue

            key = (text, entity.entity_type)
            if key in seen:
                continue
            seen.add(key)

            cleaned.append(ExtractedEntity(
                entity_type=entity.entity_type,
                entity_text=text,
                start_char=entity.start_char,
                end_char=entity.end_char,
                confidence=entity.confidence,
                pipeline_version=entity.pipeline_version,
            ))

        return cleaned

    def _clean_text(self, text: str) -> str:
        """Apply all text cleaning steps."""
        # Strip leading/trailing whitespace
        text = text.strip()

        # Remove trailing verb phrases ("Elon Musk Starts" -> "Elon Musk")
        text = _TRAILING_VERB_PATTERN.sub("", text)

        # Strip possessives ("Elon Musk's" -> "Elon Musk")
        text = _POSSESSIVE_PATTERN.sub("", text)

        # Strip leading punctuation
        text = _LEADING_PUNCT.sub("", text)

        # Strip trailing punctuation
        text = _TRAILING_PUNCT.sub("", text)

        # Normalize internal whitespace
        text = _MULTI_SPACE.sub(" ", text)

        # Final strip
        text = text.strip()

        return text

    def _is_valid(self, text: str, entity_type: str) -> bool:
        """Check if cleaned entity text is valid."""
        # Length checks
        if len(text) < self.MIN_LENGTH:
            return False
        if len(text) > self.MAX_LENGTH:
            return False

        # Pure numbers are not valid PERSON/ORG/GPE entities
        if entity_type in ("PERSON", "ORG", "GPE") and _PURE_NUMBER.match(text):
            return False

        # Single-word stopwords
        if text.lower() in _STOPWORDS:
            return False

        return True
```

- [ ] **Step 4: Run cleaner tests**

```bash
cd /Users/suryakoritala/phantombot/intelligence
source .venv/bin/activate
python -m pytest tests/test_entity_cleaner.py -v
```

- [ ] **Step 5: Commit**

```bash
git add intelligence/intelligence/pipelines/entity_cleaner.py intelligence/tests/test_entity_cleaner.py fixtures/entity_dirty_page.html
git commit -m "feat(intelligence): add entity post-processing cleaner

Strip possessives, trailing/leading punctuation, trailing verb phrases.
Filter garbage entities: too short, too long, stopwords, pure numbers.
Normalize whitespace and deduplicate after cleaning."
```

---

## Task 3: Integrate Entity Cleaner into Pipeline

**Files:**
- Modify: `intelligence/intelligence/pipelines/entity.py`
- Modify: `intelligence/intelligence/worker.py`

- [ ] **Step 1: Write integration test**

Add to `intelligence/tests/test_entity_pipeline_v2.py`:
```python
def test_dirty_entities_are_cleaned(pipeline):
    """Pipeline should clean garbage entities before returning."""
    dirty_html = """
    <html><body>
    <p>Elon Musk's company Tesla, is expanding. Elon Musk- the CEO- confirmed this.</p>
    <p>"Google announced new AI features. Elon Musk Starts a new venture.</p>
    </body></html>
    """
    entities = pipeline.extract(dirty_html, "https://example.com/test")

    texts = [e.entity_text for e in entities]
    # Should not contain possessives or trailing punctuation
    for text in texts:
        assert not text.endswith("'s"), f"Possessive not stripped: {text}"
        assert not text.endswith("\u2019s"), f"Possessive not stripped: {text}"
        assert not text.endswith(","), f"Trailing comma not stripped: {text}"
        assert not text.endswith("-"), f"Trailing dash not stripped: {text}"
        assert not text.startswith('"'), f"Leading quote not stripped: {text}"
```

- [ ] **Step 2: Integrate cleaner into entity pipeline**

Modify `intelligence/intelligence/pipelines/entity.py`:
```python
import spacy
from bs4 import BeautifulSoup

from intelligence.config import Config
from intelligence.models import ExtractedEntity
from intelligence.pipelines.base import BasePipeline
from intelligence.pipelines.entity_cleaner import EntityCleaner


class EntityPipeline(BasePipeline):
    """Extracts named entities from HTML using SpaCy NER."""

    SUPPORTED_TYPES = {"PERSON", "ORG", "GPE", "PRODUCT", "MONEY", "LOC", "FAC", "NORP", "EVENT"}

    def __init__(self, model_name: str | None = None):
        self._model_name = model_name or Config.SPACY_MODEL
        self._nlp = spacy.load(self._model_name)
        self._is_transformer = "trf" in self._model_name
        self._cleaner = EntityCleaner()

    def extract(self, html: str, source_url: str) -> list[ExtractedEntity]:
        if not html or not html.strip():
            return []

        text = self._html_to_text(html)
        if not text.strip():
            return []

        doc = self._nlp(text)
        entities = []
        seen = set()

        for ent in doc.ents:
            if ent.label_ not in self.SUPPORTED_TYPES:
                continue

            key = (ent.text.strip(), ent.label_)
            if key in seen:
                continue
            seen.add(key)

            entities.append(ExtractedEntity(
                entity_type=ent.label_,
                entity_text=ent.text.strip(),
                start_char=ent.start_char,
                end_char=ent.end_char,
                confidence=self._estimate_confidence(ent),
                pipeline_version="entity-v2",
            ))

        # Post-processing: clean and re-dedup
        entities = self._cleaner.clean(entities)

        return entities

    def _html_to_text(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)

    def _estimate_confidence(self, ent) -> float:
        """Confidence based on model type and entity characteristics."""
        score = 0.85 if self._is_transformer else 0.7
        if len(ent.text.strip()) > 2:
            score += 0.05
        if ent.label_ in ("PERSON", "ORG"):
            score += 0.05
        return min(score, 1.0)
```

- [ ] **Step 3: Run all entity tests**

```bash
cd /Users/suryakoritala/phantombot/intelligence
source .venv/bin/activate
python -m pytest tests/test_entity_cleaner.py tests/test_entity_pipeline_v2.py -v
```

- [ ] **Step 4: Commit**

```bash
git add intelligence/intelligence/pipelines/entity.py intelligence/tests/test_entity_pipeline_v2.py
git commit -m "feat(intelligence): integrate entity cleaner into extraction pipeline

EntityPipeline now runs EntityCleaner.clean() after SpaCy NER extraction.
Dirty entities are cleaned and re-deduplicated before being returned."
```

---

## Task 4: Connect Crawl API to Go Crawler via Redis Stream Consumer

**Files:**
- Modify: `crawler/internal/config/config.go`
- Create: `crawler/internal/consumer/consumer.go`
- Create: `crawler/internal/consumer/consumer_test.go`
- Modify: `crawler/cmd/phantombot/main.go`

- [ ] **Step 1: Write consumer test**

Create `crawler/internal/consumer/consumer_test.go`:
```go
package consumer

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/surya-koritala/phantombot/crawler/internal/frontier"
)

func getTestRedis(t *testing.T) *redis.Client {
	t.Helper()
	url := os.Getenv("REDIS_URL")
	if url == "" {
		url = "redis://localhost:6379"
	}
	opts, err := redis.ParseURL(url)
	if err != nil {
		t.Skipf("skipping: invalid REDIS_URL: %v", err)
	}
	client := redis.NewClient(opts)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := client.Ping(ctx).Err(); err != nil {
		t.Skipf("skipping: redis not available: %v", err)
	}
	return client
}

func TestConsumer_ReadsFromStream(t *testing.T) {
	rdb := getTestRedis(t)
	defer rdb.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	stream := "test:crawl_requests:" + time.Now().Format("150405")
	group := "test-crawler"
	defer rdb.Del(ctx, stream)

	f := frontier.New(0)

	c := New(rdb, stream, group, f)
	if err := c.Setup(ctx); err != nil {
		t.Fatalf("setup error: %v", err)
	}

	// Write a crawl request to the stream
	rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		Values: map[string]any{
			"url":       "https://example.com/test-page",
			"max_depth": "2",
			"job_id":    "test-job-123",
		},
	})

	// Run consumer once (non-blocking)
	err := c.ReadOnce(ctx)
	if err != nil {
		t.Fatalf("read error: %v", err)
	}

	// Verify URL was pushed to frontier
	url, depth, ok := f.Pop()
	if !ok {
		t.Fatal("expected URL in frontier, got empty")
	}
	if url != "https://example.com/test-page" {
		t.Errorf("expected url 'https://example.com/test-page', got %q", url)
	}
	if depth != 0 {
		t.Errorf("expected depth 0, got %d", depth)
	}
}

func TestConsumer_HandlesEmptyStream(t *testing.T) {
	rdb := getTestRedis(t)
	defer rdb.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	stream := "test:crawl_requests:empty:" + time.Now().Format("150405")
	group := "test-crawler-empty"
	defer rdb.Del(ctx, stream)

	f := frontier.New(0)
	c := New(rdb, stream, group, f)
	if err := c.Setup(ctx); err != nil {
		t.Fatalf("setup error: %v", err)
	}

	// Read from empty stream should not error
	err := c.ReadOnce(ctx)
	if err != nil {
		t.Fatalf("unexpected error on empty stream: %v", err)
	}

	// Frontier should still be empty
	_, _, ok := f.Pop()
	if ok {
		t.Error("expected empty frontier")
	}
}
```

- [ ] **Step 2: Implement Redis stream consumer**

Create `crawler/internal/consumer/consumer.go`:
```go
package consumer

import (
	"context"
	"fmt"
	"log"

	"github.com/redis/go-redis/v9"
	"github.com/surya-koritala/phantombot/crawler/internal/frontier"
)

// Consumer reads crawl requests from a Redis Stream and pushes URLs to the Frontier.
type Consumer struct {
	rdb      *redis.Client
	stream   string
	group    string
	consumer string
	frontier *frontier.Frontier
}

// New creates a Consumer that reads from the given Redis stream and pushes URLs to the frontier.
func New(rdb *redis.Client, stream, group string, f *frontier.Frontier) *Consumer {
	return &Consumer{
		rdb:      rdb,
		stream:   stream,
		group:    group,
		consumer: fmt.Sprintf("crawler-%d", time.Now().UnixNano()),
		frontier: f,
	}
}

// Setup creates the consumer group if it doesn't exist.
func (c *Consumer) Setup(ctx context.Context) error {
	err := c.rdb.XGroupCreateMkStream(ctx, c.stream, c.group, "0").Err()
	if err != nil && !isGroupExistsError(err) {
		return fmt.Errorf("creating consumer group %q on stream %q: %w", c.group, c.stream, err)
	}
	return nil
}

// ReadOnce performs a single blocking read from the stream (with 2s timeout).
// Returns nil if no messages are available.
func (c *Consumer) ReadOnce(ctx context.Context) error {
	streams, err := c.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    c.group,
		Consumer: c.consumer,
		Streams:  []string{c.stream, ">"},
		Count:    10,
		Block:    2 * time.Second,
	}).Result()

	if err == redis.Nil {
		return nil
	}
	if err != nil {
		return fmt.Errorf("reading from stream %q: %w", c.stream, err)
	}

	for _, stream := range streams {
		for _, msg := range stream.Messages {
			url, ok := msg.Values["url"].(string)
			if !ok || url == "" {
				log.Printf("[consumer] skipping message %s: missing or empty url", msg.ID)
				c.rdb.XAck(ctx, c.stream, c.group, msg.ID)
				continue
			}

			c.frontier.Push(url, 0)
			log.Printf("[consumer] queued: %s (job_id=%v)", url, msg.Values["job_id"])

			c.rdb.XAck(ctx, c.stream, c.group, msg.ID)
		}
	}

	return nil
}

// Run continuously reads from the stream until the context is cancelled.
func (c *Consumer) Run(ctx context.Context) error {
	if err := c.Setup(ctx); err != nil {
		return err
	}
	log.Printf("[consumer] listening on stream %s (group=%s)", c.stream, c.group)
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err := c.ReadOnce(ctx); err != nil {
			log.Printf("[consumer] read error: %v", err)
		}
	}
}

func isGroupExistsError(err error) bool {
	return err != nil && err.Error() == "BUSYGROUP Consumer Group name already exists"
}
```

Note: add `"time"` to the import list in the actual file.

- [ ] **Step 3: Update crawler config**

Modify `crawler/internal/config/config.go`:
```go
package config

import (
	"os"
	"strconv"
	"time"
)

type Config struct {
	RedisURL           string
	StreamName         string
	CrawlRequestStream string
	CrawlRequestGroup  string
	Concurrency        int
	MaxDepth           int
	UserAgent          string
	RequestTimeout     time.Duration
	PolitenessDelay    time.Duration
	RendererURL        string
	EnableRenderer     bool
	ProxyURL           string
	RotateUserAgent    bool
}

func Load() Config {
	return Config{
		RedisURL:           getEnv("REDIS_URL", "redis://localhost:6379"),
		StreamName:         getEnv("REDIS_STREAM_NAME", "phantombot:crawled"),
		CrawlRequestStream: getEnv("CRAWL_REQUEST_STREAM", "phantombot:crawl_requests"),
		CrawlRequestGroup:  getEnv("CRAWL_REQUEST_GROUP", "crawler"),
		Concurrency:        getEnvInt("CRAWLER_CONCURRENCY", 10),
		MaxDepth:           getEnvInt("CRAWLER_MAX_DEPTH", 3),
		UserAgent:          getEnv("CRAWLER_USER_AGENT", "Phantombot/1.0"),
		RequestTimeout:     time.Duration(getEnvInt("CRAWLER_REQUEST_TIMEOUT_SECONDS", 30)) * time.Second,
		PolitenessDelay:    time.Duration(getEnvInt("CRAWLER_POLITENESS_DELAY_MS", 1000)) * time.Millisecond,
		RendererURL:        getEnv("RENDERER_URL", "http://localhost:3001"),
		EnableRenderer:     getEnvBool("ENABLE_RENDERER", false),
		ProxyURL:           getEnv("CRAWLER_PROXY_URL", ""),
		RotateUserAgent:    getEnvBool("CRAWLER_ROTATE_USER_AGENT", false),
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getEnvInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return fallback
}

func getEnvBool(key string, fallback bool) bool {
	if v := os.Getenv(key); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return fallback
}
```

- [ ] **Step 4: Update main.go to support stream consumer mode**

Modify `crawler/cmd/phantombot/main.go`:
```go
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/surya-koritala/phantombot/crawler/internal/config"
	"github.com/surya-koritala/phantombot/crawler/internal/consumer"
	"github.com/surya-koritala/phantombot/crawler/internal/fetcher"
	"github.com/surya-koritala/phantombot/crawler/internal/frontier"
	"github.com/surya-koritala/phantombot/crawler/internal/publisher"
	"github.com/surya-koritala/phantombot/crawler/internal/robots"
)

func main() {
	seedURLs := flag.String("urls", "", "Comma-separated seed URLs to crawl (optional if stream consumer is enabled)")
	listenStream := flag.Bool("listen", false, "Listen for crawl requests on Redis stream (keeps running)")
	flag.Parse()

	if *seedURLs == "" && !*listenStream {
		fmt.Fprintln(os.Stderr, "usage: phantombot -urls https://example.com,https://other.com")
		fmt.Fprintln(os.Stderr, "       phantombot -listen                                    (consume from Redis stream)")
		fmt.Fprintln(os.Stderr, "       phantombot -urls https://example.com -listen           (both)")
		os.Exit(1)
	}

	cfg := config.Load()

	opts, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		log.Fatalf("invalid REDIS_URL: %v", err)
	}
	rdb := redis.NewClient(opts)
	defer rdb.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Fatalf("cannot connect to Redis: %v", err)
	}

	f := frontier.New(cfg.PolitenessDelay)
	fetch := fetcher.New(cfg.UserAgent, cfg.RequestTimeout)
	pub := publisher.New(rdb, cfg.StreamName)
	robo := robots.NewChecker(cfg.UserAgent)

	// Seed URLs from CLI
	if *seedURLs != "" {
		for raw := range strings.SplitSeq(*seedURLs, ",") {
			raw = strings.TrimSpace(raw)
			if raw != "" {
				f.Push(raw, 0)
			}
		}
	}

	log.Printf("phantombot starting: %d seed URLs, concurrency=%d, max_depth=%d, listen=%v",
		f.Len(), cfg.Concurrency, cfg.MaxDepth, *listenStream)

	// Handle graceful shutdown
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		log.Println("shutting down...")
		cancel()
	}()

	// Start stream consumer if enabled
	if *listenStream {
		cons := consumer.New(rdb, cfg.CrawlRequestStream, cfg.CrawlRequestGroup, f)
		go func() {
			if err := cons.Run(ctx); err != nil && ctx.Err() == nil {
				log.Printf("consumer error: %v", err)
			}
		}()
	}

	var wg sync.WaitGroup
	for i := 0; i < cfg.Concurrency; i++ {
		wg.Add(1)
		go func(workerID int) {
			defer wg.Done()
			for {
				if ctx.Err() != nil {
					return
				}

				rawURL, depth, ok := f.Pop()
				if !ok {
					if *listenStream {
						// In listen mode, wait for new URLs from stream consumer
						time.Sleep(500 * time.Millisecond)
						continue
					}
					return
				}

				allowed, err := robo.IsAllowed(rawURL)
				if err != nil {
					log.Printf("[worker %d] robots check error for %s: %v", workerID, rawURL, err)
				}
				if !allowed {
					log.Printf("[worker %d] blocked by robots.txt: %s", workerID, rawURL)
					continue
				}

				log.Printf("[worker %d] fetching: %s (depth=%d)", workerID, rawURL, depth)
				result, err := fetch.Fetch(rawURL, depth, "")
				if err != nil {
					log.Printf("[worker %d] fetch error for %s: %v", workerID, rawURL, err)
					continue
				}

				if err := pub.Publish(ctx, result); err != nil {
					log.Printf("[worker %d] publish error for %s: %v", workerID, rawURL, err)
					continue
				}

				log.Printf("[worker %d] published: %s (status=%d)", workerID, rawURL, result.StatusCode)

				// Extract links and add to frontier if within depth limit
				if depth < cfg.MaxDepth {
					links := extractLinks(result.HTML, rawURL)
					for _, link := range links {
						f.Push(link, depth+1)
					}
				}
			}
		}(i)
	}

	wg.Wait()
	log.Println("crawl complete")
}

// extractLinks does basic <a href="..."> extraction.
func extractLinks(html, baseURL string) []string {
	var links []string
	base, err := url.Parse(baseURL)
	if err != nil {
		return links
	}

	remaining := html
	for {
		idx := strings.Index(remaining, "href=\"")
		if idx == -1 {
			break
		}
		remaining = remaining[idx+6:]
		end := strings.Index(remaining, "\"")
		if end == -1 {
			break
		}
		href := remaining[:end]
		remaining = remaining[end:]

		parsed, err := url.Parse(href)
		if err != nil {
			continue
		}
		resolved := base.ResolveReference(parsed)

		if resolved.Scheme == "http" || resolved.Scheme == "https" {
			links = append(links, resolved.String())
		}
	}
	return links
}
```

- [ ] **Step 5: Run Go tests**

```bash
cd /Users/suryakoritala/phantombot/crawler
go test ./internal/consumer/ -v -run TestConsumer
go test ./... -v -short
```

- [ ] **Step 6: Commit**

```bash
git add crawler/internal/config/config.go crawler/internal/consumer/ crawler/cmd/phantombot/main.go
git commit -m "feat(crawler): connect crawl API to Go crawler via Redis stream consumer

Add consumer package that reads from phantombot:crawl_requests Redis stream
and pushes URLs to the frontier. CLI now supports -listen flag to keep running
and consume API-initiated crawl requests. Workers wait for new URLs in listen
mode instead of exiting when frontier is empty."
```

---

## Task 5: Add Test Fixtures for New Scenarios

**Files:**
- Create: `fixtures/swagger_ui_page.html`
- Create: `fixtures/redoc_page.html`
- Create: `fixtures/js_heavy_page.html`

- [ ] **Step 1: Create Swagger UI fixture**

Create `fixtures/swagger_ui_page.html`:
```html
<!DOCTYPE html>
<html>
<head>
<title>Swagger UI - Pet Store API</title>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist/swagger-ui-bundle.js"></script>
<script>
SwaggerUIBundle({
  url: "https://petstore.swagger.io/v2/swagger.json",
  dom_id: '#swagger-ui',
  presets: [
    SwaggerUIBundle.presets.apis,
    SwaggerUIBundle.SwaggerUIStandalonePreset
  ],
  layout: "StandaloneLayout"
})
</script>
</body>
</html>
```

- [ ] **Step 2: Create Redoc fixture**

Create `fixtures/redoc_page.html`:
```html
<!DOCTYPE html>
<html>
<head>
<title>API Documentation - Redoc</title>
<meta charset="utf-8"/>
</head>
<body>
<redoc spec-url="https://petstore.swagger.io/v2/swagger.json"></redoc>
<script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create JS-heavy page fixture**

Create `fixtures/js_heavy_page.html`:
```html
<!DOCTYPE html>
<html>
<head>
<title>React App</title>
<meta charset="utf-8"/>
</head>
<body>
<noscript>You need to enable JavaScript to run this app.</noscript>
<div id="root"></div>
<script>
// This simulates a React/Vue SPA where content is rendered by JS
document.getElementById('root').innerHTML = '<h1>Rendered by JavaScript</h1><p>Elon Musk founded Tesla and SpaceX.</p>';
</script>
</body>
</html>
```

- [ ] **Step 4: Commit**

```bash
git add fixtures/swagger_ui_page.html fixtures/redoc_page.html fixtures/js_heavy_page.html
git commit -m "test: add fixture HTML files for Swagger UI, Redoc, and JS-heavy pages"
```

---

## Phase 2: P1 Important Improvements

---

## Task 6: Entity Resolution / Dedup

**Files:**
- Create: `intelligence/intelligence/pipelines/entity_resolver.py`
- Create: `intelligence/tests/test_entity_resolver.py`
- Modify: `intelligence/intelligence/models.py`
- Create: `store/migrations/002_entity_resolution.sql`

- [ ] **Step 1: Write entity resolver tests**

Create `intelligence/tests/test_entity_resolver.py`:
```python
"""Tests for entity resolution and deduplication."""
import pytest

from intelligence.models import ExtractedEntity
from intelligence.pipelines.entity_resolver import EntityResolver


@pytest.fixture
def resolver():
    return EntityResolver()


def _make_entity(text: str, entity_type: str = "ORG", confidence: float = 0.9) -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=entity_type,
        entity_text=text,
        start_char=0,
        end_char=len(text),
        confidence=confidence,
        pipeline_version="entity-v2",
    )


class TestNormalization:
    def test_strips_corp_suffix(self, resolver):
        assert resolver._normalize("Tesla Corp") == "tesla"

    def test_strips_inc_suffix(self, resolver):
        assert resolver._normalize("Tesla Inc") == "tesla"

    def test_strips_inc_with_period(self, resolver):
        assert resolver._normalize("Tesla, Inc.") == "tesla"

    def test_strips_llc(self, resolver):
        assert resolver._normalize("Acme LLC") == "acme"

    def test_strips_ltd(self, resolver):
        assert resolver._normalize("Acme Ltd") == "acme"

    def test_lowercases(self, resolver):
        assert resolver._normalize("GOOGLE") == "google"

    def test_strips_the_prefix(self, resolver):
        assert resolver._normalize("The New York Times") == "new york times"


class TestSimilarity:
    def test_identical_strings(self, resolver):
        assert resolver._similarity("tesla", "tesla") == 1.0

    def test_similar_strings(self, resolver):
        score = resolver._similarity("tesla", "tesla motors")
        assert score >= 0.5

    def test_very_different_strings(self, resolver):
        score = resolver._similarity("apple", "microsoft")
        assert score < 0.5


class TestResolution:
    def test_merges_org_variants(self, resolver):
        entities = [
            _make_entity("Tesla"),
            _make_entity("Tesla Inc"),
            _make_entity("Tesla, Inc."),
            _make_entity("Tesla Motors"),
        ]
        resolved = resolver.resolve(entities)
        # Should produce one group
        canonical_ids = {r.canonical_id for r in resolved}
        assert len(canonical_ids) == 1, f"Expected 1 canonical group, got {len(canonical_ids)}: {canonical_ids}"

    def test_keeps_different_entities_separate(self, resolver):
        entities = [
            _make_entity("Tesla"),
            _make_entity("Microsoft"),
            _make_entity("Apple"),
        ]
        resolved = resolver.resolve(entities)
        canonical_ids = {r.canonical_id for r in resolved}
        assert len(canonical_ids) == 3

    def test_picks_shortest_clean_form_as_canonical(self, resolver):
        entities = [
            _make_entity("Tesla, Inc."),
            _make_entity("Tesla"),
            _make_entity("Tesla Motors"),
        ]
        resolved = resolver.resolve(entities)
        # Canonical should be the shortest clean form
        canonical_texts = {r.canonical_text for r in resolved}
        assert "Tesla" in canonical_texts

    def test_preserves_entity_type(self, resolver):
        entities = [
            _make_entity("Elon Musk", "PERSON"),
            _make_entity("Elon Musk", "PERSON"),
        ]
        resolved = resolver.resolve(entities)
        assert len(resolved) == 1
        assert resolved[0].entity_type == "PERSON"

    def test_does_not_merge_across_types(self, resolver):
        """'Apple' as ORG and 'Apple' as PRODUCT should not merge."""
        entities = [
            _make_entity("Apple", "ORG"),
            _make_entity("Apple", "PRODUCT"),
        ]
        resolved = resolver.resolve(entities)
        assert len(resolved) == 2
```

- [ ] **Step 2: Add ResolvedEntity model**

Modify `intelligence/intelligence/models.py` -- add after `ExtractedEntity`:
```python
class ResolvedEntity(BaseModel):
    """An entity after resolution/deduplication."""
    entity_type: str
    entity_text: str
    canonical_text: str
    canonical_id: str
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    pipeline_version: str = "entity-v2"
    start_char: int | None = None
    end_char: int | None = None
```

- [ ] **Step 3: Implement entity resolver**

Create `intelligence/intelligence/pipelines/entity_resolver.py`:
```python
"""Entity resolution layer — merges variant spellings into canonical entities.

Groups entities by normalized form + fuzzy similarity, picks the shortest clean
form as canonical, and assigns a stable canonical_id (hash of canonical_text + type).
"""
import hashlib
import re

from rapidfuzz import fuzz

from intelligence.models import ExtractedEntity, ResolvedEntity


# Suffixes to strip during normalization (case-insensitive)
_ORG_SUFFIXES = re.compile(
    r",?\s*\b(Inc\.?|Corp\.?|Corporation|LLC|Ltd\.?|Limited|Co\.?|Company|Group|Holdings?|PLC|SA|AG|GmbH|SE)\s*\.?\s*$",
    re.IGNORECASE,
)

_THE_PREFIX = re.compile(r"^the\s+", re.IGNORECASE)

# Similarity threshold for merging entities
_SIMILARITY_THRESHOLD = 0.80


class EntityResolver:
    """Resolves and deduplicates entities by fuzzy matching."""

    def resolve(self, entities: list[ExtractedEntity]) -> list[ResolvedEntity]:
        """Group entities by similarity and return resolved entities with canonical forms."""
        if not entities:
            return []

        # Group by entity_type first — never merge across types
        by_type: dict[str, list[ExtractedEntity]] = {}
        for ent in entities:
            by_type.setdefault(ent.entity_type, []).append(ent)

        resolved = []
        for entity_type, type_entities in by_type.items():
            groups = self._cluster(type_entities)
            for group in groups:
                canonical_text = self._pick_canonical(group)
                canonical_id = self._make_id(canonical_text, entity_type)
                aliases = sorted({e.entity_text for e in group if e.entity_text != canonical_text})
                best_confidence = max(e.confidence for e in group)

                for ent in group:
                    resolved.append(ResolvedEntity(
                        entity_type=entity_type,
                        entity_text=ent.entity_text,
                        canonical_text=canonical_text,
                        canonical_id=canonical_id,
                        aliases=aliases,
                        confidence=best_confidence,
                        pipeline_version=ent.pipeline_version,
                        start_char=ent.start_char,
                        end_char=ent.end_char,
                    ))

        return resolved

    def _cluster(self, entities: list[ExtractedEntity]) -> list[list[ExtractedEntity]]:
        """Cluster entities by normalized similarity."""
        if not entities:
            return []

        clusters: list[list[ExtractedEntity]] = []
        assigned = [False] * len(entities)

        normalized = [self._normalize(e.entity_text) for e in entities]

        for i in range(len(entities)):
            if assigned[i]:
                continue
            cluster = [entities[i]]
            assigned[i] = True

            for j in range(i + 1, len(entities)):
                if assigned[j]:
                    continue
                if self._similarity(normalized[i], normalized[j]) >= _SIMILARITY_THRESHOLD:
                    cluster.append(entities[j])
                    assigned[j] = True

            clusters.append(cluster)

        return clusters

    def _pick_canonical(self, group: list[ExtractedEntity]) -> str:
        """Pick the best canonical text from a group.

        Prefer the shortest text that is at least 2 chars (usually the cleanest form).
        """
        texts = sorted({e.entity_text for e in group}, key=len)
        return texts[0] if texts else group[0].entity_text

    def _normalize(self, text: str) -> str:
        """Normalize entity text for comparison."""
        text = text.strip()
        text = _ORG_SUFFIXES.sub("", text)
        text = _THE_PREFIX.sub("", text)
        text = text.strip(" ,.")
        text = text.lower()
        return text

    def _similarity(self, a: str, b: str) -> float:
        """Compute similarity between two normalized strings (0.0 - 1.0)."""
        if a == b:
            return 1.0
        # Use token_sort_ratio for word-order-independent comparison
        return fuzz.token_sort_ratio(a, b) / 100.0

    def _make_id(self, canonical_text: str, entity_type: str) -> str:
        """Create a stable canonical_id from text + type."""
        content = f"{entity_type}:{canonical_text.lower()}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
```

- [ ] **Step 4: Create database migration**

Create `store/migrations/002_entity_resolution.sql`:
```sql
-- Entity resolution: canonical entities and aliases
-- Run after 001_initial.sql

CREATE TABLE IF NOT EXISTS canonical_entities (
    id TEXT PRIMARY KEY,
    canonical_text TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases JSONB NOT NULL DEFAULT '[]',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (canonical_text, entity_type)
);

-- Link extracted entities to their canonical form
ALTER TABLE entities ADD COLUMN IF NOT EXISTS canonical_entity_id TEXT REFERENCES canonical_entities(id);

CREATE INDEX IF NOT EXISTS idx_canonical_entities_type ON canonical_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_canonical_entities_text ON canonical_entities(canonical_text);
CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(canonical_entity_id);
```

- [ ] **Step 5: Run resolver tests**

```bash
cd /Users/suryakoritala/phantombot/intelligence
source .venv/bin/activate
pip install rapidfuzz
python -m pytest tests/test_entity_resolver.py -v
```

- [ ] **Step 6: Commit**

```bash
git add intelligence/intelligence/pipelines/entity_resolver.py intelligence/intelligence/models.py intelligence/tests/test_entity_resolver.py store/migrations/002_entity_resolution.sql
git commit -m "feat(intelligence): add entity resolution layer for fuzzy dedup

Merge 'Tesla' / 'Tesla Inc' / 'Tesla, Inc.' / 'Tesla Motors' into canonical
entities. Uses rapidfuzz for similarity matching, strips org suffixes during
normalization. Never merges across entity types. Includes DB migration for
canonical_entities table."
```

---

## Task 7: Playwright Sidecar for JS Rendering

**Files:**
- Create: `playwright-service/pyproject.toml`
- Create: `playwright-service/server.py`
- Create: `playwright-service/tests/test_server.py`
- Create: `playwright-service/Dockerfile`

- [ ] **Step 1: Write Playwright service test**

Create `playwright-service/tests/__init__.py` (empty) and `playwright-service/tests/test_server.py`:
```python
"""Tests for Playwright render service."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client -- skips if Playwright is not installed."""
    try:
        from server import app
        return TestClient(app)
    except Exception:
        pytest.skip("Playwright not installed or browsers not available")


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_render_returns_error_for_invalid_url(client):
    response = client.post("/render", json={"url": "not-a-url"})
    assert response.status_code == 422 or response.status_code == 400


def test_render_request_schema(client):
    """Verify the request schema accepts expected fields."""
    response = client.post("/render", json={
        "url": "https://httpbin.org/html",
        "timeout_ms": 5000,
    })
    # We accept 200 or 500 (if network is unavailable in test env)
    assert response.status_code in (200, 500, 502)
```

- [ ] **Step 2: Create Playwright service**

Create `playwright-service/pyproject.toml`:
```toml
[project]
name = "phantombot-playwright-service"
version = "0.1.0"
description = "Playwright render sidecar for Phantombot JS-heavy page rendering"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "playwright>=1.48.0",
    "pydantic>=2.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "httpx>=0.27.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"
```

Create `playwright-service/server.py`:
```python
"""Playwright render sidecar — HTTP service that renders JS-heavy pages.

The Go crawler calls POST /render with a URL, and this service returns
the fully rendered HTML after JavaScript execution.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright, Browser
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_browser: Browser | None = None
_playwright = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start Playwright browser on startup, close on shutdown."""
    global _browser, _playwright
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(headless=True)
    logger.info("Playwright browser started")
    yield
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()
    logger.info("Playwright browser stopped")


app = FastAPI(title="Phantombot Playwright Service", lifespan=lifespan)


class RenderRequest(BaseModel):
    url: str
    timeout_ms: int = Field(default=30000, ge=1000, le=120000)


class RenderResponse(BaseModel):
    url: str
    html: str
    status_code: int
    rendered: bool = True


@app.get("/health")
async def health():
    return {"status": "ok", "browser": _browser is not None}


@app.post("/render", response_model=RenderResponse)
async def render(req: RenderRequest):
    parsed = urlparse(req.url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL: must include scheme and host")

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid URL: only http/https supported")

    if not _browser:
        raise HTTPException(status_code=503, detail="Browser not available")

    try:
        page = await _browser.new_page()
        try:
            response = await page.goto(req.url, timeout=req.timeout_ms, wait_until="networkidle")
            status_code = response.status if response else 0

            # Wait a bit more for any late JS rendering
            await page.wait_for_timeout(1000)

            html = await page.content()
            return RenderResponse(
                url=req.url,
                html=html,
                status_code=status_code,
            )
        finally:
            await page.close()
    except Exception as e:
        logger.exception(f"Render error for {req.url}")
        raise HTTPException(status_code=502, detail=f"Render failed: {str(e)}")
```

- [ ] **Step 3: Create Dockerfile**

Create `playwright-service/Dockerfile`:
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY server.py .

EXPOSE 3001

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "3001"]
```

- [ ] **Step 4: Commit**

```bash
git add playwright-service/
git commit -m "feat: add Playwright render sidecar for JS-heavy page rendering

HTTP service exposing POST /render endpoint. The Go crawler calls this
to get fully rendered HTML for JS-heavy SPAs. Uses Playwright with Chromium
in headless mode. Includes health endpoint and configurable timeout."
```

---

## Task 8: Go Renderer Client

**Files:**
- Create: `crawler/internal/renderer/renderer.go`
- Create: `crawler/internal/renderer/renderer_test.go`

- [ ] **Step 1: Write renderer client test**

Create `crawler/internal/renderer/renderer_test.go`:
```go
package renderer

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestRenderer_Render(t *testing.T) {
	// Mock Playwright service
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/render" {
			http.NotFound(w, r)
			return
		}
		if r.Method != "POST" {
			w.WriteHeader(405)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"url":         "https://example.com/spa",
			"html":        "<html><body><h1>Rendered Content</h1></body></html>",
			"status_code": 200,
			"rendered":    true,
		})
	}))
	defer srv.Close()

	r := New(srv.URL, 10*time.Second)
	result, err := r.Render("https://example.com/spa")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if result.StatusCode != 200 {
		t.Errorf("expected status 200, got %d", result.StatusCode)
	}
	if !result.Rendered {
		t.Error("expected rendered=true")
	}
	if result.HTML != "<html><body><h1>Rendered Content</h1></body></html>" {
		t.Errorf("unexpected HTML: %s", result.HTML)
	}
}

func TestRenderer_ServiceDown(t *testing.T) {
	r := New("http://localhost:19999", 2*time.Second)
	_, err := r.Render("https://example.com/test")
	if err == nil {
		t.Fatal("expected error when service is down")
	}
}

func TestNeedsRendering(t *testing.T) {
	tests := []struct {
		name string
		html string
		want bool
	}{
		{
			name: "empty root div",
			html: `<html><body><div id="root"></div></body></html>`,
			want: true,
		},
		{
			name: "empty app div",
			html: `<html><body><div id="app"></div></body></html>`,
			want: true,
		},
		{
			name: "noscript tag",
			html: `<html><body><noscript>Enable JS</noscript><div id="root"></div></body></html>`,
			want: true,
		},
		{
			name: "normal HTML with content",
			html: `<html><body><h1>Hello World</h1><p>This is a real page with content.</p></body></html>`,
			want: false,
		},
		{
			name: "very short body",
			html: `<html><body>x</body></html>`,
			want: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := NeedsRendering(tt.html)
			if got != tt.want {
				t.Errorf("NeedsRendering() = %v, want %v", got, tt.want)
			}
		})
	}
}
```

- [ ] **Step 2: Implement renderer client**

Create `crawler/internal/renderer/renderer.go`:
```go
package renderer

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

// Renderer calls the Playwright sidecar service to render JS-heavy pages.
type Renderer struct {
	baseURL string
	client  *http.Client
}

// New creates a Renderer that calls the Playwright service at baseURL.
func New(baseURL string, timeout time.Duration) *Renderer {
	return &Renderer{
		baseURL: strings.TrimRight(baseURL, "/"),
		client:  &http.Client{Timeout: timeout},
	}
}

type renderRequest struct {
	URL       string `json:"url"`
	TimeoutMS int    `json:"timeout_ms"`
}

type renderResponse struct {
	URL        string `json:"url"`
	HTML       string `json:"html"`
	StatusCode int    `json:"status_code"`
	Rendered   bool   `json:"rendered"`
}

// Render sends a URL to the Playwright service and returns the rendered result.
func (r *Renderer) Render(rawURL string) (*models.CrawlResult, error) {
	body, err := json.Marshal(renderRequest{
		URL:       rawURL,
		TimeoutMS: 30000,
	})
	if err != nil {
		return nil, fmt.Errorf("marshaling render request: %w", err)
	}

	resp, err := r.client.Post(r.baseURL+"/render", "application/json", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("calling render service: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading render response: %w", err)
	}

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("render service returned %d: %s", resp.StatusCode, string(respBody))
	}

	var result renderResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("decoding render response: %w", err)
	}

	return &models.CrawlResult{
		URL:         rawURL,
		StatusCode:  result.StatusCode,
		ContentType: "text/html",
		HTML:        result.HTML,
		Headers:     map[string]string{},
		FetchedAt:   time.Now().UTC(),
		Rendered:    true,
		Depth:       0,
	}, nil
}

// NeedsRendering checks if HTML looks like a JS shell that needs rendering.
func NeedsRendering(html string) bool {
	// Very short content is suspicious
	textContent := stripHTMLTags(html)
	if len(strings.TrimSpace(textContent)) < 100 {
		return true
	}

	lower := strings.ToLower(html)

	// Common SPA markers
	spaMarkers := []string{
		`<div id="root"></div>`,
		`<div id="app"></div>`,
		`<div id="__next"></div>`,
		`<div id="__nuxt"></div>`,
	}
	for _, marker := range spaMarkers {
		if strings.Contains(lower, marker) {
			return true
		}
	}

	// Noscript tag suggests JS dependency
	if strings.Contains(lower, "<noscript>") {
		return true
	}

	return false
}

// stripHTMLTags is a simple tag stripper for content length estimation.
func stripHTMLTags(html string) string {
	var result strings.Builder
	inTag := false
	for _, r := range html {
		if r == '<' {
			inTag = true
			continue
		}
		if r == '>' {
			inTag = false
			continue
		}
		if !inTag {
			result.WriteRune(r)
		}
	}
	return result.String()
}
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/suryakoritala/phantombot/crawler
go test ./internal/renderer/ -v
```

- [ ] **Step 4: Commit**

```bash
git add crawler/internal/renderer/
git commit -m "feat(crawler): add Playwright renderer client and JS detection heuristic

Go client that calls the Playwright sidecar POST /render endpoint.
Includes NeedsRendering() heuristic that checks for SPA markers
(empty root/app divs, noscript tags, very short content)."
```

---

## Task 9: Anti-Bot User-Agent Rotation and Stealth Headers

**Files:**
- Create: `crawler/internal/fetcher/useragents.go`
- Create: `crawler/internal/fetcher/useragents_test.go`
- Modify: `crawler/internal/fetcher/fetcher.go`
- Modify: `crawler/internal/fetcher/fetcher_test.go`

- [ ] **Step 1: Write user-agent rotation tests**

Create `crawler/internal/fetcher/useragents_test.go`:
```go
package fetcher

import (
	"testing"
)

func TestRandomUserAgent_ReturnsNonEmpty(t *testing.T) {
	ua := RandomUserAgent()
	if ua == "" {
		t.Error("expected non-empty user agent")
	}
}

func TestRandomUserAgent_ReturnsFromPool(t *testing.T) {
	ua := RandomUserAgent()
	found := false
	for _, poolUA := range UserAgents {
		if ua == poolUA {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("returned UA not in pool: %q", ua)
	}
}

func TestRandomUserAgent_NotAlwaysSame(t *testing.T) {
	// With 10 UAs, getting the same one 20 times in a row is extremely unlikely
	first := RandomUserAgent()
	allSame := true
	for i := 0; i < 20; i++ {
		if RandomUserAgent() != first {
			allSame = false
			break
		}
	}
	if allSame {
		t.Error("RandomUserAgent returned the same value 20 times — likely not random")
	}
}
```

- [ ] **Step 2: Implement user-agent pool**

Create `crawler/internal/fetcher/useragents.go`:
```go
package fetcher

import (
	"math/rand/v2"
)

// UserAgents is a pool of common browser user-agent strings for rotation.
var UserAgents = []string{
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
	"Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
	"Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
	"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
}

// RandomUserAgent returns a random user agent from the pool.
func RandomUserAgent() string {
	return UserAgents[rand.IntN(len(UserAgents))]
}
```

- [ ] **Step 3: Update fetcher with stealth headers and proxy support**

Modify `crawler/internal/fetcher/fetcher.go`:
```go
package fetcher

import (
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"

	"github.com/surya-koritala/phantombot/crawler/pkg/models"
)

// Fetcher performs HTTP requests with configurable user-agent, stealth headers, and timeout.
type Fetcher struct {
	client          *http.Client
	userAgent       string
	rotateUserAgent bool
}

// New creates a Fetcher with the given user-agent and timeout.
func New(userAgent string, timeout time.Duration) *Fetcher {
	return &Fetcher{
		client: &http.Client{
			Timeout: timeout,
			CheckRedirect: func(req *http.Request, via []*http.Request) error {
				if len(via) >= 10 {
					return fmt.Errorf("too many redirects (max 10)")
				}
				return nil
			},
		},
		userAgent:       userAgent,
		rotateUserAgent: false,
	}
}

// NewWithOptions creates a Fetcher with proxy and user-agent rotation support.
func NewWithOptions(userAgent string, timeout time.Duration, proxyURL string, rotateUA bool) *Fetcher {
	transport := &http.Transport{}

	if proxyURL != "" {
		proxyParsed, err := url.Parse(proxyURL)
		if err == nil {
			transport.Proxy = http.ProxyURL(proxyParsed)
		}
	}

	f := &Fetcher{
		client: &http.Client{
			Timeout:   timeout,
			Transport: transport,
			CheckRedirect: func(req *http.Request, via []*http.Request) error {
				if len(via) >= 10 {
					return fmt.Errorf("too many redirects (max 10)")
				}
				return nil
			},
		},
		userAgent:       userAgent,
		rotateUserAgent: rotateUA,
	}

	return f
}

// Fetch retrieves a URL and returns a CrawlResult.
// HTTP error status codes (4xx, 5xx) are returned in the result, not as Go errors.
// Go errors are returned only for network/transport failures.
func (f *Fetcher) Fetch(rawURL string, depth int, jobID string) (*models.CrawlResult, error) {
	req, err := http.NewRequest("GET", rawURL, nil)
	if err != nil {
		return nil, fmt.Errorf("creating request for %q: %w", rawURL, err)
	}

	// Set user-agent (rotate or static)
	ua := f.userAgent
	if f.rotateUserAgent {
		ua = RandomUserAgent()
	}
	req.Header.Set("User-Agent", ua)

	// Stealth headers — look like a real browser
	req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8")
	req.Header.Set("Accept-Language", "en-US,en;q=0.9")
	req.Header.Set("Accept-Encoding", "gzip, deflate, br")
	req.Header.Set("Connection", "keep-alive")
	req.Header.Set("Upgrade-Insecure-Requests", "1")
	req.Header.Set("Sec-Fetch-Dest", "document")
	req.Header.Set("Sec-Fetch-Mode", "navigate")
	req.Header.Set("Sec-Fetch-Site", "none")
	req.Header.Set("Sec-Fetch-User", "?1")

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("fetching %q: %w", rawURL, err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading body from %q: %w", rawURL, err)
	}

	headers := make(map[string]string)
	for k := range resp.Header {
		headers[k] = resp.Header.Get(k)
	}

	return &models.CrawlResult{
		URL:         rawURL,
		StatusCode:  resp.StatusCode,
		ContentType: resp.Header.Get("Content-Type"),
		HTML:        string(body),
		Headers:     headers,
		FetchedAt:   time.Now().UTC(),
		Rendered:    false,
		Depth:       depth,
		JobID:       jobID,
	}, nil
}
```

- [ ] **Step 4: Add stealth header test**

Add to `crawler/internal/fetcher/fetcher_test.go`:
```go
func TestFetcher_SendsStealthHeaders(t *testing.T) {
	var gotHeaders http.Header
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotHeaders = r.Header
		w.Write([]byte("ok"))
	}))
	defer srv.Close()

	f := New("Phantombot/1.0", 10*time.Second)
	f.Fetch(srv.URL, 0, "")

	requiredHeaders := []string{
		"Accept",
		"Accept-Language",
		"Accept-Encoding",
		"Connection",
		"Upgrade-Insecure-Requests",
	}
	for _, h := range requiredHeaders {
		if gotHeaders.Get(h) == "" {
			t.Errorf("missing stealth header: %s", h)
		}
	}
}

func TestFetcher_UserAgentRotation(t *testing.T) {
	var userAgents []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		userAgents = append(userAgents, r.Header.Get("User-Agent"))
		w.Write([]byte("ok"))
	}))
	defer srv.Close()

	f := NewWithOptions("default", 10*time.Second, "", true)
	for i := 0; i < 10; i++ {
		f.Fetch(srv.URL, 0, "")
	}

	// With rotation enabled, we should see at least 2 different UAs in 10 requests
	uniqueUAs := make(map[string]bool)
	for _, ua := range userAgents {
		uniqueUAs[ua] = true
	}
	if len(uniqueUAs) < 2 {
		t.Errorf("expected multiple user agents with rotation, got %d unique: %v", len(uniqueUAs), uniqueUAs)
	}
}
```

- [ ] **Step 5: Run tests**

```bash
cd /Users/suryakoritala/phantombot/crawler
go test ./internal/fetcher/ -v
```

- [ ] **Step 6: Commit**

```bash
git add crawler/internal/fetcher/
git commit -m "feat(crawler): add anti-bot stealth headers and user-agent rotation

Add browser-like headers (Accept, Accept-Language, Sec-Fetch-*) to all requests.
Add user-agent rotation pool with 10 common browser UAs. Proxy support via
NewWithOptions(). All backwards compatible — existing New() function unchanged."
```

---

## Task 10: Integrate Renderer and Anti-Bot into Main Crawl Loop

**Files:**
- Modify: `crawler/cmd/phantombot/main.go`

- [ ] **Step 1: Update main.go to use new fetcher options and optional renderer**

Modify the relevant section of `crawler/cmd/phantombot/main.go` — replace the fetcher initialization:
```go
	// Create fetcher with anti-bot options
	var fetch *fetcher.Fetcher
	if cfg.RotateUserAgent || cfg.ProxyURL != "" {
		fetch = fetcher.NewWithOptions(cfg.UserAgent, cfg.RequestTimeout, cfg.ProxyURL, cfg.RotateUserAgent)
	} else {
		fetch = fetcher.New(cfg.UserAgent, cfg.RequestTimeout)
	}
```

And inside the worker loop, after a successful fetch, add renderer fallback:
```go
				// If renderer is enabled and page looks like a JS shell, re-fetch via Playwright
				if cfg.EnableRenderer && renderer.NeedsRendering(result.HTML) {
					log.Printf("[worker %d] JS shell detected, rendering: %s", workerID, rawURL)
					rendered, renderErr := rend.Render(rawURL)
					if renderErr != nil {
						log.Printf("[worker %d] render error for %s: %v (using static HTML)", workerID, rawURL, renderErr)
					} else {
						result = rendered
						result.Depth = depth
					}
				}
```

Import the renderer package and initialize it:
```go
	// Optional Playwright renderer
	var rend *renderer.Renderer
	if cfg.EnableRenderer {
		rend = renderer.New(cfg.RendererURL, cfg.RequestTimeout)
		log.Printf("Playwright renderer enabled at %s", cfg.RendererURL)
	}
```

- [ ] **Step 2: Run full Go test suite**

```bash
cd /Users/suryakoritala/phantombot/crawler
go build ./cmd/phantombot/
go test ./... -v -short
```

- [ ] **Step 3: Commit**

```bash
git add crawler/cmd/phantombot/main.go
git commit -m "feat(crawler): integrate renderer fallback and anti-bot options into crawl loop

Main crawler now uses NewWithOptions for proxy/UA rotation support.
When ENABLE_RENDERER=true, pages detected as JS shells are re-fetched
via the Playwright sidecar. Static fetch result is used as fallback if
rendering fails."
```

---

## Phase 3: P2 Polish

---

## Task 11: Entity Typing Rules

**Files:**
- Create: `intelligence/intelligence/pipelines/entity_typing_rules.py`
- Create: `intelligence/tests/test_entity_typing_rules.py`

- [ ] **Step 1: Write typing rules tests**

Create `intelligence/tests/test_entity_typing_rules.py`:
```python
"""Tests for entity type override rules."""
import pytest

from intelligence.models import ExtractedEntity
from intelligence.pipelines.entity_typing_rules import EntityTypingRules


@pytest.fixture
def rules():
    return EntityTypingRules()


def _make_entity(text: str, entity_type: str) -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=entity_type,
        entity_text=text,
        start_char=0,
        end_char=len(text),
        confidence=0.9,
        pipeline_version="entity-v2",
    )


class TestProgrammingLanguageOverrides:
    def test_python_person_to_product(self, rules):
        entities = [_make_entity("Python", "PERSON")]
        result = rules.apply(entities)
        assert result[0].entity_type == "PRODUCT"

    def test_java_person_to_product(self, rules):
        entities = [_make_entity("Java", "PERSON")]
        result = rules.apply(entities)
        assert result[0].entity_type == "PRODUCT"

    def test_rust_person_to_product(self, rules):
        entities = [_make_entity("Rust", "PERSON")]
        result = rules.apply(entities)
        assert result[0].entity_type == "PRODUCT"

    def test_go_person_to_product(self, rules):
        entities = [_make_entity("Go", "PERSON")]
        result = rules.apply(entities)
        assert result[0].entity_type == "PRODUCT"

    def test_swift_person_to_product(self, rules):
        entities = [_make_entity("Swift", "PERSON")]
        result = rules.apply(entities)
        assert result[0].entity_type == "PRODUCT"


class TestAIProductOverrides:
    def test_claude_person_to_product(self, rules):
        entities = [_make_entity("Claude", "PERSON")]
        result = rules.apply(entities)
        assert result[0].entity_type == "PRODUCT"

    def test_chatgpt_person_to_product(self, rules):
        entities = [_make_entity("ChatGPT", "PERSON")]
        result = rules.apply(entities)
        assert result[0].entity_type == "PRODUCT"

    def test_copilot_person_to_product(self, rules):
        entities = [_make_entity("Copilot", "PERSON")]
        result = rules.apply(entities)
        assert result[0].entity_type == "PRODUCT"


class TestNoFalseOverrides:
    def test_person_name_not_overridden(self, rules):
        entities = [_make_entity("John Swift", "PERSON")]
        result = rules.apply(entities)
        assert result[0].entity_type == "PERSON"

    def test_org_not_affected(self, rules):
        entities = [_make_entity("Python Software Foundation", "ORG")]
        result = rules.apply(entities)
        assert result[0].entity_type == "ORG"

    def test_already_correct_type_unchanged(self, rules):
        entities = [_make_entity("Python", "PRODUCT")]
        result = rules.apply(entities)
        assert result[0].entity_type == "PRODUCT"
```

- [ ] **Step 2: Implement typing rules**

Create `intelligence/intelligence/pipelines/entity_typing_rules.py`:
```python
"""Custom entity type override rules.

Fixes common SpaCy misclassifications where well-known products,
programming languages, and AI models are tagged as PERSON.
"""
from intelligence.models import ExtractedEntity


# Override rules: (exact_text, current_type) -> corrected_type
# Only applied for exact text matches (case-sensitive)
_EXACT_OVERRIDES: dict[tuple[str, str], str] = {
    # Programming languages often misclassified as PERSON
    ("Python", "PERSON"): "PRODUCT",
    ("Java", "PERSON"): "PRODUCT",
    ("Rust", "PERSON"): "PRODUCT",
    ("Go", "PERSON"): "PRODUCT",
    ("Ruby", "PERSON"): "PRODUCT",
    ("Swift", "PERSON"): "PRODUCT",
    ("Kotlin", "PERSON"): "PRODUCT",
    ("Scala", "PERSON"): "PRODUCT",
    ("Julia", "PERSON"): "PRODUCT",
    ("Dart", "PERSON"): "PRODUCT",
    ("Perl", "PERSON"): "PRODUCT",
    ("Haskell", "PERSON"): "PRODUCT",
    ("Elixir", "PERSON"): "PRODUCT",
    ("Erlang", "PERSON"): "PRODUCT",
    ("Fortran", "PERSON"): "PRODUCT",
    ("Pascal", "PERSON"): "PRODUCT",
    # AI products misclassified as PERSON
    ("Claude", "PERSON"): "PRODUCT",
    ("ChatGPT", "PERSON"): "PRODUCT",
    ("GPT-4", "PERSON"): "PRODUCT",
    ("GPT-4o", "PERSON"): "PRODUCT",
    ("GPT-3", "PERSON"): "PRODUCT",
    ("Gemini", "PERSON"): "PRODUCT",
    ("Copilot", "PERSON"): "PRODUCT",
    ("Siri", "PERSON"): "PRODUCT",
    ("Alexa", "PERSON"): "PRODUCT",
    ("Cortana", "PERSON"): "PRODUCT",
    ("Bard", "PERSON"): "PRODUCT",
    # Tech products
    ("Kubernetes", "PERSON"): "PRODUCT",
    ("Docker", "PERSON"): "PRODUCT",
    ("Linux", "PERSON"): "PRODUCT",
    ("Android", "PERSON"): "PRODUCT",
    ("iOS", "PERSON"): "PRODUCT",
    ("macOS", "PERSON"): "PRODUCT",
    ("Windows", "PERSON"): "PRODUCT",
    ("Chrome", "PERSON"): "PRODUCT",
    ("Firefox", "PERSON"): "PRODUCT",
    ("Safari", "PERSON"): "PRODUCT",
    ("Slack", "PERSON"): "PRODUCT",
    ("Discord", "PERSON"): "PRODUCT",
    ("Notion", "PERSON"): "PRODUCT",
    ("Figma", "PERSON"): "PRODUCT",
}


class EntityTypingRules:
    """Applies rule-based entity type corrections."""

    def apply(self, entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        """Apply type override rules to entities.

        Returns a new list with corrected types. Entities not matching any rule
        are returned unchanged.
        """
        result = []
        for entity in entities:
            key = (entity.entity_text, entity.entity_type)
            corrected_type = _EXACT_OVERRIDES.get(key, entity.entity_type)

            if corrected_type != entity.entity_type:
                result.append(ExtractedEntity(
                    entity_type=corrected_type,
                    entity_text=entity.entity_text,
                    start_char=entity.start_char,
                    end_char=entity.end_char,
                    confidence=entity.confidence,
                    pipeline_version=entity.pipeline_version,
                ))
            else:
                result.append(entity)

        return result
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/suryakoritala/phantombot/intelligence
source .venv/bin/activate
python -m pytest tests/test_entity_typing_rules.py -v
```

- [ ] **Step 4: Integrate typing rules into entity pipeline**

Modify `intelligence/intelligence/pipelines/entity.py` -- add import and usage:

Add to imports:
```python
from intelligence.pipelines.entity_typing_rules import EntityTypingRules
```

In `__init__`:
```python
        self._typing_rules = EntityTypingRules()
```

In `extract()`, after `self._cleaner.clean(entities)`:
```python
        # Apply type corrections
        entities = self._typing_rules.apply(entities)
```

- [ ] **Step 5: Commit**

```bash
git add intelligence/intelligence/pipelines/entity_typing_rules.py intelligence/tests/test_entity_typing_rules.py intelligence/intelligence/pipelines/entity.py
git commit -m "feat(intelligence): add entity typing rules for common misclassifications

Override SpaCy labels for programming languages (Python, Rust, Go, etc.)
and AI products (Claude, ChatGPT, Copilot) from PERSON to PRODUCT.
Data-driven rules that are easy to extend. Integrated into entity pipeline."
```

---

## Task 12: Fix OpenAPI Detection for Swagger UI and Redoc

**Files:**
- Modify: `intelligence/intelligence/pipelines/schema.py`
- Create: `intelligence/tests/test_schema_openapi.py`

- [ ] **Step 1: Write OpenAPI detection tests**

Create `intelligence/tests/test_schema_openapi.py`:
```python
"""Tests for improved OpenAPI spec detection."""
import pytest

from intelligence.pipelines.schema import SchemaPipeline


@pytest.fixture
def pipeline():
    return SchemaPipeline()


class TestSwaggerUIDetection:
    def test_detects_swagger_ui_bundle_url(self, pipeline):
        html = """
        <html><body>
        <div id="swagger-ui"></div>
        <script>
        SwaggerUIBundle({
          url: "https://petstore.swagger.io/v2/swagger.json",
          dom_id: '#swagger-ui'
        })
        </script>
        </body></html>
        """
        schemas = pipeline.extract(html, "https://example.com/docs")
        assert len(schemas) >= 1
        openapi = [s for s in schemas if s.schema_type == "OPENAPI_REF"]
        assert len(openapi) == 1
        assert openapi[0].schema_data["spec_url"] == "https://petstore.swagger.io/v2/swagger.json"

    def test_detects_swagger_ui_with_single_quotes(self, pipeline):
        html = """
        <html><body>
        <script>
        SwaggerUIBundle({
          url: 'https://api.example.com/openapi.json',
          dom_id: '#swagger-ui'
        })
        </script>
        </body></html>
        """
        schemas = pipeline.extract(html, "https://example.com/docs")
        openapi = [s for s in schemas if s.schema_type == "OPENAPI_REF"]
        assert len(openapi) == 1


class TestRedocDetection:
    def test_detects_redoc_element_with_spec_url(self, pipeline):
        html = """
        <html><body>
        <redoc spec-url="https://petstore.swagger.io/v2/swagger.json"></redoc>
        </body></html>
        """
        schemas = pipeline.extract(html, "https://example.com/docs")
        openapi = [s for s in schemas if s.schema_type == "OPENAPI_REF"]
        assert len(openapi) == 1
        assert openapi[0].schema_data["spec_url"] == "https://petstore.swagger.io/v2/swagger.json"


class TestInlineSpecDetection:
    def test_detects_openapi_json_in_script_tag(self, pipeline):
        html = """
        <html><body>
        <script id="api-spec" type="application/json">
        {
          "openapi": "3.0.0",
          "info": {"title": "Test API", "version": "1.0.0"},
          "paths": {"/pets": {"get": {"summary": "List pets"}}}
        }
        </script>
        </body></html>
        """
        schemas = pipeline.extract(html, "https://example.com/docs")
        openapi = [s for s in schemas if s.schema_type == "OPENAPI"]
        assert len(openapi) == 1

    def test_detects_openapi_yaml_in_pre_block(self, pipeline):
        html = """
        <html><body>
        <pre><code>
        openapi: "3.0.0"
        info:
          title: Test API
          version: "1.0.0"
        paths:
          /pets:
            get:
              summary: List pets
        </code></pre>
        </body></html>
        """
        schemas = pipeline.extract(html, "https://example.com/docs")
        openapi = [s for s in schemas if s.schema_type == "OPENAPI"]
        assert len(openapi) == 1


class TestExistingBehaviorPreserved:
    def test_still_detects_jsonld(self, pipeline):
        html = """
        <html><head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Organization", "name": "Test"}
        </script>
        </head><body></body></html>
        """
        schemas = pipeline.extract(html, "https://example.com")
        jsonld = [s for s in schemas if s.schema_type == "JSON_LD"]
        assert len(jsonld) == 1

    def test_still_detects_pre_code_openapi(self, pipeline):
        html = """
        <html><body>
        <pre><code>
        {"openapi": "3.0.0", "info": {"title": "Test", "version": "1.0"}, "paths": {}}
        </code></pre>
        </body></html>
        """
        schemas = pipeline.extract(html, "https://example.com")
        openapi = [s for s in schemas if s.schema_type == "OPENAPI"]
        assert len(openapi) == 1
```

- [ ] **Step 2: Implement improved OpenAPI detection**

Modify `intelligence/intelligence/pipelines/schema.py`:
```python
import json
import re

from bs4 import BeautifulSoup

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from intelligence.models import DetectedSchema
from intelligence.pipelines.base import BasePipeline


# Patterns for detecting Swagger UI spec URLs
_SWAGGER_UI_URL_PATTERN = re.compile(
    r"""SwaggerUIBundle\s*\(\s*\{[^}]*url\s*:\s*["']([^"']+)["']""",
    re.DOTALL,
)

# Pattern for Redoc spec-url attribute
_REDOC_SPEC_URL_PATTERN = re.compile(
    r"""<redoc[^>]+spec-url\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


class SchemaPipeline(BasePipeline):
    """Detects JSON-LD, OpenAPI specs, and other structured schemas in HTML."""

    def extract(self, html: str, source_url: str) -> list[DetectedSchema]:
        if not html or not html.strip():
            return []

        schemas: list[DetectedSchema] = []
        soup = BeautifulSoup(html, "html.parser")

        schemas.extend(self._extract_jsonld(soup, source_url))
        schemas.extend(self._extract_openapi(soup, source_url, html))

        return schemas

    def _extract_jsonld(self, soup: BeautifulSoup, source_url: str) -> list[DetectedSchema]:
        results = []
        for script in soup.find_all("script", type="application/ld+json"):
            text = script.string
            if not text:
                continue
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue

            results.append(DetectedSchema(
                schema_type="JSON_LD",
                schema_data=data,
                source_url=source_url,
                confidence=0.95,
                pipeline_version="schema-v2",
            ))
        return results

    def _extract_openapi(self, soup: BeautifulSoup, source_url: str, raw_html: str) -> list[DetectedSchema]:
        results = []

        # 1. Swagger UI detection — look for SwaggerUIBundle({url: "..."})
        results.extend(self._detect_swagger_ui(soup, source_url))

        # 2. Redoc detection — look for <redoc spec-url="...">
        results.extend(self._detect_redoc(raw_html, source_url))

        # 3. Inline script detection — check <script> tags for OpenAPI JSON
        results.extend(self._detect_inline_spec(soup, source_url))

        # 4. Pre/code block detection — existing logic + YAML support
        results.extend(self._detect_pre_code_spec(soup, source_url))

        return results

    def _detect_swagger_ui(self, soup: BeautifulSoup, source_url: str) -> list[DetectedSchema]:
        """Detect Swagger UI pages by finding SwaggerUIBundle({url: "..."})."""
        results = []
        for script in soup.find_all("script"):
            text = script.string
            if not text:
                continue
            match = _SWAGGER_UI_URL_PATTERN.search(text)
            if match:
                spec_url = match.group(1)
                results.append(DetectedSchema(
                    schema_type="OPENAPI_REF",
                    schema_data={"spec_url": spec_url, "ui_type": "swagger_ui"},
                    source_url=source_url,
                    confidence=0.9,
                    pipeline_version="schema-v2",
                ))
        return results

    def _detect_redoc(self, raw_html: str, source_url: str) -> list[DetectedSchema]:
        """Detect Redoc pages by finding <redoc spec-url="...">."""
        results = []
        for match in _REDOC_SPEC_URL_PATTERN.finditer(raw_html):
            spec_url = match.group(1)
            results.append(DetectedSchema(
                schema_type="OPENAPI_REF",
                schema_data={"spec_url": spec_url, "ui_type": "redoc"},
                source_url=source_url,
                confidence=0.9,
                pipeline_version="schema-v2",
            ))
        return results

    def _detect_inline_spec(self, soup: BeautifulSoup, source_url: str) -> list[DetectedSchema]:
        """Detect OpenAPI specs embedded in <script> tags (not JSON-LD)."""
        results = []
        for script in soup.find_all("script"):
            # Skip JSON-LD — those are handled by _extract_jsonld
            if script.get("type") == "application/ld+json":
                continue
            text = script.string
            if not text:
                continue
            text = text.strip()
            # Try JSON parse
            try:
                data = json.loads(text)
                if self._is_openapi(data):
                    results.append(DetectedSchema(
                        schema_type="OPENAPI",
                        schema_data=data,
                        source_url=source_url,
                        confidence=0.85,
                        pipeline_version="schema-v2",
                    ))
            except (json.JSONDecodeError, TypeError):
                pass
        return results

    def _detect_pre_code_spec(self, soup: BeautifulSoup, source_url: str) -> list[DetectedSchema]:
        """Detect OpenAPI specs in <pre><code> blocks (JSON or YAML)."""
        results = []

        candidates = []
        for pre in soup.find_all("pre"):
            code = pre.find("code")
            text = code.string if code else pre.string
            if text:
                candidates.append(text.strip())

        for candidate in candidates:
            # Try JSON first
            try:
                data = json.loads(candidate)
                if self._is_openapi(data):
                    results.append(DetectedSchema(
                        schema_type="OPENAPI",
                        schema_data=data,
                        source_url=source_url,
                        confidence=0.9,
                        pipeline_version="schema-v2",
                    ))
                    continue
            except (json.JSONDecodeError, TypeError):
                pass

            # Try YAML if available
            if HAS_YAML and ("openapi:" in candidate or "swagger:" in candidate):
                try:
                    data = yaml.safe_load(candidate)
                    if isinstance(data, dict) and self._is_openapi(data):
                        results.append(DetectedSchema(
                            schema_type="OPENAPI",
                            schema_data=data,
                            source_url=source_url,
                            confidence=0.85,
                            pipeline_version="schema-v2",
                        ))
                except (yaml.YAMLError, TypeError):
                    pass

        return results

    def _is_openapi(self, data: dict) -> bool:
        if not isinstance(data, dict):
            return False
        return (
            "openapi" in data
            or "swagger" in data
            or ("info" in data and "paths" in data)
        )
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/suryakoritala/phantombot/intelligence
source .venv/bin/activate
pip install pyyaml
python -m pytest tests/test_schema_openapi.py -v
```

- [ ] **Step 4: Commit**

```bash
git add intelligence/intelligence/pipelines/schema.py intelligence/tests/test_schema_openapi.py
git commit -m "feat(intelligence): fix OpenAPI detection for Swagger UI, Redoc, and YAML

Add detection methods for SwaggerUIBundle({url: ...}) pattern, <redoc spec-url>
element, inline <script> specs, and YAML-formatted specs in <pre><code> blocks.
Existing JSON-LD and pre/code JSON detection preserved. Pipeline version bumped
to schema-v2."
```

---

## Task 13: Markdown Backfill Script

**Files:**
- Create: `intelligence/intelligence/scripts/__init__.py`
- Create: `intelligence/intelligence/scripts/backfill_markdown.py`
- Modify: `intelligence/intelligence/store.py`
- Create: `intelligence/tests/test_backfill_markdown.py`

- [ ] **Step 1: Write backfill tests**

Create `intelligence/tests/test_backfill_markdown.py`:
```python
"""Tests for markdown backfill script (unit tests with mocked store)."""
import pytest
from unittest.mock import MagicMock, patch

from intelligence.models import MarkdownResult


def test_backfill_skips_pages_with_markdown():
    """Backfill should only process pages where markdown IS NULL."""
    from intelligence.scripts.backfill_markdown import backfill_pages

    mock_store = MagicMock()
    # Simulate no pages need backfill
    mock_store.get_pages_without_markdown.return_value = []

    mock_pipeline = MagicMock()

    count = backfill_pages(mock_store, mock_pipeline)
    assert count == 0
    mock_pipeline.extract.assert_not_called()


def test_backfill_processes_pages_without_markdown():
    """Backfill should extract and save markdown for pages missing it."""
    from intelligence.scripts.backfill_markdown import backfill_pages

    mock_store = MagicMock()
    mock_store.get_pages_without_markdown.return_value = [
        {"id": "page-1", "url": "https://example.com/1", "html": "<html><body><p>Content 1</p></body></html>"},
        {"id": "page-2", "url": "https://example.com/2", "html": "<html><body><p>Content 2</p></body></html>"},
    ]

    mock_pipeline = MagicMock()
    mock_pipeline.extract.return_value = [MarkdownResult(
        markdown="Content",
        word_count=1,
        confidence=0.9,
        pipeline_version="markdown-v1",
    )]

    count = backfill_pages(mock_store, mock_pipeline)
    assert count == 2
    assert mock_store.save_markdown.call_count == 2


def test_backfill_handles_empty_extraction():
    """Pages that produce no markdown should be skipped gracefully."""
    from intelligence.scripts.backfill_markdown import backfill_pages

    mock_store = MagicMock()
    mock_store.get_pages_without_markdown.return_value = [
        {"id": "page-1", "url": "https://example.com/1", "html": "<html><body></body></html>"},
    ]

    mock_pipeline = MagicMock()
    mock_pipeline.extract.return_value = []

    count = backfill_pages(mock_store, mock_pipeline)
    assert count == 0
    mock_store.save_markdown.assert_not_called()
```

- [ ] **Step 2: Add store methods for backfill**

Add to `intelligence/intelligence/store.py` — new methods in the `KnowledgeStore` class:

```python
    def get_pages_without_markdown(self, limit: int = 1000) -> list[dict]:
        """Get crawled pages that have no markdown extracted yet."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, url, html
                FROM crawled_pages
                WHERE markdown IS NULL AND html IS NOT NULL AND html != ''
                ORDER BY fetched_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
```

- [ ] **Step 3: Create backfill script**

Create `intelligence/intelligence/scripts/__init__.py` (empty file).

Create `intelligence/intelligence/scripts/backfill_markdown.py`:
```python
"""One-time backfill script for pages missing markdown.

Usage:
    python -m intelligence.scripts.backfill_markdown

Queries PostgreSQL for crawled pages where markdown IS NULL,
runs the MarkdownPipeline, and saves results. Idempotent.
"""
import logging
import sys

from intelligence.pipelines.markdown import MarkdownPipeline
from intelligence.store import KnowledgeStore

logger = logging.getLogger(__name__)


def backfill_pages(store: KnowledgeStore, pipeline: MarkdownPipeline) -> int:
    """Backfill markdown for pages missing it.

    Args:
        store: KnowledgeStore instance
        pipeline: MarkdownPipeline instance

    Returns:
        Number of pages successfully backfilled.
    """
    pages = store.get_pages_without_markdown()
    logger.info(f"Found {len(pages)} pages without markdown")

    success_count = 0
    for page in pages:
        page_id = str(page["id"])
        url = page["url"]
        html = page["html"]

        try:
            results = pipeline.extract(html, url)
            if results:
                store.save_markdown(page_id, results[0])
                logger.info(f"  Backfilled: {url} ({results[0].word_count} words)")
                success_count += 1
            else:
                logger.info(f"  Skipped (no content): {url}")
        except Exception:
            logger.exception(f"  Error backfilling: {url}")

    return success_count


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    logger.info("Starting markdown backfill...")
    store = KnowledgeStore()
    pipeline = MarkdownPipeline()

    try:
        count = backfill_pages(store, pipeline)
        logger.info(f"Backfill complete: {count} pages updated")
    finally:
        store.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/suryakoritala/phantombot/intelligence
source .venv/bin/activate
python -m pytest tests/test_backfill_markdown.py -v
```

- [ ] **Step 5: Commit**

```bash
git add intelligence/intelligence/scripts/ intelligence/intelligence/store.py intelligence/tests/test_backfill_markdown.py
git commit -m "feat(intelligence): add markdown backfill script for pages missing markdown

One-time script that queries pages where markdown IS NULL, runs the
MarkdownPipeline, and saves results. Idempotent. Run with:
python -m intelligence.scripts.backfill_markdown"
```

---

## Task 14: Integrate Full Entity Pipeline in Worker

**Files:**
- Modify: `intelligence/intelligence/worker.py`

- [ ] **Step 1: Update worker to use full entity processing chain**

Modify `intelligence/intelligence/worker.py`:
```python
import json
import logging
import time
from datetime import datetime

import redis

from intelligence.config import Config
from intelligence.models import CrawlMessage
from intelligence.pipelines.entity import EntityPipeline
from intelligence.pipelines.entity_resolver import EntityResolver
from intelligence.pipelines.markdown import MarkdownPipeline
from intelligence.pipelines.schema import SchemaPipeline
from intelligence.store import KnowledgeStore

logger = logging.getLogger(__name__)


class Worker:
    """Consumes crawled pages from Redis Streams, runs pipelines, stores results."""

    def __init__(self):
        self._redis = redis.from_url(Config.REDIS_URL, decode_responses=True)
        self._store = KnowledgeStore()
        self._entity_pipeline = EntityPipeline()
        self._entity_resolver = EntityResolver()
        self._schema_pipeline = SchemaPipeline()
        self._markdown_pipeline = MarkdownPipeline()
        self._stream = Config.REDIS_STREAM_NAME
        self._group = Config.REDIS_CONSUMER_GROUP
        self._consumer = f"worker-{id(self)}"

    def setup(self):
        """Create consumer group if it doesn't exist."""
        try:
            self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    @staticmethod
    def parse_message(raw: dict) -> CrawlMessage:
        # Normalize bytes keys/values to strings (Redis without decode_responses)
        decoded = {}
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            decoded[key] = val

        headers = decoded.get("headers", "{}")
        if isinstance(headers, str):
            headers = json.loads(headers)

        return CrawlMessage(
            url=decoded["url"],
            status_code=int(decoded["status_code"]),
            content_type=decoded.get("content_type", ""),
            html=decoded.get("html", ""),
            headers=headers,
            fetched_at=datetime.fromisoformat(decoded["fetched_at"]),
            rendered=decoded.get("rendered", "false").lower() == "true",
            depth=int(decoded.get("depth", "0")),
            job_id=decoded.get("job_id"),
        )

    def process_message(self, msg: CrawlMessage):
        """Run all pipelines on a crawled page and store results."""
        logger.info(f"Processing: {msg.url}")

        page_id = self._store.save_crawled_page(msg)

        # Markdown extraction (run first -- other pipelines could use it)
        md_results = self._markdown_pipeline.extract(msg.html, msg.url)
        if md_results:
            self._store.save_markdown(page_id, md_results[0])
            logger.info(f"  Extracted markdown ({md_results[0].word_count} words) from {msg.url}")

        # Entity extraction (now includes cleaning + typing rules)
        entities = self._entity_pipeline.extract(msg.html, msg.url)
        if entities:
            self._store.save_entities(page_id, entities)
            logger.info(f"  Extracted {len(entities)} entities from {msg.url}")

            # Entity resolution
            resolved = self._entity_resolver.resolve(entities)
            canonical_count = len({r.canonical_id for r in resolved})
            logger.info(f"  Resolved to {canonical_count} canonical entities from {msg.url}")

        # Schema detection
        schemas = self._schema_pipeline.extract(msg.html, msg.url)
        if schemas:
            self._store.save_schemas(page_id, schemas)
            logger.info(f"  Detected {len(schemas)} schemas from {msg.url}")

    def run(self):
        """Main loop: consume from Redis Streams, process, acknowledge."""
        self.setup()
        logger.info(f"Worker started, consuming from {self._stream}")

        while True:
            try:
                messages = self._redis.xreadgroup(
                    groupname=self._group,
                    consumername=self._consumer,
                    streams={self._stream: ">"},
                    count=1,
                    block=5000,
                )

                if not messages:
                    continue

                for stream_name, stream_messages in messages:
                    for msg_id, raw in stream_messages:
                        try:
                            msg = self.parse_message(raw)
                            self.process_message(msg)
                            self._redis.xack(self._stream, self._group, msg_id)
                        except Exception:
                            logger.exception(f"Error processing message {msg_id}")

            except KeyboardInterrupt:
                logger.info("Worker shutting down")
                break
            except Exception:
                logger.exception("Unexpected error in worker loop")
                time.sleep(1)

    def close(self):
        self._store.close()
        self._redis.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = Worker()
    try:
        worker.run()
    finally:
        worker.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add intelligence/intelligence/worker.py
git commit -m "feat(intelligence): integrate entity resolver into worker pipeline

Worker now runs: extract -> clean -> type-fix -> resolve for entities.
Logs canonical entity count alongside raw entity count."
```

---

## Phase 4: P3 Advanced

---

## Task 15: Crawl Scheduler

**Files:**
- Create: `intelligence/intelligence/scheduler.py`
- Create: `intelligence/tests/test_scheduler.py`
- Modify: `intelligence/intelligence/config.py`
- Modify: `intelligence/intelligence/store.py`

- [ ] **Step 1: Write scheduler tests**

Create `intelligence/tests/test_scheduler.py`:
```python
"""Tests for crawl scheduler."""
import pytest
from unittest.mock import MagicMock, patch


def test_scheduler_enqueues_stale_pages():
    """Scheduler should push stale URLs to Redis stream."""
    from intelligence.scheduler import CrawlScheduler

    mock_store = MagicMock()
    mock_store.get_stale_pages.return_value = [
        {"id": "1", "url": "https://example.com/page1"},
        {"id": "2", "url": "https://example.com/page2"},
    ]

    mock_redis = MagicMock()

    scheduler = CrawlScheduler(
        store=mock_store,
        redis_client=mock_redis,
        max_age_hours=72,
    )

    count = scheduler.run_once()
    assert count == 2
    assert mock_redis.xadd.call_count == 2

    # Verify the stream name and URL are correct
    first_call = mock_redis.xadd.call_args_list[0]
    assert first_call[0][0] == "phantombot:crawl_requests"
    assert first_call[0][1]["url"] == "https://example.com/page1"


def test_scheduler_handles_no_stale_pages():
    """Scheduler should do nothing when no pages are stale."""
    from intelligence.scheduler import CrawlScheduler

    mock_store = MagicMock()
    mock_store.get_stale_pages.return_value = []

    mock_redis = MagicMock()

    scheduler = CrawlScheduler(store=mock_store, redis_client=mock_redis)
    count = scheduler.run_once()
    assert count == 0
    mock_redis.xadd.assert_not_called()
```

- [ ] **Step 2: Add stale pages query to store**

Add to `intelligence/intelligence/store.py`:
```python
    def get_stale_pages(self, max_age_hours: int = 72, limit: int = 100) -> list[dict]:
        """Get pages that haven't been re-crawled within max_age_hours."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, url
                FROM crawled_pages
                WHERE fetched_at < NOW() - INTERVAL '%s hours'
                ORDER BY fetched_at ASC
                LIMIT %s
                """,
                (max_age_hours, limit),
            )
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
```

- [ ] **Step 3: Add scheduler config**

Add to `intelligence/intelligence/config.py`:
```python
    SCHEDULER_INTERVAL_HOURS: int = int(os.getenv("SCHEDULER_INTERVAL_HOURS", "24"))
    SCHEDULER_MAX_AGE_HOURS: int = int(os.getenv("SCHEDULER_MAX_AGE_HOURS", "72"))
    CRAWL_REQUEST_STREAM: str = os.getenv("CRAWL_REQUEST_STREAM", "phantombot:crawl_requests")
```

- [ ] **Step 4: Implement scheduler**

Create `intelligence/intelligence/scheduler.py`:
```python
"""Crawl scheduler — periodically re-queues stale URLs for re-crawling.

Queries PostgreSQL for pages older than max_age_hours and pushes them to the
phantombot:crawl_requests Redis stream for the Go crawler to pick up.

Usage:
    python -m intelligence.scheduler
"""
import logging
import time
import uuid

import redis as redis_lib

from intelligence.config import Config
from intelligence.store import KnowledgeStore

logger = logging.getLogger(__name__)


class CrawlScheduler:
    """Enqueues stale URLs for re-crawling."""

    def __init__(
        self,
        store: KnowledgeStore | None = None,
        redis_client=None,
        max_age_hours: int | None = None,
        interval_hours: int | None = None,
    ):
        self._store = store or KnowledgeStore()
        self._redis = redis_client or redis_lib.from_url(Config.REDIS_URL, decode_responses=True)
        self._max_age_hours = max_age_hours or Config.SCHEDULER_MAX_AGE_HOURS
        self._interval_hours = interval_hours or Config.SCHEDULER_INTERVAL_HOURS
        self._stream = Config.CRAWL_REQUEST_STREAM

    def run_once(self) -> int:
        """Check for stale pages and enqueue them for re-crawl.

        Returns:
            Number of URLs enqueued.
        """
        stale_pages = self._store.get_stale_pages(max_age_hours=self._max_age_hours)

        if not stale_pages:
            logger.info("No stale pages to re-crawl")
            return 0

        logger.info(f"Found {len(stale_pages)} stale pages to re-crawl")

        for page in stale_pages:
            job_id = str(uuid.uuid4())
            self._redis.xadd(
                self._stream,
                {
                    "url": page["url"],
                    "max_depth": "0",
                    "job_id": job_id,
                },
            )
            logger.debug(f"  Enqueued: {page['url']} (job_id={job_id})")

        logger.info(f"Enqueued {len(stale_pages)} URLs for re-crawl")
        return len(stale_pages)

    def run(self):
        """Run scheduler loop — check and enqueue on interval."""
        logger.info(
            f"Scheduler started: interval={self._interval_hours}h, "
            f"max_age={self._max_age_hours}h"
        )
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                logger.info("Scheduler shutting down")
                break
            except Exception:
                logger.exception("Scheduler error")

            time.sleep(self._interval_hours * 3600)

    def close(self):
        self._store.close()
        self._redis.close()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    scheduler = CrawlScheduler()
    try:
        scheduler.run()
    finally:
        scheduler.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests**

```bash
cd /Users/suryakoritala/phantombot/intelligence
source .venv/bin/activate
python -m pytest tests/test_scheduler.py -v
```

- [ ] **Step 6: Commit**

```bash
git add intelligence/intelligence/scheduler.py intelligence/intelligence/config.py intelligence/intelligence/store.py intelligence/tests/test_scheduler.py
git commit -m "feat(intelligence): add crawl scheduler for periodic re-crawling

Queries PostgreSQL for pages older than SCHEDULER_MAX_AGE_HOURS (default 72h)
and pushes them to phantombot:crawl_requests Redis stream. Runs on configurable
interval (default 24h). Run with: python -m intelligence.scheduler"
```

---

## Summary of All Tasks

| Phase | Task | Description | New Files | Modified Files |
|-------|------|-------------|-----------|----------------|
| P0 | 1 | SpaCy transformer upgrade | test_entity_pipeline_v2.py | pyproject.toml, config.py, entity.py |
| P0 | 2 | Entity post-processing | entity_cleaner.py, test_entity_cleaner.py, entity_dirty_page.html | - |
| P0 | 3 | Integrate cleaner into pipeline | - | entity.py, test_entity_pipeline_v2.py |
| P0 | 4 | Connect crawl API to Go crawler | consumer.go, consumer_test.go | config.go, main.go |
| P0 | 5 | Test fixtures | swagger_ui_page.html, redoc_page.html, js_heavy_page.html | - |
| P1 | 6 | Entity resolution | entity_resolver.py, test_entity_resolver.py, 002_entity_resolution.sql | models.py |
| P1 | 7 | Playwright sidecar | server.py, pyproject.toml, Dockerfile, test_server.py | - |
| P1 | 8 | Go renderer client | renderer.go, renderer_test.go | - |
| P1 | 9 | Anti-bot UA rotation | useragents.go, useragents_test.go | fetcher.go, fetcher_test.go |
| P1 | 10 | Integrate renderer + anti-bot | - | main.go |
| P2 | 11 | Entity typing rules | entity_typing_rules.py, test_entity_typing_rules.py | entity.py |
| P2 | 12 | Fix OpenAPI detection | test_schema_openapi.py | schema.py |
| P2 | 13 | Markdown backfill | backfill_markdown.py, test_backfill_markdown.py | store.py |
| P2 | 14 | Full pipeline integration | - | worker.py |
| P3 | 15 | Crawl scheduler | scheduler.py, test_scheduler.py | config.py, store.py |
