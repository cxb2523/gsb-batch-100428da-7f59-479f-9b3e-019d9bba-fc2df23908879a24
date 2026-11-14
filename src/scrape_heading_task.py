from concurrent.futures import ThreadPoolExecutor

from botasaurus.request import request, Request
from botasaurus.soupify import soupify

# 批量抓取时的最大并发请求数，避免一次性打出过多请求
MAX_PARALLEL_REQUESTS = 5


def _scrape_heading(req: Request, url):
    """抓取单个链接的 h1 标题；任何失败都记录原因，绝不抛出异常。"""
    if not isinstance(url, str) or not url.strip():
        return {
            "url": url,
            "status": "error",
            "error": "链接无效：不是非空字符串",
        }

    try:
        # Navigate to the Link
        response = req.get(url)
    except Exception as e:
        return {
            "url": url,
            "status": "error",
            "error": f"请求失败: {e}",
        }

    status_code = getattr(response, "status_code", None)
    if status_code is not None and not 200 <= status_code < 300:
        return {
            "url": url,
            "status": "error",
            "error": f"请求返回非成功状态码: {status_code}",
        }

    # Create a BeautifulSoup object
    soup = soupify(response)

    # Retrieve the first h1 element's text, stripped of surrounding whitespace
    h1 = soup.find("h1")
    heading = h1.get_text().strip() if h1 is not None else ""
    if not heading:
        return {
            "url": url,
            "status": "error",
            "error": "页面没有 h1 标题或标题为空",
        }

    return {
        "url": url,
        "status": "success",
        "heading": heading,
    }


def _resolve_links(data):
    """解析入参：links 列表优先于单个 link。返回 (links, error)。"""
    links = data.get("links")
    if links is not None:
        if not isinstance(links, list) or len(links) == 0:
            return None, "参数错误: links 不能为空列表，请至少提供一个链接"
        return links, None

    link = data.get("link")
    if link:
        return [link], None

    return None, "参数错误: 缺少 link 或 links 参数"


@request
def scrape_heading_task(request: Request, data):
    links, error = _resolve_links(data)
    if error is not None:
        # 参数错误：返回一条明确的失败说明，而不是抛出未捕获异常
        return [{
            "url": None,
            "status": "error",
            "error": error,
        }]

    # 并发抓取（有上限）；executor.map 按提交顺序产出结果，
    # 因此输出顺序始终与输入顺序一致，不受完成先后影响。
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_REQUESTS, len(links))) as executor:
        results = list(executor.map(lambda url: _scrape_heading(request, url), links))

    # Save the data as a JSON file in output/scrape_heading_task.json
    return results
