import pytest
from intelligence.pipelines.schema import SchemaPipeline


@pytest.fixture
def pipeline():
    return SchemaPipeline()


@pytest.fixture
def swagger_html():
    import pathlib
    return (pathlib.Path(__file__).parent.parent.parent / "fixtures" / "swagger_ui_page.html").read_text()


@pytest.fixture
def redoc_html():
    import pathlib
    return (pathlib.Path(__file__).parent.parent.parent / "fixtures" / "redoc_page.html").read_text()


def test_detects_swagger_ui_spec(pipeline, swagger_html):
    schemas = pipeline.extract(swagger_html, "https://petstore.example.com")
    openapi = [s for s in schemas if s.schema_type == "OPENAPI"]
    assert len(openapi) >= 1
    assert openapi[0].schema_data["info"]["title"] == "Pet Store API"


def test_detects_redoc_spec(pipeline, redoc_html):
    schemas = pipeline.extract(redoc_html, "https://api.example.com/docs")
    openapi = [s for s in schemas if s.schema_type == "OPENAPI"]
    assert len(openapi) >= 1
    assert openapi[0].schema_data["info"]["title"] == "Payment API"
