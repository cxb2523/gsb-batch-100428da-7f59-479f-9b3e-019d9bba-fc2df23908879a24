"""Pure offer selection logic for multi-offer JSON-LD products.

A product page can expose several offers (for example refurbished items in
different conditions). The strategy used to pick one of them is locked on the
first snapshot, so later refreshes must keep using the same offer.

This module is intentionally free of database, browser and network access so
the selection rules can be tested in isolation.  Database lookups for the
locked strategy happen in :class:`~src.providers.configurable_provider.ConfigurableProvider`;
the values are then passed to :func:`resolve_offer_strategy`.
"""

from dataclasses import dataclass, field
from math import isfinite
from typing import Any

DEFAULT_OFFER_STRATEGY = "first"
VALID_OFFER_STRATEGIES = ("first", "cheapest", "cheapest_available")

# Availability values that unambiguously mean "not purchasable".  Checked
# before the in-stock markers so "Unavailable" does not match "Available".
_OUT_OF_STOCK_MARKERS = (
    "outofstock",
    "out_of_stock",
    "out of stock",
    "soldout",
    "sold_out",
    "sold out",
    "discontinued",
    "preorder",
    "pre_order",
    "pre-order",
    "backorder",
    "back_order",
    "back-order",
    "unavailable",
)

# Availability values that explicitly mean "in stock".  A missing availability
# field is *not* treated as in stock for the cheapest_available strategy.
_IN_STOCK_MARKERS = (
    "instock",
    "in_stock",
    "in stock",
    "limitedavailability",
    "limited_availability",
    "limited availability",
    "onlineonly",
    "online_only",
    "online only",
    "instoreonly",
    "in_store_only",
    "in store only",
    "available",
)


