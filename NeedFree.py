import asyncio
import aiohttp
import datetime
import json
import pytz
import bs4
import sys
import random
from dataclasses import dataclass
from threading import Lock


API_URL_TEMPLATE = "https://store.steampowered.com/search/results/?query&start={pos}&count=100&infinite=1"
PAGE_SIZE = 100
MAX_RETRY_TIMES = 3


@dataclass
class Stats:
    """统计信息"""
    total_goods: int = 0
    free_goods: int = 0
    pages_completed: int = 0
    lock: Lock = None

    def __post_init__(self):
        self.lock = Lock()

    def update_display(self):
        """更新显示"""
        with self.lock:
            current_time = datetime.datetime.now().strftime('%H:%M:%S')
            msg = f"[{current_time}] 📊 总商品: {self.total_goods} | 免费商品: {self.free_goods} | 已完成页数: {self.pages_completed}\n"
            sys.stdout.write(msg)
            sys.stdout.flush()


stats = Stats()


def log(message):
    """带时间戳的日志输出"""
    current_time = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"[{current_time}] {message}")


async def random_delay():
    """页面/重试之间的随机延迟（1-4秒）"""
    await asyncio.sleep(1 + random.uniform(0, 3))


class PageFetchError(Exception):
    """单页抓取重试耗尽后抛出"""
    def __init__(self, start, reason):
        self.start = start
        self.reason = reason
        super().__init__(f"start={start} 抓取失败: {reason}")


async def fetch_Steam_json_response(session, url):
    ''' Fetch json response from Steam API using async
    session:        aiohttp session
    url:            Steam WebAPI url

    return:         (json content, None) on success, (None, error_reason) if failed
    '''
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:149.0) Gecko/20100101 Firefox/149.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'cross-site',
        'Sec-Fetch-User': '?1',
        'Priority': 'u=4',
        'Pragma': 'no-cache',
        'Cache-Control': 'no-cache'
    }

    try:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                ret_json = await response.json()
                return (ret_json, None)
            else:
                return (None, f"HTTP 状态码 {response.status}")

    except Exception as e:
        return (None, f"请求异常: {e}")


async def get_free_goods(session, start):
    ''' Extract 100%-discount goods list in a list of 100 products
    session:        aiohttp session
    start:          start page index

    return:         (goods_count, item_count, free_list)
    raises:         PageFetchError if all retry attempts are exhausted
    '''
    url = API_URL_TEMPLATE.format(pos=start)
    last_error = "未知错误"

    # 单页最多尝试 MAX_RETRY_TIMES 次
    for attempt in range(1, MAX_RETRY_TIMES + 1):
        response_json, error = await fetch_Steam_json_response(session, url)

        if error is None:
            try:
                goods_count = response_json["total_count"]

                # 动态更新总商品数，取最大值
                with stats.lock:
                    if goods_count > stats.total_goods:
                        stats.total_goods = goods_count

                goods_html = response_json["results_html"]
                page_parser = bs4.BeautifulSoup(goods_html, "html.parser")

                # 当前页实际包含的商品数，用于翻页终止判断
                item_count = len(page_parser.find_all(
                    name="a", class_="search_result_row"))

                full_discounts_div = page_parser.find_all(
                    name="div",
                    attrs={"class": "search_discount_block", "data-discount": "100"}
                )

                sub_free_list = []
                for div in full_discounts_div:
                    try:
                        parent = div.parent.parent.parent.parent
                        title = parent.find(name="span", attrs={"class": "title"}).get_text()
                        href = parent.get("href")
                        sub_free_list.append([title, href])
                    except Exception:
                        continue

                # 更新统计
                with stats.lock:
                    stats.free_goods += len(sub_free_list)
                    stats.pages_completed += 1

                stats.update_display()
                return (goods_count, item_count, sub_free_list)

            except Exception as e:
                last_error = f"解析响应失败: {e}"
        else:
            last_error = error

        if attempt < MAX_RETRY_TIMES:
            log(f"⚠️  start={start} 第 {attempt}/{MAX_RETRY_TIMES} 次尝试失败 ({last_error})，重试中...")
            await random_delay()

    # 重试耗尽，抛出异常由上层记录
    raise PageFetchError(start, last_error)


