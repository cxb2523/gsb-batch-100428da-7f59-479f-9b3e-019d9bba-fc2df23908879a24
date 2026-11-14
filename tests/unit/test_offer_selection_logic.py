"""Unit tests for the pure offer selection implementation.

These tests never touch the network, a browser or a database.
"""

import pytest

from src.providers.base_product import BaseProduct
from src.providers.offer_selection import (
    DEFAULT_OFFER_STRATEGY,
    OfferSelection,
    StrategyDecision,
    extract_offer_price,
    offer_is_explicitly_in_stock,
    resolve_offer_strategy,
    select_offer,
)
from src.scrapers.async_base_provider import AsyncBaseProvider

REFURBISHED_OFFERS = [
    {"price": "509.99", "priceCurrency": "EUR", "availability": "InStock", "itemCondition": "New"},
    {
        "price": "495.99",
        "priceCurrency": "EUR",
        "availability": "OutOfStock",
        "itemCondition": "Refurbished",
    },
    {
        "price": "491.99",
        "priceCurrency": "EUR",
        "availability": "https://schema.org/InStock",
        "itemCondition": "Refurbished",
    },
]


class TestSelectOfferHappyPath:
    """Normal selection across the three supported strategies."""

    def test_first_selects_first_offer_and_explains(self):
        selection = select_offer(REFURBISHED_OFFERS, "first")

        assert isinstance(selection, OfferSelection)
        assert selection.offer == REFURBISHED_OFFERS[0]
        assert selection.selected_index == 0
        assert selection.strategy == "first"
        assert selection.fallback is False
        assert "first offer" in selection.reason

    def test_cheapest_selects_lowest_price(self):
        selection = select_offer(REFURBISHED_OFFERS, "cheapest")

        assert selection.offer is REFURBISHED_OFFERS[2]
        assert selection.price == 491.99
        assert selection.selected_index == 2
        assert selection.strategy == "cheapest"
        assert selection.fallback is False
        assert selection.reason

    def test_cheapest_available_skips_out_of_stock(self):
        offers = [
            {"price": 509.99, "availability": "InStock"},
            {"price": 400.00, "availability": "OutOfStock"},
            {"price": 491.99, "availability": "InStock"},
        ]

        selection = select_offer(offers, "cheapest_available")

        assert selection.offer is offers[2]
        assert selection.price == 491.99
        assert selection.strategy == "cheapest_available"
        assert selection.fallback is False

    def test_cheapest_available_requires_explicit_stock(self):
        """Missing availability does not count as in stock."""
        offers = [
            {"price": 100.00},
            {"price": 200.00, "availability": "InStock"},
        ]

        selection = select_offer(offers, "cheapest_available")

        assert selection.offer is offers[1]
        assert selection.price == 200.00

    def test_default_strategy_is_first(self):
        selection = select_offer(REFURBISHED_OFFERS)

        assert selection.requested_strategy == DEFAULT_OFFER_STRATEGY
        assert selection.offer == REFURBISHED_OFFERS[0]

    def test_selection_is_json_friendly(self):
        selection = select_offer(REFURBISHED_OFFERS, "cheapest")
        data = selection.to_dict()

        assert data["strategy"] == "cheapest"
        assert data["price"] == 491.99
        assert data["selected_index"] == 2
        assert isinstance(data["reason"], str) and data["reason"]
        assert data["fallback"] is False
        assert isinstance(data["skipped"], list)


