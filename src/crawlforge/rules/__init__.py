"""SEO rule catalog — auto-discovery via @register_rule decorator."""

from crawlforge.rules._registry import REGISTRY, Rule, register_rule

__all__ = ["REGISTRY", "Rule", "register_rule"]
