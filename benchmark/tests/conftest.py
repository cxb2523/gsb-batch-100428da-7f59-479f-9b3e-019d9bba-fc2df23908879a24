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
