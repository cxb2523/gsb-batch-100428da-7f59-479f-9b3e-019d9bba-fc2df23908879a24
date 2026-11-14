"""Write rows as JSON, JSON Lines or CSV."""

import csv
import json
from typing import IO, Any, Dict, Iterable

from .collector import FIELDS

FORMATS = ("json", "jsonl", "csv")


def _csv_value(field: str, value: Any) -> Any:
    if value is None:
        return ""
    if field == "categories":
        return "; ".join(value)
    if field == "hours":
        return json.dumps(value, ensure_ascii=False)
    return value


def write_rows(rows: Iterable[Dict[str, Any]], stream: IO[str], fmt: str = "json") -> None:
    """Write ``rows`` to ``stream`` in ``fmt`` (``json``, ``jsonl`` or ``csv``).

    In CSV, ``categories`` is joined with ``"; "`` and ``hours`` is a JSON object.
    """
    if fmt == "json":
        json.dump(list(rows), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    elif fmt == "jsonl":
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    elif fmt == "csv":
        writer = csv.DictWriter(stream, fieldnames=list(FIELDS), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(field, row.get(field)) for field in FIELDS})
    else:
        raise ValueError(f"unknown format {fmt!r}; use one of {', '.join(FORMATS)}")
