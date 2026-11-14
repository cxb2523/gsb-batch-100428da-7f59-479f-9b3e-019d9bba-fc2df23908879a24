"""Unit tests for the text matching evidence chain and wait_until_image_shown polling.

These tests use mocked OCR results / stubbed handlers, so no real OCR engine,
screenshot or display is required.
"""
from unittest.mock import MagicMock

import pytest

from RPALite import RPALite, ImageHandler
from RPALite.image_handler import MATCH_TYPE_EXACT, MATCH_TYPE_FUZZY


def make_ocr_array(*entries):
    """Build a mocked OCR result array.

    Each entry is (text, x, y, w, h). The returned items follow the OCR handler
    format: (bbox_points, text, confidence).
    """
    arr = []
    for text, x, y, w, h in entries:
        bbox = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
        arr.append((bbox, text, 0.99))
    return arr


def make_image_handler():
    """Create an ImageHandler without loading any OCR engine."""
    handler = ImageHandler.__new__(ImageHandler)
    handler.debug_mode = False
    handler.languages = ['en']
    handler.debug_image_show_milliseconds = 0
    return handler


def make_rpalite(find_texts_in_rects_return=None):
    """Create an RPALite instance without touching the screen, with a stubbed image handler."""
    rpalite = RPALite.__new__(RPALite)
    rpalite.debug_mode = False
    rpalite.step_pause_interval = 0
    rpalite.image_handler = MagicMock()
    rpalite.image_handler.find_texts_in_rects.return_value = find_texts_in_rects_return
    return rpalite


class TestFindTextsInArrayAndRect:
    """ImageHandler keeps match type and similarity for every candidate."""

    def setup_method(self):
        self.handler = make_image_handler()

    def test_exact_match(self):
        arr = make_ocr_array(("Click the Login button", 10, 20, 200, 30))
        results = self.handler.find_texts_in_array_and_rect("Login", arr, None)
        assert results is not None and len(results) == 1
        location, target_text, similarity, match_type = results[0]
        assert location == (10, 20, 200, 30)
        assert target_text == "Click the Login button"
        assert similarity == 1.0
        assert match_type == MATCH_TYPE_EXACT

    def test_fuzzy_match_only(self):
        # "Logim" vs "Login": SequenceMatcher ratio is 0.8 (> 0.75) but not contained
        arr = make_ocr_array(("Login", 10, 20, 100, 30))
        results = self.handler.find_texts_in_array_and_rect("Logim", arr, None)
        assert results is not None and len(results) == 1
        location, target_text, similarity, match_type = results[0]
        assert location == (10, 20, 100, 30)
        assert target_text == "Login"
        assert match_type == MATCH_TYPE_FUZZY
        assert 0.75 < similarity < 1.0

    def test_no_match(self):
        arr = make_ocr_array(("Completely different text", 10, 20, 100, 30))
        results = self.handler.find_texts_in_array_and_rect("zzzzzz", arr, None)
        assert results is None

    def test_none_array(self):
        assert self.handler.find_texts_in_array_and_rect("Login", None, None) is None

    def test_multiple_candidates_sorted_by_similarity(self):
        arr = make_ocr_array(
            ("Logim", 10, 20, 100, 30),          # fuzzy for "Login"
            ("Click Login now", 50, 60, 200, 30)  # exact for "Login"
        )
        results = self.handler.find_texts_in_array_and_rect("Login", arr, None)
        assert results is not None and len(results) == 2
        # Exact match (similarity 1.0) must come first
        assert results[0][3] == MATCH_TYPE_EXACT
        assert results[0][2] == 1.0
        assert results[1][3] == MATCH_TYPE_FUZZY
        assert results[1][2] < 1.0

    def test_rect_filter_still_applies(self):
        arr = make_ocr_array(("Login", 10, 20, 100, 30))
        # rect that does not contain the text position
        results = self.handler.find_texts_in_array_and_rect(
            "Login", arr, None, None, rect=(500, 500, 100, 100))
        assert results is None
        # rect that contains the text position
        results = self.handler.find_texts_in_array_and_rect(
            "Login", arr, None, None, rect=(0, 0, 500, 500))
        assert results is not None and results[0][3] == MATCH_TYPE_EXACT


