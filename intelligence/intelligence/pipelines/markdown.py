import re
from urllib.parse import urljoin, urlparse

import trafilatura
from markdownify import markdownify as md_convert

from intelligence.models import MarkdownResult
from intelligence.pipelines.base import BasePipeline


class MarkdownPipeline(BasePipeline):
    """Converts raw HTML to clean, token-efficient markdown.

    Strategy:
    1. Use trafilatura to extract main content as markdown (primary path)
    2. If trafilatura returns empty/low-quality, fall back to
       trafilatura HTML extraction piped through markdownify
    3. Post-process: normalize whitespace, fix relative URLs, strip leftover HTML
    """

    MIN_CONTENT_LENGTH = 50  # Characters below this = low confidence

    def extract(self, html: str, source_url: str) -> list[MarkdownResult]:
        if not html or not html.strip():
            return []

        # Primary path: trafilatura direct markdown output
        markdown = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_images=True,
            include_tables=True,
        )

        extraction_method = "trafilatura_direct"

        # Fallback: trafilatura HTML extraction + markdownify
        if not markdown or len(markdown.strip()) < self.MIN_CONTENT_LENGTH:
            markdown = self._fallback_extract(html)
            if markdown:
                extraction_method = "trafilatura_markdownify"

        if not markdown or not markdown.strip():
            return []

        # Post-processing
        markdown = self._post_process(markdown, source_url)

        if not markdown.strip():
            return []

        # Compute metadata
        word_count = len(markdown.split())
        has_code = "```" in markdown or "    " in markdown
        has_tables = "|" in markdown and "---" in markdown
        has_images = "![" in markdown

        # Confidence scoring
        confidence = self._compute_confidence(markdown, html)

        return [
            MarkdownResult(
                markdown=markdown,
                word_count=word_count,
                has_code=has_code,
                has_tables=has_tables,
                has_images=has_images,
                extraction_method=extraction_method,
                confidence=confidence,
                pipeline_version="markdown-v1",
            )
        ]

    def _fallback_extract(self, html: str) -> str | None:
        """Trafilatura HTML extraction piped through markdownify."""
        clean_html = trafilatura.extract(
            html,
            output_format="html",
            include_links=True,
            include_images=True,
            include_tables=True,
        )
        if not clean_html:
            return None

        return md_convert(
            clean_html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "footer"],
        )

    def _post_process(self, markdown: str, source_url: str) -> str:
        """Clean up markdown output."""
        # Normalize excessive blank lines (max 2 consecutive newlines)
        markdown = re.sub(r"\n{4,}", "\n\n\n", markdown)

        # Resolve relative URLs
        markdown = self._resolve_relative_urls(markdown, source_url)

        # Strip any remaining HTML tags (outside of code blocks)
        markdown = self._strip_html_outside_code(markdown)

        # Trim leading/trailing whitespace
        markdown = markdown.strip()

        return markdown

    def _resolve_relative_urls(self, markdown: str, source_url: str) -> str:
        """Convert relative URLs in markdown links/images to absolute."""
        base_url = source_url

        def replace_url(match: re.Match) -> str:
            prefix = match.group(1)  # ]( or ](
            url = match.group(2)
            suffix = match.group(3)  # ) or "title")
            parsed = urlparse(url)
            if not parsed.scheme and not parsed.netloc and url.startswith("/"):
                url = urljoin(base_url, url)
            return f"{prefix}{url}{suffix}"

        # Match markdown links: [text](url) and images: ![alt](url)
        markdown = re.sub(
            r"(\]\()([^)]+)(\))",
            replace_url,
            markdown,
        )
        return markdown

    def _strip_html_outside_code(self, markdown: str) -> str:
        """Remove leftover HTML tags that are outside code blocks."""
        lines = markdown.split("\n")
        result = []
        in_code = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code = not in_code
                result.append(line)
                continue

            if in_code:
                result.append(line)
            else:
                # Remove HTML tags but preserve content
                cleaned = re.sub(r"<(?!code|/code)[^>]+>", "", line)
                result.append(cleaned)

        return "\n".join(result)

    def _compute_confidence(self, markdown: str, html: str) -> float:
        """Heuristic confidence based on extraction quality."""
        confidence = 0.9  # Base confidence for successful extraction

        # Penalize very short content
        if len(markdown.strip()) < self.MIN_CONTENT_LENGTH:
            confidence = 0.3

        # Penalize if markdown is drastically shorter than a rough plaintext estimate
        # (rough estimate: HTML is ~3-5x longer than content)
        html_text_estimate = len(html) / 4
        if html_text_estimate > 100 and len(markdown) < html_text_estimate * 0.05:
            confidence = min(confidence, 0.5)

        return round(confidence, 2)
