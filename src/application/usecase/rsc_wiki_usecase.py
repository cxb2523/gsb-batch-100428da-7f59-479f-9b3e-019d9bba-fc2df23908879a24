#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Usecase for converting a wiki site to markdown using RSC interception.
"""

import os
from typing import List, Optional

from src.domain.models.wiki_page import (
    ConversionResult,
    PageOutcome,
    PageResult,
    WikiContent,
)
from src.domain.services.page_filename import (
    build_page_filename,
    escape_link_text,
    relative_link,
)
from src.domain.services.page_matcher import PageMatcher
from src.domain.models.wikisite_entity import WikiSite


class ConvertWikiSiteToMarkdownViaRSCUsecase:
    """
    通过RSC拦截将DeepWiki站点转换为Markdown文档集合的用例。

    抓取与落盘之间通过 ``rsc_repository`` / ``file_repository`` 两个边界解耦，
    测试时可以注入返回假数据的仓储实现。
    """

    def __init__(
        self,
        rsc_repository,
        file_repository,
        matcher: Optional[PageMatcher] = None,
    ):
        """
        Initialize the usecase with repositories.

        Args:
            rsc_repository: For fetching RSC content (``fetch_wiki_content``).
            file_repository: For file operations.
            matcher: Optional matcher override (useful for tests).
        """
        self.rsc_repository = rsc_repository
        self.file_repository = file_repository
        self.page_matcher = matcher or PageMatcher()

    async def execute(self, url: str, output_base_dir: str) -> ConversionResult:
        """
        通过RSC拦截将Wiki网站转换为Markdown文件的集合。

        Args:
            url: DeepWiki的URL (例: https://deepwiki.com/langchain-ai/langchain)
            output_base_dir: 输出目录的基本路径

        Returns:
            本次抓取的结算结果，包含每个导航页面的明确结局。
        """
        print("Starting RSC-based Wiki to Markdown conversion process...")
        print(f"Target URL: {url}")
        print(f"Output Base Directory: {output_base_dir}")
        print("Mode: RSC Interception (Direct Markdown extraction)")

        # 1.从URL中提取Wiki站点信息
        wiki_site = WikiSite.from_url(url)
        print(f"Processing wiki for {wiki_site.organization}/{wiki_site.repository}")

        # 2. 创建输出目录结构
        output_dir = wiki_site.get_output_directory(output_base_dir)
        self.file_repository.ensure_directory(output_dir)
        print(f"Output directory: {output_dir}")

        result = ConversionResult(output_directory=output_dir)

        # 3. 通过拦截RSC响应获取Wiki内容
        print("Fetching wiki content via RSC interception...")
        try:
            wiki_content = WikiContent.coerce(
                await self.rsc_repository.fetch_wiki_content(url)
            )
        except Exception as e:
            print(f"Error during RSC content extraction: {e}")
            result.error = str(e)
            return result

        if not wiki_content.navigation_pages:
            print("No navigation pages found via RSC interception. Exiting.")
            result.error = "no navigation pages found"
            # Refresh the index so a previous run's links cannot survive.
            self._write_index(result, wiki_site)
            return result

        print(f"Found {len(wiki_content.navigation_pages)} navigation pages.")
        print(f"Extracted content for {len(wiki_content.content_pages)} pages.")

        # 4. 将导航链接与内容匹配（稳定标识优先，标题仅作兜底）
        matches = self.page_matcher.match(wiki_content)

        # 5. 保存匹配成功的页面；其余页面只记录结局，不写出空文件/错页文件
        used_filenames: set[str] = set()
        for page_num, match in enumerate(matches, 1):
            title = match.navigation.title
            print(f"Processing page {page_num}/{len(matches)}: {title}")

            if match.status is PageOutcome.SAVED and match.content is not None:
                filename = build_page_filename(
                    match.page_id,
                    title,
                    page_num,
                    used_filenames,
                )
                page_filepath = os.path.join(output_dir, filename)

                try:
                    self.file_repository.save_markdown(
                        match.content.content, page_filepath
                    )
                except Exception as exc:
                    # A failed write must not be counted as saved nor linked
                    # from the index.
                    print(f"  Failed to write {page_filepath}: {exc}")
                    result.pages.append(
                        PageResult(
                            title=title,
                            url=match.navigation.url,
                            status=PageOutcome.CONTENT_MISSING,
                            reason=f"write failed: {exc}",
                            page_id=match.page_id,
                        )
                    )
                    continue

                used_filenames.add(filename)
                print(f"  Saved: {page_filepath} (matched by {match.match_kind})")
                result.pages.append(
                    PageResult(
                        title=title,
                        url=match.navigation.url,
                        status=PageOutcome.SAVED,
                        reason=f"matched by {match.match_kind}",
                        filename=filename,
                        page_id=match.page_id,
                    )
                )
                continue

            # Not written: record the explicit outcome instead of guessing.
            reason = match.reason or "content could not be matched"
            status = (
                match.status
                if match.status is not PageOutcome.SAVED
                else PageOutcome.CONTENT_MISSING
            )
            print(f"  Skipped ({status.value}): {reason}")
            result.pages.append(
                PageResult(
                    title=title,
                    url=match.navigation.url,
                    status=status,
                    reason=reason,
                    page_id=match.page_id,
                )
            )

        # 6. 索引只列出真正落盘的页面；即使本次一页未存也重写索引，
        #    避免上一轮遗留的 index.md 继续链接已失效的页面。
        self._write_index(result, wiki_site)

        self._report(result)
        return result

    def _write_index(self, result: ConversionResult, wiki_site: WikiSite) -> None:
        """Write ``index.md`` from the pages that actually reached disk."""
        index_content = self._generate_index(
            result.saved_pages,
            wiki_site.organization,
            wiki_site.repository,
        )
        index_filepath = os.path.join(result.output_directory, "index.md")
        try:
            self.file_repository.save_markdown(index_content, index_filepath)
            result.index_path = index_filepath
            print(f"Generated index page: {index_filepath}")
        except Exception as exc:
            print(f"Failed to write index {index_filepath}: {exc}")
            result.error = f"index write failed: {exc}"

    def _generate_index(
        self,
        saved_pages: List[PageResult],
        organization: str,
        repository: str,
    ) -> str:
        """生成只包含已保存页面的索引，链接文件名与磁盘文件一致。"""
        lines = [
            f"# {organization}/{repository}",
            "",
            "## Pages",
            "",
        ]

        for page in saved_pages:
            if not page.filename:
                continue
            lines.append(
                f"- [{escape_link_text(page.title)}]"
                f"({relative_link(page.filename)})"
            )

        lines.extend(
            [
                "",
                "---",
                "",
                "*Generated by deepwiki-get via RSC interception*",
                "",
            ]
        )
        return "\n".join(lines)

    def _report(self, result: ConversionResult) -> None:
        """在日志中为每个未保存页面留下明确原因，并打印汇总。"""
        mismatched = result.pages_with_status(PageOutcome.TITLE_MISMATCH)
        missing = result.pages_with_status(PageOutcome.CONTENT_MISSING)

        if mismatched:
            print("\nPages skipped because no unique content match was found:")
            for page in mismatched:
                print(f"  - {page.title} ({page.url}): {page.reason}")

        if missing:
            print("\nPages skipped because content is missing:")
            for page in missing:
                print(f"  - {page.title} ({page.url}): {page.reason}")

        print(
            "\nSummary: "
            f"{result.count(PageOutcome.SAVED)} saved, "
            f"{result.count(PageOutcome.TITLE_MISMATCH)} title-mismatch, "
            f"{result.count(PageOutcome.CONTENT_MISSING)} content-missing "
            f"(total {len(result.pages)} navigation pages)"
        )
        print(f"Output directory: {result.output_directory}")
