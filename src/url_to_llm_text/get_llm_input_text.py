# SPDX-License-Identifier: MIT

from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin
from typing import Optional, Union, Dict, Any, List
from url_to_llm_text.config import ScraperConfig, load_config

try:
    from minify_html import minify
except ImportError:
    def minify(html_str: str) -> str:
        return html_str

try:
    from inscriptis import get_text
except ImportError:
    def get_text(html_str: str) -> str:
        return BeautifulSoup(html_str, "html.parser").get_text()


def extract_best_image_url(image_tag) -> Optional[str]:
    """
    Extracts the most relevant image URL from an <img> tag, checking standard src
    and lazy-loading attributes (data-src, data-original, data-lazy-src, srcset).
    This ensures image URLs are preserved even when image binaries are blocked.
    """
    src = (image_tag.get("src") or "").strip()
    lazy_candidates = [
        image_tag.get("data-src"),
        image_tag.get("data-original"),
        image_tag.get("data-lazy-src"),
        image_tag.get("data-url"),
        image_tag.get("data-hi-res-src"),
    ]
    valid_lazy = [c.strip() for c in lazy_candidates if c and isinstance(c, str) and c.strip()]

    # If src is missing or a tiny inline placeholder (data:image/svg+xml or 1x1 gif), use data-src
    if (not src or src.startswith("data:image")) and valid_lazy:
        return valid_lazy[0]

    if src and not src.startswith("data:image"):
        return src

    if valid_lazy:
        return valid_lazy[0]

    # Check srcset or data-srcset (format: "url1 1x, url2 2x" or "url1 300w, url2 600w")
    srcset = image_tag.get("srcset") or image_tag.get("data-srcset")
    if srcset and isinstance(srcset, str):
        candidates = [item.strip().split()[0] for item in srcset.split(",") if item.strip()]
        if candidates:
            return candidates[-1]  # Return highest resolution candidate if available

    return src if src else None