class TestNoValidPrice:
    """Offers without a usable price are skipped, not fatal."""

    def test_cheapest_skips_unparseable_prices(self):
        offers = [
            {"price": None, "availability": "InStock"},
            {"price": "not-a-price", "availability": "InStock"},
            {"price": "491,99", "priceCurrency": "EUR", "availability": "InStock"},
        ]

        selection = select_offer(offers, "cheapest")

        assert selection.offer is offers[2]
        assert selection.price == 491.99
        assert selection.fallback is False
        assert [entry["index"] for entry in selection.skipped] == [0, 1]
        assert all(entry["reason"] for entry in selection.skipped)
        assert "Skipped 2" in selection.reason

    def test_all_prices_invalid_falls_back_to_first(self):
        offers = [
            {"price": "garbage", "availability": "InStock"},
            {"price": None, "availability": "InStock"},
        ]

        selection = select_offer(offers, "cheapest")

        assert selection.offer is offers[0]
        assert selection.fallback is True
        assert selection.strategy == DEFAULT_OFFER_STRATEGY
        assert selection.requested_strategy == "cheapest"
        assert len(selection.skipped) == 2
        assert "No offer has a valid price" in selection.reason

    def test_offer_prices_accept_common_formats(self):
        assert extract_offer_price({"price": "€1.234,56"}) == (1234.56, None)
        assert extract_offer_price({"price": 491.99}) == (491.99, None)
        assert extract_offer_price({"lowPrice": "19.99"}) == (19.99, None)
        assert extract_offer_price({"priceSpecification": {"price": "9,50"}}) == (9.5, None)

    def test_extract_offer_price_reports_reason(self):
        price, reason = extract_offer_price({"price": "abc"})

        assert price is None
        assert reason is not None and "abc" in reason

        price, reason = extract_offer_price({"availability": "InStock"})
        assert price is None
        assert reason == "no price field present"

        price, reason = extract_offer_price("not-a-dict")
        assert price is None
        assert reason is not None

    def test_non_mapping_offer_does_not_break_selection(self):
        selection = select_offer(["oops", {"price": 5.0, "availability": "InStock"}], "cheapest")

        assert selection.offer == {"price": 5.0, "availability": "InStock"}
        assert selection.skipped[0]["index"] == 0


class TestNoAvailableOffers:
    """cheapest_available degrades to cheapest when nothing is in stock."""

    def test_nothing_in_stock_falls_back_to_cheapest(self):
        offers = [
            {"price": 509.99, "availability": "OutOfStock"},
            {"price": 495.99, "availability": "OutOfStock"},
            {"price": 491.99, "availability": "https://schema.org/Discontinued"},
        ]

        selection = select_offer(offers, "cheapest_available")

        assert selection.offer is offers[2]
        assert selection.price == 491.99
        assert selection.fallback is True
        assert selection.strategy == "cheapest"
        assert selection.requested_strategy == "cheapest_available"
        assert "No in-stock offers found" in selection.reason
        assert "fell back to 'cheapest'" in selection.reason

    def test_unavailable_is_not_treated_as_available(self):
        assert offer_is_explicitly_in_stock({"availability": "Unavailable"}) is False
        assert offer_is_explicitly_in_stock({"availability": "OutOfStock"}) is False
        assert offer_is_explicitly_in_stock({"availability": "PreOrder"}) is False
        assert offer_is_explicitly_in_stock({}) is False
        assert offer_is_explicitly_in_stock({"availability": "InStock"}) is True
        assert offer_is_explicitly_in_stock({"availability": "https://schema.org/InStock"}) is True


class TestUnknownStrategy:
    """Unknown strategies fall back to first with an explanatory reason."""

    def test_unknown_strategy_uses_first(self):
        selection = select_offer(REFURBISHED_OFFERS, "most_expensive")

        assert selection.offer == REFURBISHED_OFFERS[0]
        assert selection.fallback is True
        assert selection.strategy == DEFAULT_OFFER_STRATEGY
        assert selection.requested_strategy == "most_expensive"
        assert "Unknown offer selection strategy" in selection.reason

    def test_blank_strategy_uses_default_without_warning(self):
        selection = select_offer(REFURBISHED_OFFERS, "   ")

        assert selection.requested_strategy == DEFAULT_OFFER_STRATEGY
        assert selection.offer == REFURBISHED_OFFERS[0]
        assert selection.fallback is False

    def test_empty_offer_list_is_safe(self):
        selection = select_offer([], "cheapest")

        assert selection.offer is None
        assert selection.fallback is False
        assert "No offers" in selection.reason


