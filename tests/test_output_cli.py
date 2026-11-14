import csv
import io
import json

import pytest

from gmaps_extractor import FIELDS, cli
from gmaps_extractor.collector import CollectionResult
from gmaps_extractor.exceptions import BoundaryError
from gmaps_extractor.output import write_rows

ROW = {
    "name": "Example Coffee Roasters",
    "address": "100 Example St",
    "phone": "(512) 555-0100",
    "website": "https://example-coffee.test/",
    "rating": 4.6,
    "reviews_count": 812,
    "category": "Coffee shop",
    "categories": ["Coffee shop", "Cafe"],
    "latitude": 30.2672,
    "longitude": -97.7431,
    "place_id": "ChIJexample",
    "google_maps_url": "https://www.google.com/maps/search/?api=1&query=x&query_place_id=ChIJexample",
    "hours": {"monday": "7 AM-6 PM"},
}


def test_write_json_and_jsonl():
    out = io.StringIO()
    write_rows([ROW], out, "json")
    assert json.loads(out.getvalue()) == [ROW]
    out = io.StringIO()
    write_rows([ROW, ROW], out, "jsonl")
    assert [json.loads(line) for line in out.getvalue().splitlines()] == [ROW, ROW]


def test_write_csv():
    out = io.StringIO()
    write_rows([ROW, dict(ROW, hours=None, categories=[])], out, "csv")
    rows = list(csv.DictReader(io.StringIO(out.getvalue())))
    assert tuple(rows[0]) == FIELDS
    assert rows[0]["categories"] == "Coffee shop; Cafe"
    assert json.loads(rows[0]["hours"]) == {"monday": "7 AM-6 PM"}
    assert rows[1]["hours"] == "" and rows[1]["categories"] == ""


def test_write_unknown_format():
    with pytest.raises(ValueError):
        write_rows([], io.StringIO(), "xml")


@pytest.fixture
def fake_collect(monkeypatch):
    calls = []

    def fake(area, query, **kwargs):
        calls.append((area, query, kwargs))
        return CollectionResult([ROW], {"places_found": 1})

    monkeypatch.setattr(cli, "collect", fake)
    return calls


def test_cli_stdout_json(fake_collect, capsys):
    assert cli.main(["Austin, Texas", "coffee shop", "-n", "10", "-q"]) == 0
    assert json.loads(capsys.readouterr().out) == [ROW]
    area, query, kwargs = fake_collect[0]
    assert (area, query) == ("Austin, Texas", "coffee shop")
    assert kwargs["max_results"] == 10 and kwargs["hours"] is True and kwargs["proxy"] is None


def test_cli_file_format_from_extension(fake_collect, tmp_path):
    target = tmp_path / "out.csv"
    assert cli.main(["Austin", "coffee", "--no-hours", "-o", str(target), "-q"]) == 0
    assert target.read_text(encoding="utf-8").splitlines()[0] == ",".join(FIELDS)
    assert fake_collect[0][2]["hours"] is False


def test_cli_proxy_flag(fake_collect):
    cli.main(["Austin", "coffee", "--proxy", "http://user:pass@host:1", "-q"])
    assert fake_collect[0][2]["proxy"] == "http://user:pass@host:1"


def test_cli_error_exit_code(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise BoundaryError("no area found for 'Nowhere'")

    monkeypatch.setattr(cli, "collect", boom)
    assert cli.main(["Nowhere", "coffee", "-q"]) == 1
    assert "error:" in capsys.readouterr().err


def test_cli_has_no_review_options(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "review" not in capsys.readouterr().out.lower()
