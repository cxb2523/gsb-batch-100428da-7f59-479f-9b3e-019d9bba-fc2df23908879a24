import pathlib
import pytest

FIXTURES_DIR = pathlib.Path(__file__).parent.parent.parent / "fixtures"


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
def article_html() -> str:
    return (FIXTURES_DIR / "article_page.html").read_text()


@pytest.fixture
def code_doc_html() -> str:
    return (FIXTURES_DIR / "code_doc_page.html").read_text()


@pytest.fixture
def swagger_html() -> str:
    return (FIXTURES_DIR / "swagger_ui_page.html").read_text()


@pytest.fixture
def redoc_html() -> str:
    return (FIXTURES_DIR / "redoc_page.html").read_text()
