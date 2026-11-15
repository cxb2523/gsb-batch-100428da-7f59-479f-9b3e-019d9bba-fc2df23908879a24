#!/usr/bin/env python3
"""Tests for gen_nextractdata_with_summary (Extract Table Data + summary Assign).

Covers the three extraction shapes an operator must distinguish without
reading XAML by hand:
  - paginated   (next_link_selector present, unlimited results)
  - single-page (no next_link_selector — summary must say so explicitly)
  - limited     (MaximumResults > 0 — summary must carry the actual cap)

Also covers: summary/result variable references, ValueError boundaries,
deterministic regeneration, and XML well-formedness of the emitted fragment.

Usage:
    python3 scripts/test_nextractdata_summary.py
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from generate_activities import gen_nextractdata_with_summary  # noqa: E402

UIX = "http://schemas.uipath.com/workflow/activities/uix"

# --- Self-made valid sample inputs (mirror Studio-exported structure) --------

TABLE_SELECTOR = "<webctrl tag='table' class='items' />"
NEXT_LINK_SELECTOR = "<webctrl aaname='next' tag='A' />"
SCOPE_ID = "c679a542-b705-43e4-9aa2-afa22899ae4a"

EXTRACT_DATA_SETTINGS = (
    "<Table xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance' "
    "xmlns:xsd='http://www.w3.org/2001/XMLSchema' Type='Structured' "
    "AddCvHeader='true' IsScrollEnabled='false'>"
    "<Column xsi:type='DataColumn' ReferenceName='Column0' Name='Title'>"
    "<IsValidName>true</IsValidName><ValidationErrorMessage />"
    "<IsExtra>false</IsExtra><CanExtractSimilar>true</CanExtractSimilar>"
    "<Format xsi:type='TextColumnFormat' /></Column>"
    "<Column xsi:type='DataColumn' ReferenceName='Column1' Name='Price'>"
    "<IsValidName>true</IsValidName><ValidationErrorMessage />"
    "<IsExtra>false</IsExtra><CanExtractSimilar>false</CanExtractSimilar>"
    "<Format xsi:type='TextColumnFormat' /></Column>"
    "<Column xsi:type='DataNextLink' /></Table>"
)

EXTRACT_METADATA = (
    "<extract><row exact='1'><webctrl tag='li' />"
    "<webctrl tag='article' idx='1' /></row>"
    "<column exact='1' name='Column0' attr='fulltext'>"
    "<webctrl tag='li' /><webctrl tag='article' idx='1' />"
    "<webctrl tag='h3' idx='1' /></column>"
    "<column exact='1' name='Column1' attr='fulltext'>"
    "<webctrl tag='li' /><webctrl tag='article' idx='1' />"
    "<webctrl tag='div' idx='2' /><webctrl tag='p' idx='1' /></column>"
    "</extract>"
)


def _gen(**overrides):
    """Generate with shared defaults; overrides select the scenario."""
    args = dict(
        display_name="Extract Table Data",
        selector=TABLE_SELECTOR,
        output_variable="dt_Extracted",
        summary_variable="str_ExtractSummary",
        id_ref="1",
        scope_id=SCOPE_ID,
        extract_data_settings=EXTRACT_DATA_SETTINGS,
        extract_metadata=EXTRACT_METADATA,
    )
    args.update(overrides)
    return gen_nextractdata_with_summary(**args)


def _parse_fragment(xaml_fragment):
    """Wrap the two-activity fragment in a namespace-complete root and parse."""
    wrapped = (
        f'<Activity xmlns="{UIX}" '
        'xmlns:uix="http://schemas.uipath.com/workflow/activities/uix" '
        'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" '
        'xmlns:sap="http://schemas.microsoft.com/netfx/2009/xaml/activities/presentation" '
        'xmlns:sap2010="http://schemas.microsoft.com/netfx/2010/xaml/activities/presentation" '
        'xmlns:sd2="clr-namespace:System.Data;assembly=System.Data.Common">'
        f"{xaml_fragment}</Activity>"
    )
    return ET.fromstring(wrapped)


def _extract_element(root):
    return root.find(f"{{{UIX}}}NExtractDataGeneric")


# --- Scenario tests -----------------------------------------------------------

def test_paginated_unlimited():
    xaml = _gen(next_link_selector=NEXT_LINK_SELECTOR, max_results=0)
    assert "<uix:NExtractDataGeneric.NextLink>" in xaml, "NextLink block missing"
    assert 'MaximumResults="0"' in xaml
    assert 'LimitExtractionTo="None"' in xaml
    # Summary: pagination on, 0 marked as unlimited, 2 declared columns
    assert "Pagination=Enabled" in xaml
    assert "MaximumResults=0 (unlimited)" in xaml
    assert "DeclaredColumns=2" in xaml
    assert "single-page" not in xaml


def test_single_page():
    xaml = _gen()  # no next_link_selector
    assert "<uix:NExtractDataGeneric.NextLink>" not in xaml, \
        "single-page extraction must not emit a NextLink child element"
    assert "Pagination=Disabled (single-page)" in xaml
    assert "MaximumResults=0 (unlimited)" in xaml
    assert "DeclaredColumns=2" in xaml


def test_limited_results():
    xaml = _gen(next_link_selector=NEXT_LINK_SELECTOR, max_results=100,
                limit_extraction_to="CurrentPage")
    assert 'MaximumResults="100"' in xaml
    assert 'LimitExtractionTo="CurrentPage"' in xaml
    assert "MaximumResults=100;" in xaml
    assert "unlimited" not in xaml


# --- Variable reference tests -------------------------------------------------

def test_output_and_summary_variables_referenced():
    xaml = _gen(next_link_selector=NEXT_LINK_SELECTOR)
    # Extraction result bound via ExtractedData
    assert 'ExtractedData="[dt_Extracted]"' in xaml
    # Summary written via Assign.To
    assert '<OutArgument x:TypeArguments="x:String">[str_ExtractSummary]</OutArgument>' in xaml
    # IdRef uses generic backtick notation
    assert 'sap2010:WorkflowViewState.IdRef="NExtractDataGeneric`1_1"' in xaml
    assert 'ScopeIdentifier="' + SCOPE_ID + '"' in xaml


# --- Boundary / validation tests ----------------------------------------------

def test_missing_extract_metadata_raises():
    try:
        _gen(extract_metadata="")
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing extract_metadata")


def test_missing_extract_data_settings_raises():
    try:
        _gen(extract_data_settings="")
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing extract_data_settings")


def test_invalid_limit_extraction_to_raises():
    for bad in ("All", "FirstPage", "none", ""):
        try:
            _gen(limit_extraction_to=bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for LimitExtractionTo={bad!r}")


# --- Structural tests -----------------------------------------------------------

def test_output_is_well_formed_xml_and_settings_roundtrip():
    xaml = _gen(next_link_selector=NEXT_LINK_SELECTOR, max_results=50)
    root = _parse_fragment(xaml)
    extract = _extract_element(root)
    assert extract is not None, "NExtractDataGeneric element not found"
    # Entity-escaped attributes must decode back to the exact input XML
    assert extract.get("ExtractDataSettings") == EXTRACT_DATA_SETTINGS
    assert extract.get("ExtractMetadata") == EXTRACT_METADATA
    # NextLink carries the next-page selector; Target carries the table selector
    next_link = extract.find(f"{{{UIX}}}NExtractDataGeneric.NextLink")
    assert next_link is not None
    target = extract.find(f"{{{UIX}}}NExtractDataGeneric.Target")
    assert target is not None


def test_generation_is_deterministic():
    kwargs = dict(next_link_selector=NEXT_LINK_SELECTOR, max_results=25)
    first = _gen(**kwargs)
    second = _gen(**kwargs)
    assert first == second, "same parameters must produce identical XAML"


ALL_TESTS = [
    test_paginated_unlimited,
    test_single_page,
    test_limited_results,
    test_output_and_summary_variables_referenced,
    test_missing_extract_metadata_raises,
    test_missing_extract_data_settings_raises,
    test_invalid_limit_extraction_to_raises,
    test_output_is_well_formed_xml_and_settings_roundtrip,
    test_generation_is_deterministic,
]


def main():
    passed = 0
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as exc:
            print(f"  FAIL  {fn.__name__}: {exc}")
        else:
            passed += 1
            print(f"  PASS  {fn.__name__}")
    print(f"{passed}/{len(ALL_TESTS)} passed")
    sys.exit(0 if passed == len(ALL_TESTS) else 1)


if __name__ == "__main__":
    main()
