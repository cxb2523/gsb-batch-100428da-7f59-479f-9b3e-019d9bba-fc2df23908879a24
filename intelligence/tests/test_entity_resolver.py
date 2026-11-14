import pytest
from intelligence.pipelines.entity_resolver import EntityResolver
from intelligence.models import ExtractedEntity


@pytest.fixture
def resolver():
    return EntityResolver()


def _make_entity(text, etype="ORG", confidence=0.9):
    return ExtractedEntity(
        entity_type=etype, entity_text=text, confidence=confidence, pipeline_version="entity-v2"
    )


def test_merges_corp_suffixes(resolver):
    entities = [
        _make_entity("Tesla"),
        _make_entity("Tesla Inc"),
        _make_entity("Tesla, Inc."),
    ]
    result = resolver.resolve(entities)
    assert len(result) == 1
    assert "Tesla" in result[0].entity_text


def test_merges_similar_names(resolver):
    entities = [
        _make_entity("Tesla Motors"),
        _make_entity("Tesla"),
    ]
    result = resolver.resolve(entities)
    assert len(result) == 1


def test_does_not_merge_different_types(resolver):
    entities = [
        _make_entity("Apple", "ORG"),
        _make_entity("Apple", "PRODUCT"),
    ]
    result = resolver.resolve(entities)
    assert len(result) == 2


def test_does_not_merge_different_entities(resolver):
    entities = [
        _make_entity("Google"),
        _make_entity("Microsoft"),
    ]
    result = resolver.resolve(entities)
    assert len(result) == 2


def test_picks_most_common_form(resolver):
    entities = [
        _make_entity("Tesla Inc"),
        _make_entity("Tesla"),
        _make_entity("Tesla"),
        _make_entity("Tesla"),
    ]
    result = resolver.resolve(entities)
    assert len(result) == 1
    assert result[0].entity_text == "Tesla"


def test_boosts_confidence_for_multiple_mentions(resolver):
    entities = [
        _make_entity("Google", confidence=0.8),
        _make_entity("Google Inc", confidence=0.85),
    ]
    result = resolver.resolve(entities)
    assert result[0].confidence > 0.85


def test_empty_input(resolver):
    assert resolver.resolve([]) == []


def test_single_entity(resolver):
    entities = [_make_entity("Google")]
    result = resolver.resolve(entities)
    assert len(result) == 1
    assert result[0].entity_text == "Google"


def test_person_name_variants(resolver):
    entities = [
        _make_entity("Elon Musk", "PERSON"),
        _make_entity("Musk", "PERSON"),
    ]
    result = resolver.resolve(entities)
    # "Musk" is a substring of "Elon Musk" — should merge
    assert len(result) == 1
