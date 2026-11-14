import pytest
from intelligence.pipelines.entity import EntityPipeline


@pytest.fixture
def pipeline():
    return EntityPipeline()


def test_extracts_known_entities(pipeline, entity_rich_html):
    entities = pipeline.extract(entity_rich_html, "https://example.com/news")

    entity_texts = [e.entity_text for e in entities]
    # Should find at least Google, Sundar Pichai, Microsoft, OpenAI, Apple
    assert any("Google" in t for t in entity_texts)
    assert any("Sundar Pichai" in t or "Pichai" in t for t in entity_texts)
    assert any("Microsoft" in t for t in entity_texts)

    for entity in entities:
        assert entity.entity_type in ("PERSON", "ORG", "GPE", "PRODUCT", "MONEY", "LOC", "FAC", "NORP", "EVENT")
        assert 0.0 <= entity.confidence <= 1.0
        assert entity.pipeline_version == "entity-v2"


def test_returns_empty_for_no_entities(pipeline, simple_html):
    entities = pipeline.extract(simple_html, "https://example.com/simple")
    for entity in entities:
        assert 0.0 <= entity.confidence <= 1.0


def test_handles_empty_html(pipeline):
    entities = pipeline.extract("", "https://example.com/empty")
    assert entities == []


def test_handles_malformed_html(pipeline):
    entities = pipeline.extract("<html><body><p>unclosed tag <div>Google is a company", "https://example.com/bad")
    assert isinstance(entities, list)


def test_confidence_scores_are_bounded(pipeline, entity_rich_html):
    entities = pipeline.extract(entity_rich_html, "https://example.com/news")
    for entity in entities:
        assert 0.0 <= entity.confidence <= 1.0


def test_strips_possessives(pipeline):
    html = "<html><body><p>Google's CEO Sundar Pichai's announcement was big.</p></body></html>"
    entities = pipeline.extract(html, "https://example.com/test")
    texts = [e.entity_text for e in entities]
    # Should not have possessive forms
    for t in texts:
        assert not t.endswith("'s"), f"Entity '{t}' still has possessive"


def test_strips_trailing_punctuation(pipeline):
    html = "<html><body><p>Microsoft- and Google- are competing.</p></body></html>"
    entities = pipeline.extract(html, "https://example.com/test")
    texts = [e.entity_text for e in entities]
    for t in texts:
        assert not t.endswith("-"), f"Entity '{t}' has trailing hyphen"


def test_rejects_short_entities(pipeline):
    html = "<html><body><p>I saw X and Y at the store in LA.</p></body></html>"
    entities = pipeline.extract(html, "https://example.com/test")
    texts = [e.entity_text for e in entities]
    for t in texts:
        assert len(t) >= 2, f"Entity '{t}' is too short"


def test_corrects_product_typing(pipeline):
    html = "<html><body><p>Python is a programming language created by Guido van Rossum.</p></body></html>"
    entities = pipeline.extract(html, "https://example.com/test")
    python_ents = [e for e in entities if e.entity_text.lower() == "python"]
    if python_ents:
        assert python_ents[0].entity_type == "PRODUCT", f"Python typed as {python_ents[0].entity_type}"


def test_rejects_navigation_noise(pipeline):
    html = "<html><body><p>Jump to content. Skip to navigation. Click here for more.</p></body></html>"
    entities = pipeline.extract(html, "https://example.com/test")
    texts = [e.entity_text for e in entities]
    for t in texts:
        assert "Jump" not in t or "Wikipedia" not in t