class TestLockedStrategyPriority:
    """Locked strategy wins only for tracked products with existing snapshots."""

    def test_locked_strategy_overrides_config(self):
        decision = resolve_offer_strategy(
            config_strategy="first",
            locked_strategy="cheapest",
            has_snapshots=True,
        )

        assert isinstance(decision, StrategyDecision)
        assert decision.strategy == "cheapest"
        assert decision.source == "locked"
        assert decision.config_strategy == "first"
        assert decision.locked_strategy == "cheapest"
        assert decision.warning is not None
        assert "locked" in decision.warning

    def test_locked_equal_to_config_has_no_warning(self):
        decision = resolve_offer_strategy(
            config_strategy="cheapest_available",
            locked_strategy="cheapest_available",
            has_snapshots=True,
        )

        assert decision.strategy == "cheapest_available"
        assert decision.source == "locked"
        assert decision.warning is None

    def test_lock_ignored_without_snapshots(self):
        decision = resolve_offer_strategy(
            config_strategy="cheapest",
            locked_strategy="first",
            has_snapshots=False,
        )

        assert decision.strategy == "cheapest"
        assert decision.source == "config"
        assert decision.warning is None

    def test_config_used_when_never_tracked(self):
        decision = resolve_offer_strategy(
            config_strategy="cheapest_available",
            locked_strategy=None,
            has_snapshots=False,
        )

        assert decision.strategy == "cheapest_available"
        assert decision.source == "config"

    def test_locked_unknown_value_is_still_honoured_then_falls_back(self):
        decision = resolve_offer_strategy(
            config_strategy="first",
            locked_strategy="legacy_strategy",
            has_snapshots=True,
        )
        selection = select_offer(REFURBISHED_OFFERS, decision.strategy)

        assert decision.strategy == "legacy_strategy"
        assert decision.source == "locked"
        assert selection.offer == REFURBISHED_OFFERS[0]
        assert selection.fallback is True
        assert "Unknown offer selection strategy" in selection.reason

    def test_provider_reads_locked_strategy_from_database_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The provider decorates DB values without needing a real database."""
        from src.providers.configurable_provider import ConfigurableProvider

        monkeypatch.setattr(
            ConfigurableProvider,
            "_get_locked_strategy",
            staticmethod(lambda url: ("cheapest", True)),
        )

        # Instance state is irrelevant here: only the DB lookup is stubbed.
        provider = object.__new__(ConfigurableProvider)
        decision = ConfigurableProvider._resolve_locked_strategy(
            provider, "https://example.com/product", {}
        )

        assert decision.strategy == "cheapest"
        assert decision.source == "locked"
        assert decision.config_strategy == DEFAULT_OFFER_STRATEGY
        assert decision.warning is not None


class TestNormalizeJsonLdProduct:
    """JSON-LD normalization must use the strategy and return the selection."""

    def test_multi_offer_list_uses_strategy_not_first(self):
        product = AsyncBaseProvider.normalize_json_ld_product(
            {"name": "Console", "offers": REFURBISHED_OFFERS}, strategy="cheapest"
        )

        assert product["current_price"] == "491.99"
        assert product["currency"] == "EUR"
        assert product["offer_selection_strategy"] == "cheapest"
        assert product["offer_selection"]["selected_index"] == 2
        assert product["offer_selection"]["reason"]
        assert product["offer_selection"]["fallback"] is False

    def test_default_strategy_keeps_first_offer(self):
        product = AsyncBaseProvider.normalize_json_ld_product(
            {"name": "Console", "offers": REFURBISHED_OFFERS}
        )

        assert product["current_price"] == "509.99"
        assert product["offer_selection_strategy"] == "first"
        assert product["offer_selection"]["selected_index"] == 0

    def test_malformed_offer_does_not_abort_normalization(self):
        offers = [{"price": "n/a"}, {"price": "42.50", "availability": "InStock"}]

        product = AsyncBaseProvider.normalize_json_ld_product(
            {"name": "Console", "offers": offers}, strategy="cheapest"
        )

        assert product["current_price"] == "42.50"
        assert product["offer_selection"]["skipped"]

    def test_single_offer_object_still_normalizes(self):
        product = AsyncBaseProvider.normalize_json_ld_product(
            {
                "name": "Single",
                "offers": {
                    "price": "19.99",
                    "highPrice": "24.99",
                    "priceCurrency": "EUR",
                    "availability": "InStock",
                },
            },
            strategy="cheapest",
        )

        assert product["current_price"] == "19.99"
        assert product["original_price"] == "24.99"
        assert product["has_promotion"] is True
        assert product["offer_selection"]["reason"]


class TestBaseProductExposesSelection:
    def test_offer_selection_is_serialized(self):
        product = BaseProduct(
            name="Console",
            url="https://example.com/console",
            offer_selection_strategy="cheapest",
            offer_selection={"strategy": "cheapest", "reason": "lowest price", "price": 1.0},
        )

        data = product.to_dict()

        assert data["offer_selection_strategy"] == "cheapest"
        assert data["offer_selection"] == {
            "strategy": "cheapest",
            "reason": "lowest price",
            "price": 1.0,
        }