async def crawl(session):
    ''' 顺序抓取所有页面，按已抓取数量与商品总数判断终止

    session:        aiohttp session

    return:         (final_free_list, failed_pages)
                    failed_pages 元素为 {"page": 页码, "start": 起始位置, "reason": 失败原因}
    '''
    all_free_items = []
    failed_pages = []
    fetched_count = 0
    total_count = None
    current_page = 0

    while True:
        start = current_page * PAGE_SIZE

        # 起始位置已覆盖当前商品总数（可能有页面失败导致已抓取数滞后），不再多抓空页
        if total_count is not None and start >= total_count:
            break

        # 如果不是第一页，添加随机延迟
        if current_page > 0:
            await random_delay()

        # 获取当前页
        page_item_count = None
        try:
            goods_count, page_item_count, sub_free_list = await get_free_goods(session, start)
        except PageFetchError as e:
            # 重试耗尽：记录失败页码与原因，继续抓取其余页
            log(f"❌ 第 {current_page + 1} 页 (start={start}) 重试 {MAX_RETRY_TIMES} 次后仍失败: {e.reason}，跳过该页")
            failed_pages.append({
                "page": current_page + 1,
                "start": start,
                "reason": e.reason
            })
        else:
            total_count = goods_count
            fetched_count += page_item_count
            all_free_items.extend(sub_free_list)

        current_page += 1

        # 依据已抓取数量与当前商品总数判断是否结束
        if total_count is not None and fetched_count >= total_count:
            break
        # 成功返回但一页都没有商品，说明后面没有更多数据
        if page_item_count == 0:
            break

    print()  # 换行
    log("-" * 60)
    log("✅ 页面抓取结束")

    # 去重处理（按游戏名）
    log("🔄 去重处理中...")
    final_free_list = []
    free_names = set()

    for free_item in all_free_items:
        game_name = free_item[0]
        if game_name not in free_names:
            free_names.add(game_name)
            final_free_list.append(free_item)

    log(f"✅ 去重后: {len(final_free_list)} 个免费游戏")
    if failed_pages:
        log(f"⚠️  共 {len(failed_pages)} 页抓取失败: " +
            ", ".join(f"第 {p['page']} 页" for p in failed_pages))

    return (final_free_list, failed_pages)


def save_results(final_free_list, failed_pages, path="free_goods_detail.json"):
    """保存结果到 JSON 文件"""
    with open(path, "w", encoding="utf-8") as fp:
        json.dump({
            "total_count": len(final_free_list),
            "free_list": final_free_list,
            "failed_pages": failed_pages,
            "update_time": datetime.datetime.now(tz=pytz.timezone("Asia/Shanghai")).strftime('%Y-%m-%d %H:%M:%S')
        }, fp, ensure_ascii=False, indent=2)


async def main():
    log("=" * 60)
    log("🚀 NeedFree 爬虫启动 (顺序模式)")
    log("=" * 60)
    log("⚙️  请求间隔: 1-4秒随机延迟")
    log(f"⚙️  单页最多尝试: {MAX_RETRY_TIMES} 次")
    log("-" * 60)

    # 创建 aiohttp session
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        final_free_list, failed_pages = await crawl(session)

    # 保存结果
    log("💾 保存结果...")
    save_results(final_free_list, failed_pages)

    log("=" * 60)
    log(f"🎉 完成！共找到 {len(final_free_list)} 个免费游戏")
    if failed_pages:
        log(f"⚠️  {len(failed_pages)} 页抓取失败，详情见 free_goods_detail.json 的 failed_pages 字段")
    log("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
