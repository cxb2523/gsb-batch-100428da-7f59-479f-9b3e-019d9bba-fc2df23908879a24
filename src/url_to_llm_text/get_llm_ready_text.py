# SPDX-License-Identifier: MIT

from typing import Optional, Union, Dict, Any, List
from url_to_llm_text.config import ScraperConfig, load_config
from url_to_llm_text.get_html_text import get_page_source
from url_to_llm_text.get_llm_input_text import get_processed_text

async def url_to_llm_text(
    url: str,
    wait: Optional[float] = None,
    user_agent: Optional[str] = None,
    html_parser: Optional[str] = None,
    keep_images: Optional[bool] = None,
    remove_svg_image: Optional[bool] = None,
    remove_gif_image: Optional[bool] = None,
    remove_image_types: Optional[List[str]] = None,
    keep_webpage_links: Optional[bool] = None,
    remove_script_tag: Optional[bool] = None,
    remove_style_tag: Optional[bool] = None,
    remove_tags: Optional[List[str]] = None,
    remove_header_footer: Optional[bool] = None,
    proxy: Optional[Union[str, Dict[str, Any]]] = None,
    max_characters: Optional[int] = None,
    config: Optional[Union[ScraperConfig, Dict[str, Any], str]] = None
) -> str:
    """
    Main function to scrape a URL and return clean LLM-friendly text.

    Args:
        url (str): The target URL.
        wait (float, optional): Extra wait seconds. Defaults to config.
        user_agent (str, optional): Custom user-agent string. Defaults to config.
        html_parser (str, optional): Parser to use (e.g., 'lxml'). Defaults to config.
        keep_images (bool, optional): Keep image links. Defaults to config.
        remove_svg_image (bool, optional): Remove SVG image links. Defaults to config.
        remove_gif_image (bool, optional): Remove GIF image links. Defaults to config.
        remove_image_types (list, optional): List of image file extensions to remove.
        keep_webpage_links (bool, optional): Keep hyperlinks. Defaults to config.
        remove_script_tag (bool, optional): Remove <script> tags. Defaults to config.
        remove_style_tag (bool, optional): Remove <style> tags. Defaults to config.
        remove_header_footer (bool, optional): Remove <header>, <footer>, <nav>, <aside>. Defaults to config.
        remove_tags (list, optional): List of additional HTML tags to remove.
        proxy (str or dict, optional): Proxy URL string ("http://user:pass@host:port") or dict.
        max_characters (int, optional): Max character count in output (default unlimited).
        config (ScraperConfig, dict, or str, optional): Configuration object or path to YAML/JSON.

    Returns:
        str: LLM-ready processed web page text.
    """
    try:
        cfg = load_config(config)

        page_source = await get_page_source(
            url=url,
            wait=wait,
            user_agent=user_agent,
            proxy=proxy,
            config=cfg
        )

        if not page_source:
            return ""

        llm_text = await get_processed_text(
            page_source=page_source,
            base_url=url,
            html_parser=html_parser,
            keep_images=keep_images,
            remove_svg_image=remove_svg_image,
            remove_gif_image=remove_gif_image,
            remove_image_types=remove_image_types,
            keep_webpage_links=keep_webpage_links,
            remove_script_tag=remove_script_tag,
            remove_style_tag=remove_style_tag,
            remove_header_footer=remove_header_footer,
            remove_tags=remove_tags,
            max_characters=max_characters,
            config=cfg
        )

        return llm_text
    except Exception as e:
        print("Error while scraping url: ", str(e))
        return ""
