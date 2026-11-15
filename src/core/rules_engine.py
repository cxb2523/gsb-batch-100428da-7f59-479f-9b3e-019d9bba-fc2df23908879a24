"""
Automation rules engine — evaluates ad set performance and assigns flags.

Three business type modes are supported: ecommerce (ROAS/CTR based),
lead generation (CPL based), and awareness (frequency based).
"""

from collections import defaultdict


def group_by_adset(data: list[dict]) -> list[dict]:
    """
    Group enriched ad rows by Ad Set ID and aggregate key metrics.

    Each ad set row contains summed spend, impressions, clicks, purchases,
    leads, and other metrics, plus a list of the individual ad rows.

    Args:
        data: List of enriched ad-level row dicts.

    Returns:
        List of aggregated ad set dicts, one per Ad Set ID.
    """
    raise NotImplementedError("Proprietary implementation")


def evaluate_adset(adset_row: dict, rules: dict) -> str:
    """
    Evaluate a single ad set against ecommerce pause and scale rules.

    An ad set is labeled 'watch' if it has not yet met the minimum spend
    and minimum days threshold for evaluation. Otherwise it is evaluated
    against pause and scale conditions.

    Args:
        adset_row: Aggregated ad set dict from group_by_adset.
        rules: Automation rules dict from settings (pause_rule, scale_rule).

    Returns:
        One of: 'scale', 'pause', 'watch', 'ok'.

    Note:
        Core evaluation logic is proprietary and not included in this public version.
    """
    raise NotImplementedError("Proprietary implementation")


def evaluate_adset_by_type(
    adset_row: dict,
    rules: dict,
    business_type: str = "ecommerce",
    target_cpl: float | None = None,
) -> str:
    """
    Route evaluation to the correct logic based on business_type.

    Args:
        adset_row: Aggregated ad set dict.
        rules: Automation rules dict from settings.
        business_type: One of 'ecommerce', 'lead_gen', 'awareness'.
        target_cpl: Target cost-per-lead threshold (used in lead_gen mode).

    Returns:
        One of: 'scale', 'pause', 'watch', 'ok'.

    Note:
        Lead gen and awareness evaluation logic is proprietary and not included
        in this public version.
    """
    raise NotImplementedError("Proprietary implementation")


def evaluate_all_adsets(
    data: list[dict],
    rules: dict,
    business_type: str = "ecommerce",
    target_cpl: float | None = None,
) -> list[dict]:
    """
    Group data by ad set, evaluate each ad set, and attach a rule_flag.

    Args:
        data: List of enriched ad-level row dicts.
        rules: Automation rules dict from settings.
        business_type: One of 'ecommerce', 'lead_gen', 'awareness'.
        target_cpl: Target CPL threshold for lead gen mode.

    Returns:
        List of aggregated ad set dicts, each with a 'rule_flag' field added.
    """
    raise NotImplementedError("Proprietary implementation")


def get_flagged_summary(adset_rows: list[dict]) -> dict:
    """
    Count how many ad sets have each rule flag.

    Args:
        adset_rows: List of evaluated ad set dicts (with rule_flag field).

    Returns:
        Dict with keys: scale, pause, watch, ok — each mapped to an integer count.
    """
    raise NotImplementedError("Proprietary implementation")