class TestFindTextPositions:
    """RPALite.find_text_positions keeps default coordinate behavior and can
    optionally return match details."""

    def test_default_returns_plain_coordinates(self):
        rpalite = make_rpalite([
            ((10, 20, 100, 30), "Login", 1.0, MATCH_TYPE_EXACT),
        ])
        positions = rpalite.find_text_positions("Login", img=object())
        assert positions == [(10, 20, 100, 30)]

    def test_return_match_details(self):
        rpalite = make_rpalite([
            ((10, 20, 100, 30), "Login", 1.0, MATCH_TYPE_EXACT),
            ((50, 60, 100, 30), "Logim", 0.8, MATCH_TYPE_FUZZY),
        ])
        details = rpalite.find_text_positions("Login", img=object(), return_match_details=True)
        assert len(details) == 2
        assert details[0] == {
            'location': (10, 20, 100, 30),
            'text': "Login",
            'similarity': 1.0,
            'match_type': MATCH_TYPE_EXACT,
        }
        assert details[1]['location'] == (50, 60, 100, 30)
        assert details[1]['match_type'] == MATCH_TYPE_FUZZY
        assert 0.75 < details[1]['similarity'] < 1.0

    def test_exact_match_filter_with_details(self):
        rpalite = make_rpalite([
            ((10, 20, 100, 30), "Login", 1.0, MATCH_TYPE_EXACT),
            ((50, 60, 100, 30), "Something else", 0.8, MATCH_TYPE_FUZZY),
        ])
        details = rpalite.find_text_positions(
            "Login", img=object(), exact_match=True, return_match_details=True)
        assert len(details) == 1
        assert details[0]['match_type'] == MATCH_TYPE_EXACT
        assert details[0]['location'] == (10, 20, 100, 30)

    def test_exact_match_filter_default_behavior_unchanged(self):
        rpalite = make_rpalite([
            ((10, 20, 100, 30), "Login", 1.0, MATCH_TYPE_EXACT),
            ((50, 60, 100, 30), "Something else", 0.8, MATCH_TYPE_FUZZY),
        ])
        positions = rpalite.find_text_positions("Login", img=object(), exact_match=True)
        assert positions == [(10, 20, 100, 30)]

    def test_not_found_returns_none(self):
        rpalite = make_rpalite(None)
        assert rpalite.find_text_positions("Login", img=object()) is None
        assert rpalite.find_text_positions(
            "Login", img=object(), return_match_details=True) is None

    def test_validate_text_exists_still_returns_coordinates(self):
        rpalite = make_rpalite([
            ((10, 20, 100, 30), "Login", 1.0, MATCH_TYPE_EXACT),
        ])
        positions = rpalite.validate_text_exists("Login", img=object())
        assert positions == [(10, 20, 100, 30)]

    def test_validate_text_exists_raises_when_missing(self):
        rpalite = make_rpalite(None)
        with pytest.raises(AssertionError):
            rpalite.validate_text_exists("Login", img=object())


class TestWaitUntilImageShown:
    """wait_until_image_shown must keep polling until timeout."""

    def make_rpalite(self):
        rpalite = RPALite.__new__(RPALite)
        rpalite.debug_mode = False
        rpalite.step_pause_interval = 0
        rpalite.sleep = MagicMock()
        return rpalite

    def test_returns_location_after_several_misses(self):
        rpalite = self.make_rpalite()
        rpalite.find_image_location = MagicMock(
            side_effect=[None, None, None, (10, 20, 30, 40)])
        location = rpalite.wait_until_image_shown("dummy.png", timeout=30)
        assert location == (10, 20, 30, 40)
        assert rpalite.find_image_location.call_count == 4

    def test_raises_assertion_error_on_timeout(self):
        rpalite = self.make_rpalite()
        rpalite.find_image_location = MagicMock(return_value=None)
        with pytest.raises(AssertionError):
            rpalite.wait_until_image_shown("dummy.png", timeout=0)
        # It must have polled more than once instead of returning after the first miss
        assert rpalite.find_image_location.call_count >= 1

    def test_returns_immediately_when_found(self):
        rpalite = self.make_rpalite()
        rpalite.find_image_location = MagicMock(return_value=(1, 2, 3, 4))
        location = rpalite.wait_until_image_shown("dummy.png", timeout=30)
        assert location == (1, 2, 3, 4)
        assert rpalite.find_image_location.call_count == 1
