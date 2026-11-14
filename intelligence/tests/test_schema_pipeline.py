import pytest
from intelligence.pipelines.schema import SchemaPipeline


@pytest.fixture
def pipeline():
    return SchemaPipeline()


def test_detects_jsonld(pipeline, jsonld_html):
    schemas = pipeline.extract(jsonld_html, "https://example.com/org")

    assert len(schemas) >= 1
    jsonld = [s for s in schemas if s.schema_type == "JSON_LD"]
    assert len(jsonld) >= 1

    schema = jsonld[0]
    assert schema.schema_data["@type"] == "Organization"
    assert schema.schema_data["name"] == "Phantombot Inc"
    assert schema.source_url == "https://example.com/org"
    assert 0.0 <= schema.confidence <= 1.0
    assert schema.pipeline_version == "schema-v1"


def test_detects_openapi(pipeline, openapi_html):
    schemas = pipeline.extract(openapi_html, "https://example.com/api-docs")

    openapi = [s for s in schemas if s.schema_type == "OPENAPI"]
    assert len(openapi) >= 1

    schema = openapi[0]
    assert "openapi" in schema.schema_data
    assert schema.schema_data["info"]["title"] == "Petstore API"
    assert 0.0 <= schema.confidence <= 1.0


def test_no_schemas_in_simple_page(pipeline, simple_html):
    schemas = pipeline.extract(simple_html, "https://example.com/simple")
    assert schemas == []


def test_handles_empty_html(pipeline):
    schemas = pipeline.extract("", "https://example.com/empty")
    assert schemas == []


def test_handles_malformed_html(pipeline):
    schemas = pipeline.extract("<html><script type='application/ld+json'>not valid json</script>", "https://example.com/bad")
    assert isinstance(schemas, list)