@dataclass
class OfferSelection:
    """Result of selecting one offer from a list, including a readable reason.

    ``requested_strategy`` is the strategy that was asked for (config or locked
    value).  ``strategy`` is the rule that actually produced ``offer`` and
    differs from the request when ``fallback`` is True.  Callers that persist a
    locked strategy should use ``requested_strategy`` (or the resolved
    :class:`StrategyDecision`), not the degraded value.
    """

    offer: dict[str, Any] | None
    requested_strategy: str
    strategy: str
    reason: str
    price: float | None = None
    selected_index: int | None = None
    fallback: bool = False
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for callers/records."""
        return {
            "requested_strategy": self.requested_strategy,
            "strategy": self.strategy,
            "selected_index": self.selected_index,
            "offer": self.offer,
            "price": self.price,
            "reason": self.reason,
            "fallback": self.fallback,
            "skipped": list(self.skipped),
        }


@dataclass
class StrategyDecision:
    """Resolved strategy plus where it came from (locked DB value or config)."""

    strategy: str
    config_strategy: str
    source: str  # "locked" or "config"
    locked_strategy: str | None = None
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "config_strategy": self.config_strategy,
            "source": self.source,
            "locked_strategy": self.locked_strategy,
            "warning": self.warning,
        }


def _normalize_strategy(strategy: Any) -> str:
    if isinstance(strategy, str) and strategy.strip():
        return strategy.strip()
    return DEFAULT_OFFER_STRATEGY


def _parse_price(raw: Any) -> float | None:
    """Parse a single price value, returning None when it is not usable."""
    if isinstance(raw, bool):
        return None

    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if isfinite(value) and value >= 0 else None

    text = "".join(str(raw).split())
    if not text:
        return None

    for symbol in ("€", "$", "£", "EUR", "USD", "GBP"):
        text = text.replace(symbol, "")

    if not text:
        return None

    if "," in text and "." in text:
        # Last separator is the decimal separator, the other one groups digits.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        whole, _, fraction = text.partition(",")
        if "," not in fraction and len(fraction) <= 2:
            text = f"{whole}.{fraction}"
        else:
            text = text.replace(",", "")

    try:
        value = float(text)
    except (TypeError, ValueError):
        return None

    return value if isfinite(value) and value >= 0 else None


def extract_offer_price(offer: Any) -> tuple[float | None, str | None]:
    """Return ``(price, None)`` or ``(None, reason)`` for one offer.

    ``price`` is preferred, then ``lowPrice`` (AggregateOffer), then
    ``priceSpecification.price``.  A malformed value is reported instead of
    raising, so the caller can skip just that offer.
    """
    if not isinstance(offer, dict):
        return None, "offer is not a JSON object"

    candidates: list[tuple[str, Any]] = [
        ("price", offer.get("price")),
        ("lowPrice", offer.get("lowPrice")),
    ]

    price_spec = offer.get("priceSpecification")
    if isinstance(price_spec, dict):
        candidates.append(("priceSpecification.price", price_spec.get("price")))
    elif isinstance(price_spec, list):
        for spec_index, spec in enumerate(price_spec):
            if isinstance(spec, dict):
                candidates.append((f"priceSpecification[{spec_index}].price", spec.get("price")))

    invalid_reason: str | None = None
    for field_name, raw_value in candidates:
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            continue

        price = _parse_price(raw_value)
        if price is None:
            invalid_reason = f"{field_name}={raw_value!r} is not a valid price"
            continue

        return price, None

    return None, invalid_reason or "no price field present"


def offer_is_explicitly_in_stock(offer: Any) -> bool:
    """Return True only when the availability field explicitly says in stock."""
    if not isinstance(offer, dict):
        return False

    availability = offer.get("availability")
    if isinstance(availability, bool):
        return availability
    if not isinstance(availability, str):
        return False

    text = availability.strip().lower()
    if not text:
        return False

    if any(marker in text for marker in _OUT_OF_STOCK_MARKERS):
        return False
    return any(marker in text for marker in _IN_STOCK_MARKERS)


def _select_cheapest(
    offers: list[Any], requested_strategy: str, skipped: list[dict[str, Any]]
) -> OfferSelection:
    valid: list[tuple[int, dict[str, Any], float]] = []

    for index, offer in enumerate(offers):
        price, reason = extract_offer_price(offer)
        if price is None:
            skipped.append({"index": index, "reason": reason or "no valid price"})
            continue
        valid.append((index, offer, price))

    if not valid:
        reason = (
            f"No offer has a valid price; fell back to 'first' (offer 0 of "
            f"{len(offers)}). {len(skipped)} offer(s) were skipped."
        )
        return OfferSelection(
            offer=offers[0] if isinstance(offers[0], dict) else None,
            requested_strategy=requested_strategy,
            strategy=DEFAULT_OFFER_STRATEGY,
            reason=reason,
            selected_index=0,
            fallback=True,
            skipped=skipped,
        )

    index, offer, price = min(valid, key=lambda item: (item[2], item[0]))
    reason = (
        f"Selected cheapest offer at index {index} with price {price} "
        f"from {len(valid)} valid offer(s)."
    )
    if skipped:
        reason += f" Skipped {len(skipped)} offer(s) without a valid price."

    return OfferSelection(
        offer=offer,
        requested_strategy=requested_strategy,
        strategy="cheapest",
        reason=reason,
        price=price,
        selected_index=index,
        skipped=skipped,
    )


def select_offer(
    offers: list[dict[str, Any]], strategy: str = DEFAULT_OFFER_STRATEGY
) -> OfferSelection:
    """Select one offer according to ``strategy`` and explain the choice.

    Supported strategies:

    - ``first``: the first offer in the list.
    - ``cheapest``: lowest price; offers without a valid price are skipped.
    - ``cheapest_available``: lowest price among offers whose availability
      explicitly says in stock; falls back to ``cheapest`` when none are.

    Unknown strategies fall back to ``first`` and the reason says why.  This
    function never raises for malformed individual offers; each skipped offer
    is recorded with its reason.
    """
    requested_strategy = _normalize_strategy(strategy)
    offer_list = list(offers) if offers else []

    if not offer_list:
        return OfferSelection(
            offer=None,
            requested_strategy=requested_strategy,
            strategy=requested_strategy,
            reason="No offers available to select from.",
        )

    if requested_strategy not in VALID_OFFER_STRATEGIES:
        reason = (
            f"Unknown offer selection strategy '{requested_strategy}'; "
            f"fell back to 'first' (offer 0 of {len(offer_list)})."
        )
        return OfferSelection(
            offer=offer_list[0] if isinstance(offer_list[0], dict) else None,
            requested_strategy=requested_strategy,
            strategy=DEFAULT_OFFER_STRATEGY,
            reason=reason,
            selected_index=0,
            fallback=True,
        )

    if requested_strategy == "first":
        return OfferSelection(
            offer=offer_list[0] if isinstance(offer_list[0], dict) else None,
            requested_strategy=requested_strategy,
            strategy="first",
            reason=f"Selected first offer (index 0 of {len(offer_list)}).",
            selected_index=0,
        )

    if requested_strategy == "cheapest":
        return _select_cheapest(offer_list, requested_strategy, skipped=[])

    # cheapest_available
    in_stock: list[tuple[int, dict[str, Any], float]] = []
    skipped: list[dict[str, Any]] = []

    for index, offer in enumerate(offer_list):
        price, skip_reason = extract_offer_price(offer)
        if price is None:
            skipped.append({"index": index, "reason": skip_reason or "no valid price"})
            continue
        if not offer_is_explicitly_in_stock(offer):
            skipped.append(
                {
                    "index": index,
                    "reason": "availability does not explicitly indicate in stock",
                }
            )
            continue
        in_stock.append((index, offer, price))

    if not in_stock:
        fallback = _select_cheapest(offer_list, requested_strategy, [])
        fallback.fallback = True
        fallback.skipped = skipped
        fallback.reason = (
            f"No in-stock offers found ({len(offer_list)} offer(s) checked); "
            f"fell back to 'cheapest'. {fallback.reason}"
        )
        return fallback

    index, offer, price = min(in_stock, key=lambda item: (item[2], item[0]))
    reason = (
        f"Selected cheapest in-stock offer at index {index} with price {price} "
        f"from {len(in_stock)} in-stock offer(s)."
    )
    if skipped:
        reason += f" Skipped {len(skipped)} offer(s) with no valid price or no explicit stock."

    return OfferSelection(
        offer=offer,
        requested_strategy=requested_strategy,
        strategy="cheapest_available",
        reason=reason,
        price=price,
        selected_index=index,
        skipped=skipped,
    )


def resolve_offer_strategy(
    config_strategy: Any = None,
    locked_strategy: Any = None,
    has_snapshots: bool = False,
) -> StrategyDecision:
    """Resolve which strategy applies to a product.

    Mirrors the locking semantics used by the tracker:

    - Only a tracked product that already has at least one snapshot may use the
      strategy stored in the database.
    - Otherwise the configured strategy (default ``first``) is used.
    - When the locked value differs from the config, the locked value wins and
      ``warning`` carries the user-facing explanation.
    """
    config_value = _normalize_strategy(config_strategy)

    locked_value: str | None = None
    if isinstance(locked_strategy, str) and locked_strategy.strip():
        locked_value = locked_strategy.strip()

    if has_snapshots and locked_value:
        warning = None
        if locked_value != config_value:
            warning = (
                f"Using locked offer strategy '{locked_value}' for this product "
                f"(config has '{config_value}' but strategy is locked from the first snapshot)"
            )
        return StrategyDecision(
            strategy=locked_value,
            config_strategy=config_value,
            source="locked",
            locked_strategy=locked_value,
            warning=warning,
        )

    return StrategyDecision(
        strategy=config_value,
        config_strategy=config_value,
        source="config",
        locked_strategy=locked_value,
    )
