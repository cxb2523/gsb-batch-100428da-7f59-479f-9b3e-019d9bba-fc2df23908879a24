"""Write the synthetic Google Maps answers used by the tests.

The answers copy the shape of real ones (array positions documented in
``gmaps_extractor/parsers``) but every value is made up: example domains
(``.test``), fictional 555 phone numbers and invented place ids.

Run ``python tests/fixtures/build_fixtures.py`` to rewrite the files.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFIX = ")]}'\n"


def business(name, address, place_id, hex_id, ftid, rating, reviews, lat, lng, phone, website, categories):
    biz = [None] * 180
    biz[4] = [None, None, None, None, None, None, None, rating, reviews]
    biz[7] = [website] if website else None
    biz[9] = [None, None, lat, lng]
    biz[10] = hex_id
    biz[11] = name
    biz[13] = categories
    biz[18] = address
    biz[78] = place_id
    biz[89] = ftid
    biz[178] = [[phone, 1]] if phone else None
    return biz


PLACES = [
    business(
        "Example Coffee Roasters",
        "100 Example St, Austin, TX 78701",
        "ChIJexample0000000000001",
        "0x8644b5a000000001:0x1111111111111111",
        "/g/11example001",
        4.6,
        812,
        30.2672,
        -97.7431,
        "(512) 555-0100",
        "/url?q=https://example-coffee.test/&opi=1&sa=U",
        ["Coffee shop", "Cafe"],
    ),
    business(
        "Sample Espresso Bar",
        "200 Sample Ave, Austin, TX 78702",
        "ChIJexample0000000000002",
        "0x8644b5a000000002:0x2222222222222222",
        None,
        4.2,
        57,
        30.2601,
        -97.7302,
        None,
        "https://sample-espresso.test/",
        ["Espresso bar"],
    ),
    business(
        "Café Ünïcode & Co!",
        None,
        "ChIJexample0000000000003",
        "0x8644b5a000000003:0x3333333333333333",
        "/g/11example003",
        None,
        None,
        30.2755,
        -97.7501,
        "+1 512-555-0199",
        None,
        [],
    ),
]


def search_answer():
    entries = [[None] * 14 + [biz] for biz in PLACES]
    # An encoded token in the name slot is not a place and must be skipped.
    entries.append([None] * 14 + [[None] * 11 + ["QUJDREVGR0hJSktMTU5PUA=="]])
    data = [None] * 70
    data[0] = ["coffee shop", entries]
    # The same first place again in the second list: must be de-duplicated.
    data[64] = [[None, PLACES[0]]]
    return data


DAY_ROWS = [
    ["Sunday", 7, [2026, 11, 15], [["Closed"]], 0, 2],
    ["Monday", 1, [2026, 11, 16], [["7 AM–6 PM", [[7], [18]]]], 0, 1],
    ["Tuesday", 2, [2026, 11, 17], [["7 AM–6 PM", [[7], [18]]]], 0, 1],
    ["Wednesday", 3, [2026, 11, 18], [["7 AM–6 PM", [[7], [18]]]], 0, 1],
    ["Thursday", 4, [2026, 11, 19], [["7 AM–6 PM", [[7], [18]]]], 0, 1],
    ["Friday", 5, [2026, 11, 20], [["11 AM–2 PM", [[11], [14]]], ["5–10 PM", [[17], [22]]]], 0, 1],
    ["Saturday", 6, [2026, 11, 21], [["Open 24 hours"]], 0, 1],
]

# Other data in the place answer that the library must never output.
UNPARSED = ["UNPARSED TEXT MUST NOT BE OUTPUT", ["Synthetic Name", "synthetic@example.test"]]


def place_answer(hours_rows):
    place = [None] * 210
    place[11] = "Example Coffee Roasters"
    place[120] = UNPARSED
    if hours_rows is not None:
        place[203] = [hours_rows]
    data = [None] * 8
    data[6] = place
    return data


def place_answer_legacy():
    place = [None] * 60
    place[11] = "Sample Espresso Bar"
    week = [["Closed"], ["9 AM–5 PM"], ["9 AM–5 PM"], ["9 AM–5 PM"], ["9 AM–5 PM"],
            ["9 AM–3 PM"], [[]]]
    place[34] = [None, week]
    data = [None] * 8
    data[6] = place
    return data


def place_answer_short():
    # A shortened place array: fields from the pivot on live in one object keyed
    # by field number (index + 1), so the hours are at tail["204"][0].
    place = [None] * 172
    place[11] = "Example Plumbing Service"
    place.append({"179": [["(512) 555-0142"]], "204": [DAY_ROWS[:3]]})
    data = [None] * 8
    data[6] = place
    return data


def write(name, data):
    (HERE / name).write_text(PREFIX + json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main():
    write("search_page.txt", search_answer())
    write("search_empty.txt", [["coffee shop", []], None, None, [[[0, 0, 0]], None, [1024, 768], 13.1]] + [None] * 20)
    write("place_hours.txt", place_answer(DAY_ROWS))
    write("place_no_hours.txt", place_answer(None))
    write("place_hours_legacy.txt", place_answer_legacy())
    write("place_hours_short.txt", place_answer_short())


if __name__ == "__main__":
    main()
