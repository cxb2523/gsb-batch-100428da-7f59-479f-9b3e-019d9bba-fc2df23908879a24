import re

import spacy
from bs4 import BeautifulSoup

from intelligence.config import Config
from intelligence.models import ExtractedEntity
from intelligence.pipelines.base import BasePipeline


class EntityPipeline(BasePipeline):
    """Extracts named entities from HTML using SpaCy NER."""

    SUPPORTED_TYPES = {"PERSON", "ORG", "GPE", "PRODUCT", "MONEY", "LOC", "FAC", "NORP", "EVENT"}

    # Known technology/product names that SpaCy often mistypes as PERSON
    PRODUCT_OVERRIDES = {
        "claude", "gpt", "gpt-4", "gpt-3", "chatgpt", "copilot", "gemini",
        "python", "javascript", "typescript", "rust", "golang", "java", "ruby",
        "react", "angular", "vue", "fastapi", "django", "flask", "redis",
        "pytorch", "tensorflow", "kubernetes", "docker", "linux", "macos",
        "windows", "android", "ios", "safari", "chrome", "firefox",
    }

    def __init__(self, model_name: str | None = None):
        self._nlp = spacy.load(model_name or Config.SPACY_MODEL)

    def extract(self, html: str, source_url: str) -> list[ExtractedEntity]:
        if not html or not html.strip():
            return []

        text = self._html_to_text(html)
        if not text.strip():
            return []

        doc = self._nlp(text)
        entities = []
        seen = set()

        for ent in doc.ents:
            if ent.label_ not in self.SUPPORTED_TYPES:
                continue

            cleaned = self._clean_entity_text(ent.text)
            if not cleaned or not self._is_valid_entity(cleaned):
                continue

            entity_type = self._correct_entity_type(cleaned, ent.label_)

            key = (cleaned, entity_type)
            if key in seen:
                continue
            seen.add(key)

            entities.append(ExtractedEntity(
                entity_type=entity_type,
                entity_text=cleaned,
                start_char=ent.start_char,
                end_char=ent.end_char,
                confidence=self._estimate_confidence(ent, cleaned),
                pipeline_version="entity-v2",
            ))

        return entities

    def _clean_entity_text(self, text: str) -> str:
        """Clean up entity text by removing noise."""
        text = text.strip()
        # Strip trailing possessives
        text = re.sub(r"'s$", "", text)
        text = re.sub(r"\u2019s$", "", text)  # Unicode right single quote
        # Strip trailing/leading punctuation (hyphens, commas, periods, colons)
        text = text.strip("-.,;:!?()[]{}\"'")
        # Strip leading articles that got captured
        text = re.sub(r"^(the|a|an)\s+", "", text, flags=re.IGNORECASE)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _is_valid_entity(self, text: str) -> bool:
        """Filter out garbage entities."""
        if len(text) < 2:
            return False
        if len(text) > 100:
            return False
        # Reject if mostly non-alphanumeric
        alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
        if alpha_ratio < 0.5:
            return False
        # Reject if it contains newlines (multi-line garbage)
        if "\n" in text:
            return False
        # Reject common noise patterns
        noise_patterns = [
            r"^(Jump|Skip|Click|Read|View|See|More|Next|Previous)\b",
            r"Wikipedia\s+Jump",
            r"^\d+$",  # Pure numbers
        ]
        for pattern in noise_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return False
        return True

    def _correct_entity_type(self, text: str, label: str) -> str:
        """Fix common SpaCy entity type mistakes."""
        lower = text.lower()
        # Technology/product names mistyped as PERSON
        if label == "PERSON" and lower in self.PRODUCT_OVERRIDES:
            return "PRODUCT"
        # Multi-word entities starting with a known product
        if label == "PERSON":
            first_word = lower.split()[0] if lower.split() else ""
            if first_word in self.PRODUCT_OVERRIDES:
                return "PRODUCT"
        return label

    def _html_to_text(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)

    def _estimate_confidence(self, ent, cleaned_text: str) -> float:
        """Heuristic confidence based on entity quality."""
        score = 0.7  # base confidence
        if len(cleaned_text) > 2:
            score += 0.1
        if ent.label_ in ("PERSON", "ORG"):
            score += 0.1
        # Lower confidence for very long entities (likely noise)
        if len(cleaned_text) > 50:
            score -= 0.2
        # Lower confidence if entity text differs a lot from original
        if len(cleaned_text) < len(ent.text.strip()) * 0.7:
            score -= 0.1
        return max(min(score, 1.0), 0.1)