async def get_processed_text(
    page_source: str,
    base_url: str,
    html_parser: Optional[str] = None,
    keep_images: Optional[bool] = None,
    remove_svg_image: Optional[bool] = None,
    remove_gif_image: Optional[bool] = None,
    remove_image_types: Optional[List[str]] = None,
    keep_webpage_links: Optional[bool] = None,
    remove_script_tag: Optional[bool] = None,
    remove_style_tag: Optional[bool] = None,
    remove_tags: Optional[List[str]] = None,
    extract: Optional[bool] = None,
    remove_header_footer: Optional[bool] = None,
    max_characters: Optional[int] = None,
    config: Optional[Union[ScraperConfig, Dict[str, Any], str]] = None
) -> str:
    """
    Process html text into clean LLM-friendly input. Preserves tables in markdown,
    extracts links and images (including lazy-loaded data-src attributes), and cleans tags.

    Args:
        page_source (str): HTML source text.
        base_url (str): URL of the HTML source.
        html_parser (str, optional): BeautifulSoup parser, defaults to 'lxml'.
        keep_images (bool, optional): Keep image links. Default True.
        remove_svg_image (bool, optional): Remove .svg image links. Default True.
        remove_gif_image (bool, optional): Remove .gif image links. Default True.
        remove_image_types (list, optional): List of image extensions to remove.
        keep_webpage_links (bool, optional): Keep webpage links. Default True.
        remove_script_tag (bool, optional): Remove <script> tags. Default True.
        remove_style_tag (bool, optional): Remove <style> tags. Default True.
        remove_header_footer (bool, optional): Remove <header>, <footer>, <nav>, <aside>. Default False.
        remove_tags (list, optional): Additional tags to remove.
        extract (bool, optional): If True, extract raw text only. Default False.
        max_characters (int, optional): Max character limit for output text (0 or None for unlimited).
        config (ScraperConfig, dict, or str, optional): Configuration object or path.

    Returns:
        str: LLM-ready processed web page text.
    """
    try:
        cfg = load_config(config)

        # Merge function arguments with config defaults
        parser = html_parser if html_parser is not None else cfg.html_parser
        _keep_images = keep_images if keep_images is not None else cfg.keep_images
        _remove_svg = remove_svg_image if remove_svg_image is not None else cfg.remove_svg_image
        _remove_gif = remove_gif_image if remove_gif_image is not None else cfg.remove_gif_image
        _remove_img_types = remove_image_types if remove_image_types is not None else list(cfg.remove_image_types)
        _keep_links = keep_webpage_links if keep_webpage_links is not None else cfg.keep_webpage_links
        _remove_script = remove_script_tag if remove_script_tag is not None else cfg.remove_script_tag
        _remove_style = remove_style_tag if remove_style_tag is not None else cfg.remove_style_tag
        _remove_hdr_ftr = remove_header_footer if remove_header_footer is not None else cfg.remove_header_footer
        _remove_tags = remove_tags if remove_tags is not None else list(cfg.remove_tags)
        _extract = extract if extract is not None else cfg.extract
        _max_chars = max_characters if max_characters is not None else cfg.max_characters

        try:
            soup = BeautifulSoup(page_source, parser)
        except Exception as e:
            print("Error in BeautifulSoup: ", str(e))
            soup = BeautifulSoup("", "html.parser")

        # ------- Remove tags ----------
        tags_to_remove = []
        if _remove_script:
            tags_to_remove.append("script")
        if _remove_style:
            tags_to_remove.append("style")
        if _remove_hdr_ftr:
            tags_to_remove.extend(["header", "footer", "nav", "aside"])
        tags_to_remove.extend(_remove_tags)
        tags_to_remove = list(set(tags_to_remove))

        for tag in soup.find_all(tags_to_remove):
            try:
                tag.extract()
            except Exception as e:
                print("Error while removing tag: ", e)
                continue

        # -------- Process image links (with lazy load inspection) --------
        remove_image_type = []
        if _remove_svg:
            remove_image_type.append(".svg")
        if _remove_gif:
            remove_image_type.append(".gif")
        remove_image_type.extend(_remove_img_types)
        remove_image_type = list(set(remove_image_type))

        for image in soup.find_all("img"):
            try:
                if not _keep_images:
                    image.replace_with("")
                else:
                    image_link = extract_best_image_url(image)
                    type_replaced = False
                    if isinstance(image_link, str) and image_link:
                        if remove_image_type:
                            for img_type in remove_image_type:
                                if not type_replaced and img_type in image_link.lower():
                                    image.replace_with("")
                                    type_replaced = True
                        if not type_replaced:
                            full_url = urljoin(base_url, image_link)
                            image.replace_with("\n" + full_url + " ")
                    else:
                        image.replace_with("")
            except Exception as e:
                print("Error while getting image link: ", e)
                continue

        # ---------- Process website links -----------
        for link in soup.find_all("a", href=True):
            try:
                if not _keep_links:
                    link.replace_with("")
                else:
                    link_text = link.text.strip()
                    full_link = urljoin(base_url, link["href"])
                    if link_text:
                        link.replace_with(f"{link_text}: {full_link} ")
                    else:
                        link.replace_with(f"{full_link} ")
            except Exception as e:
                print("Error while getting webpage link: ", e)
                continue

        # ----------- Process tables into markdown ----------
        table_present = 0
        try:
            for tbs in soup.find_all("table"):
                try:
                    caption_tag = tbs.find("caption")
                    caption_text = caption_tag.get_text() if caption_tag else ""

                    tr_rows = tbs.find_all("tr")
                    if not tr_rows:
                        continue

                    header_row = tr_rows[0]
                    headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
                    markdown = []
                    if headers:
                        markdown = ["| " + " | ".join(headers) + " |"]
                        markdown.append("| " + " | ".join(["---"] * len(headers)) + " |")

                    current_row_header = None
                    for tr in tr_rows[1:]:
                        cells = tr.find_all(["td", "th"])
                        if not cells:
                            continue
                        row_data = []

                        if cells[0].name == "th" and cells[0].has_attr("rowspan"):
                            current_row_header = cells[0].get_text(strip=True)
                            cells = cells[1:]
                        elif cells[0].name == "th":
                            current_row_header = cells[0].get_text(strip=True)
                            cells = cells[1:]

                        if current_row_header is not None:
                            row_data.append(current_row_header)

                        row_data.extend(cell.get_text(strip=True) for cell in cells)
                        if any(row_data):
                            markdown.append("| " + " | ".join(row_data) + " |")

                    if markdown:
                        tbs.replace_with("ttaabbllee ssttaarrtt\n" + caption_text + "\n" + "\n".join(markdown) + "\nttabbllee eenndd")
                        table_present += 1
                except Exception as e:
                    print("Error while processing table: ", e)
                    continue
        except Exception as e:
            print("Error while processing tables: ", e)

        # ----------- Structure text output -----------
        if _extract:
            text = soup.get_text()
        else:
            body_content = soup.find("body")
            if table_present == 0:
                if body_content:
                    try:
                        minimized_body = minify(str(body_content))
                        text = get_text(minimized_body)
                    except Exception:
                        text = get_text(str(body_content))
                else:
                    text = soup.get_text()
            else:
                if body_content:
                    text = ""
                    body_content_str = str(body_content)
                    start_idx = [i.start() for i in re.finditer("ttaabbllee ssttaarrtt\n", body_content_str)]
                    end_idx = [i.end() for i in re.finditer("\nttabbllee eenndd", body_content_str)]
                    if 0 not in start_idx:
                        start_idx.insert(0, 0)
                    idxs = sorted(list(set(start_idx + end_idx)))

                    for i in range(len(idxs) - 1):
                        try:
                            txt = body_content_str[idxs[i]:idxs[i+1]]
                            if "ttaabbllee ssttaarrtt\n" in txt:
                                txt = txt.replace("ttaabbllee ssttaarrtt\n", "").replace("ttabbllee eenndd", "")
                                text = text + "\n" + txt
                            else:
                                txt = txt.replace("ttabbllee eenndd", "")
                                try:
                                    text = text + "\n" + get_text(minify(txt))
                                except Exception:
                                    text = text + "\n" + get_text(txt)
                        except Exception:
                            pass

                    # Process tail
                    if idxs:
                        tail_txt = body_content_str[idxs[-1]:]
                        tail_txt = tail_txt.replace("ttaabbllee ssttaarrtt\n", "").replace("ttabbllee eenndd", "")
                        try:
                            text = text + "\n" + get_text(minify(tail_txt))
                        except Exception:
                            text = text + "\n" + get_text(tail_txt)
                else:
                    text = soup.get_text()

        # Optional truncation if configured (> 0)
        if _max_chars is not None and _max_chars > 0:
            text = text[:_max_chars]

        return text

    except Exception as e:
        print("Error while getting processed text: ", e)
        return ""
