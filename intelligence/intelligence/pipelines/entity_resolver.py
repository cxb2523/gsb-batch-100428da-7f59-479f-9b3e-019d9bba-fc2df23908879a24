import re
from collections import defaultdict
from difflib import SequenceMatcher

from intelligence.models import ExtractedEntity


class EntityResolver:
    """Deduplicates and merges similar entities into canonical forms."""

    SIMILARITY_THRESHOLD = 0.85

    # Common suffixes to normalize before comparison
    CORP_SUFFIXES = re.compile(
        r"\s*,?\s*(Inc\.?|Corp\.?|LLC|Ltd\.?|Co\.?|Corporation|Company|Limited|PLC|AG|SA|GmbH)\s*$",
        re.IGNORECASE,
    )

    def resolve(self, entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        """Merge similar entities, keeping the most common surface form."""
        if not entities:
            return []

        # Group by entity type first — only merge within same type
        by_type: dict[str, list[ExtractedEntity]] = defaultdict(list)
        for e in entities:
            by_type[e.entity_type].append(e)

        resolved = []
        for entity_type, type_entities in by_type.items():
            resolved.extend(self._resolve_group(type_entities))

        return resolved

    def _resolve_group(self, entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        """Merge similar entities within a single type group."""
        if len(entities) <= 1:
            return entities

        # Build clusters of similar entities
        clusters: list[list[ExtractedEntity]] = []
        used = set()

        for i, e1 in enumerate(entities):
            if i in used:
                continue
            cluster = [e1]
            used.add(i)

            norm1 = self._normalize(e1.entity_text)
            for j, e2 in enumerate(entities):
                if j in used:
                    continue
                norm2 = self._normalize(e2.entity_text)
                if self._are_similar(norm1, norm2):
                    cluster.append(e2)
                    used.add(j)

            clusters.append(cluster)

        # For each cluster, pick the canonical form (most common, or shortest clean form)
        result = []
        for cluster in clusters:
            canonical = self._pick_canonical(cluster)
            result.append(canonical)

        return result

    def _normalize(self, text: str) -> str:
        """Normalize entity text for comparison."""
        text = text.lower().strip()
        text = self.CORP_SUFFIXES.sub("", text)
        text = re.sub(r"\s+", " ", text)
        return text

    def _are_similar(self, a: str, b: str) -> bool:
        """Check if two normalized entity strings are similar enough to merge."""
        if a == b:
            return True
        # One is a substring of the other
        if a in b or b in a:
            return True
        # Sequence matcher similarity
        ratio = SequenceMatcher(None, a, b).ratio()
        return ratio >= self.SIMILARITY_THRESHOLD

    def _pick_canonical(self, cluster: list[ExtractedEntity]) -> ExtractedEntity:
        """Pick the best representative from a cluster of similar entities."""
        if len(cluster) == 1:
            return cluster[0]

        # Count occurrences of each surface form
        form_counts: dict[str, int] = defaultdict(int)
        for e in cluster:
            form_counts[e.entity_text] += 1

        # Prefer: most common form, then highest confidence, then shortest (to break remaining ties)
        best = max(
            cluster,
            key=lambda e: (
                form_counts[e.entity_text],
                e.confidence,
                -len(e.entity_text),
            ),
        )

        # Use the best entity but boost confidence slightly for having multiple mentions
        mention_boost = min(0.05 * (len(cluster) - 1), 0.1)
        boosted_confidence = min(best.confidence + mention_boost, 1.0)

        return ExtractedEntity(
            entity_type=best.entity_type,
            entity_text=best.entity_text,
            start_char=best.start_char,
            end_char=best.end_char,
            confidence=boosted_confidence,
            pipeline_version=best.pipeline_version,
        )
