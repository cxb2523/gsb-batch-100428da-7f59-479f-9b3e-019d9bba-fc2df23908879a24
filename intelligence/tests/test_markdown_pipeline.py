import re

import pytest

from intelligence.models import MarkdownResult
from intelligence.pipelines.markdown import MarkdownPipeline

SOURCE_URL = "https://example.com/test"


@pytest.fixture
def pipeline():
    return MarkdownPipeline()


class TestMarkdownPipelineBasic:
    """Test basic extraction and edge cases."""

    def test_empty_html_returns_empty_list(self, pipeline):
        result = pipeline.extract("", SOURCE_URL)
        assert result == []

    def test_whitespace_only_returns_empty_list(self, pipeline):
        result = pipeline.extract("   \n\t  ", SOURCE_URL)
        assert result == []

    def test_none_html_returns_empty_list(self, pipeline):
        result = pipeline.extract(None, SOURCE_URL)
        assert result == []

    def test_returns_list_of_markdown_result(self, pipeline, simple_html):
        results = pipeline.extract(simple_html, SOURCE_URL)
        assert isinstance(results, list)
        assert len(results) <= 1
        if results:
            assert isinstance(results[0], MarkdownResult)

    def test_result_has_markdown_content(self, pipeline, simple_html):
        results = pipeline.extract(simple_html, SOURCE_URL)
        assert len(results) == 1
        assert len(results[0].markdown) > 0

    def test_pipeline_version_set(self, pipeline, simple_html):
        results = pipeline.extract(simple_html, SOURCE_URL)
        assert results[0].pipeline_version == "markdown-v1"


class TestMarkdownPipelineContentExtraction:
    """Test that nav/ads/boilerplate are stripped and content is preserved."""

    def test_strips_nav_elements(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        assert len(results) == 1
        md = results[0].markdown
        # Nav links should be stripped
        assert "/blog" not in md or "Blog" not in md.split("\n")[0:3]

    def test_strips_ad_banners(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "50% off" not in md

    def test_strips_footer(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "Privacy Policy" not in md
        assert "Terms of Service" not in md

    def test_strips_script_tags(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "tracking" not in md
        assert "<script>" not in md

    def test_preserves_article_content(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "Web Crawlers" in md or "web crawlers" in md
        assert "seed URLs" in md or "seed" in md

    def test_preserves_headings(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "#" in md  # At least one ATX heading

    def test_preserves_list_items(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        # Should contain list markers (-, *, or numbered)
        assert "URL frontier" in md or "Fetcher" in md

    def test_preserves_code_blocks(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "robots.txt" in md or "User-agent" in md or "Disallow" in md


class TestMarkdownPipelineCodeDocs:
    """Test with documentation page containing tables and code."""

    def test_preserves_table_content(self, pipeline, code_doc_html):
        results = pipeline.extract(code_doc_html, SOURCE_URL)
        md = results[0].markdown
        # Table content should be present (even if table structure varies)
        assert "/crawl" in md
        assert "/query" in md

    def test_preserves_code_snippets(self, pipeline, code_doc_html):
        results = pipeline.extract(code_doc_html, SOURCE_URL)
        md = results[0].markdown
        assert "requests.post" in md or "import requests" in md

    def test_has_code_flag(self, pipeline, code_doc_html):
        results = pipeline.extract(code_doc_html, SOURCE_URL)
        assert results[0].has_code is True

    def test_has_tables_flag(self, pipeline, code_doc_html):
        results = pipeline.extract(code_doc_html, SOURCE_URL)
        assert results[0].has_tables is True


class TestMarkdownPipelineMetadata:
    """Test metadata computation."""

    def test_word_count_positive(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        assert results[0].word_count > 0

    def test_confidence_in_range(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        assert 0.0 <= results[0].confidence <= 1.0

    def test_extraction_method_set(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        assert results[0].extraction_method in (
            "trafilatura_direct",
            "trafilatura_markdownify",
        )


class TestMarkdownPipelinePostProcessing:
    """Test post-processing rules."""

    def test_no_excessive_blank_lines(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        assert "\n\n\n\n" not in md  # Max 2 consecutive blank lines (3 newlines)

    def test_no_remaining_html_tags(self, pipeline, article_html):
        results = pipeline.extract(article_html, SOURCE_URL)
        md = results[0].markdown
        # Should not contain HTML tags (except possibly in code blocks)
        lines_outside_code = []
        in_code = False
        for line in md.split("\n"):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if not in_code:
                lines_outside_code.append(line)
        outside_code_text = "\n".join(lines_outside_code)
        # Check for common HTML tags outside code blocks
        assert "<div>" not in outside_code_text
        assert "<span>" not in outside_code_text
        assert "<nav>" not in outside_code_text

    def test_relative_urls_resolved(self, pipeline):
        html = """
        <html><body>
        <article>
        <p>Visit <a href="/about">our about page</a> for more info.</p>
        <p>Also see <a href="https://example.com/full">this full link</a>.</p>
        </article>
        </body></html>
        """
        results = pipeline.extract(html, "https://example.com/blog/post")
        if results:
            md = results[0].markdown
            # If relative URL is present, it should be resolved
            if "/about" in md:
                assert "https://example.com/about" in md or "(https://example.com/about)" in md


class TestMarkdownPipelineLowQuality:
    """Test handling of low-quality or unusual input."""

    def test_tiny_html_low_confidence(self, pipeline):
        html = "<html><body><p>Hi</p></body></html>"
        results = pipeline.extract(html, SOURCE_URL)
        # Either empty or low confidence
        if results:
            assert results[0].confidence < 0.9 or len(results[0].markdown) < 50

    def test_malformed_html_does_not_crash(self, pipeline):
        html = "<html><body><p>Unclosed tag<div>Mixed <b>nesting</p></div>"
        results = pipeline.extract(html, SOURCE_URL)
        assert isinstance(results, list)

    def test_non_html_content(self, pipeline):
        text = "This is just plain text, not HTML at all."
        results = pipeline.extract(text, SOURCE_URL)
        assert isinstance(results, list)
