import json
import re

from bs4 import BeautifulSoup

from intelligence.models import DetectedSchema
from intelligence.pipelines.base import BasePipeline


class SchemaPipeline(BasePipeline):
    """Detects JSON-LD, OpenAPI specs, and other structured schemas in HTML."""

    def extract(self, html: str, source_url: str) -> list[DetectedSchema]:
        if not html or not html.strip():
            return []

        schemas: list[DetectedSchema] = []
        soup = BeautifulSoup(html, "html.parser")

        schemas.extend(self._extract_jsonld(soup, source_url))
        schemas.extend(self._extract_openapi(soup, source_url))
        schemas.extend(self._extract_swagger_ui(soup, source_url))
        schemas.extend(self._extract_redoc(soup, source_url))

        return schemas

    def _extract_jsonld(self, soup: BeautifulSoup, source_url: str) -> list[DetectedSchema]:
        results = []
        for script in soup.find_all("script", type="application/ld+json"):
            text = script.string
            if not text:
                continue
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue

            results.append(DetectedSchema(
                schema_type="JSON_LD",
                schema_data=data,
                source_url=source_url,
                confidence=0.95,
                pipeline_version="schema-v1",
            ))
        return results

    def _extract_openapi(self, soup: BeautifulSoup, source_url: str) -> list[DetectedSchema]:
        results = []

        candidates = []
        for pre in soup.find_all("pre"):
            code = pre.find("code")
            text = code.string if code else pre.string
            if text:
                candidates.append(text)

        for candidate in candidates:
            candidate = candidate.strip()
            try:
                data = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                if "openapi:" in candidate or "swagger:" in candidate:
                    continue
                continue

            if self._is_openapi(data):
                results.append(DetectedSchema(
                    schema_type="OPENAPI",
                    schema_data=data,
                    source_url=source_url,
                    confidence=0.9,
                    pipeline_version="schema-v1",
                ))

        return results

    def _is_openapi(self, data: dict) -> bool:
        if not isinstance(data, dict):
            return False
        return (
            "openapi" in data
            or "swagger" in data
            or ("info" in data and "paths" in data)
        )

    def _extract_swagger_ui(self, soup: BeautifulSoup, source_url: str) -> list[DetectedSchema]:
        """Detect OpenAPI specs embedded in Swagger UI script blocks."""
        results = []

        for script in soup.find_all("script"):
            text = script.string
            if not text:
                continue

            # Only look in scripts that reference SwaggerUIBundle or have inline spec patterns
            if "SwaggerUIBundle" not in text and "const spec" not in text:
                continue

            # Find variable assignments that look like JSON objects.
            # Use a brace-depth scan to correctly capture the full object including nested braces.
            assign_pattern = re.compile(
                r"(?:const|var|let)\s+\w+\s*=\s*(\{)",
                re.DOTALL,
            )
            for m in assign_pattern.finditer(text):
                start = m.start(1)
                depth = 0
                end = start
                for i, ch in enumerate(text[start:], start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end <= start:
                    continue

                candidate = text[start:end]
                try:
                    data = json.loads(candidate)
                except (json.JSONDecodeError, TypeError):
                    continue

                if self._is_openapi(data):
                    results.append(DetectedSchema(
                        schema_type="OPENAPI",
                        schema_data=data,
                        source_url=source_url,
                        confidence=0.88,
                        pipeline_version="schema-v1",
                    ))

        return results

    def _extract_redoc(self, soup: BeautifulSoup, source_url: str) -> list[DetectedSchema]:
        """Detect OpenAPI specs embedded via Redoc patterns."""
        results = []

        # Pattern 1: <script type="application/json"> containing an OpenAPI spec
        for script in soup.find_all("script", type="application/json"):
            text = script.string
            if not text:
                continue
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue

            if self._is_openapi(data):
                results.append(DetectedSchema(
                    schema_type="OPENAPI",
                    schema_data=data,
                    source_url=source_url,
                    confidence=0.88,
                    pipeline_version="schema-v1",
                ))

        # Pattern 2: <redoc spec-url="..."> element present — page is a Redoc API doc
        # We already extracted the inline spec above; if there's a <redoc> tag but no
        # inline script we can still note the detection (without schema_data to parse).
        # Only add a stub if we haven't already found a spec from an inline script.
        if not results:
            redoc_tag = soup.find("redoc")
            if redoc_tag and redoc_tag.get("spec-url"):
                # No inline spec available — skip (no parseable schema_data)
                pass

        return results
