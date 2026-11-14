import os
import time
import random
import signal
import requests
import csv
import re
import html
from urllib.parse import urlparse, urljoin
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

from crawler_common import (
    CrawlStats,
    load_visited_urls,
    append_visited_url,
    save_visited_urls,
    print_summary,
)

# 使用字典存储全局状态以避免作用域问题
global_state = {'cancel_crawl': False}
YAHOO_URL = "https://hk.search.yahoo.com/search"
QUERY = ""#添加搜索内容
visited_urls_file = 'visited_urls_yahoo1.txt'
csv_file_path = 'yahoo_results1.csv'
MAX_PAGES = 1000
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36'
]

def signal_handler(sig, frame):
    global_state['cancel_crawl'] = True
    print("\n爬取已中断，正在保存当前进度...")

signal.signal(signal.SIGINT, signal_handler)

def init_driver():
    """初始化真实 Chrome 驱动（仅在未注入 driver 时由 main 调用）。"""
    # Chrome浏览器高级配置
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": random.choice(USER_AGENTS)})
        return driver
    except Exception as e:
        print(f"浏览器驱动初始化失败: {str(e)}")
        exit(1)

def random_delay(min=3, max=7):
    """Yahoo反爬较严，增加延迟时间"""
    time.sleep(random.uniform(min, max))

def search_yahoo(query, driver, stats, lang='zh-TW'):
    """执行Yahoo搜索"""
    search_results = set()

    for page in range(1, MAX_PAGES + 1):
        if global_state['cancel_crawl']:
            break

        params = {
            'p': query,
            'b': (page-1)*10 + 1,  # 台湾分页每页10条
            'vl': 'lang_' + lang,
            'geo': 'HK',  # 香港地区代码
            'country': 'HK',
            'fr2': 'sa',  # 添加必要参数
            'vlng': 'zh-Hant-HK'  # 设置香港繁体中文
        }

        try:
            random_delay()
            driver.execute_cdp_cmd('Network.setUserAgentOverride', {
                "userAgent": random.choice(USER_AGENTS)})

            driver.get(f"{YAHOO_URL}?{requests.compat.urlencode(params)}")

            # 处理验证页面：统一走收尾流程并标明验证页原因
            if "Verification" in driver.title:
                print("遇到验证页面，提前结束并保存进度！")
                stats.mark_verification()
                global_state['cancel_crawl'] = True
                break

            try:
                # 修复语法结构并增强反爬措施
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'div#web')))

                # 修改浏览器指纹特征
                driver.execute_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    window.chrome = {runtime: {}, app: {isInstalled: false}};
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['zh-TW', 'zh-CN', 'en-US']
                    });
                """)

                # 设置页面加载策略
                driver.execute_cdp_cmd('Page.setLifecycleEventsEnabled', {
                    'enabled': True
                })

                # 使用更通用的选择器并添加等待
                WebDriverWait(driver, 15).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, 'div#web li')))

                soup = BeautifulSoup(driver.page_source, 'html.parser')
                results = soup.select('div#web li h3 a')  # 更新选择器

                # 添加链接解析调试
                print(f"找到 {len(results)} 个链接元素")
                for link in results:
                    url = link.get('href')
                    print(f"解析链接: {url[:60]}...")
                    if url and url.startswith('http'):
                        search_results.add(url)

                print(f"第 {page} 页找到 {len(results)} 个结果")
                print(f"当前请求URL: {driver.current_url}")
                with open('page_source.html', 'w', encoding='utf-8') as f:
                    f.write(driver.page_source)

                # 检查是否有下一页
                next_page = soup.select_one('a.next')
                if not next_page:
                    break

            except Exception as e:
                print(f"页面元素加载失败: {str(e)}")
                break

        except Exception as e:
            print(f"搜索失败: {str(e)}")
            break

    return list(search_results)

def clean_content(html_content):
    """清理网页内容"""
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'iframe', 'noscript']):
        tag.decompose()
    text = soup.get_text(separator=' ', strip=True)
    text = re.sub(r'\s+', ' ', text)
    return text[:5000]

def crawl_page(url, visited, stats, driver, depth=1, max_depth=2):
    """递归爬取页面"""
    if global_state['cancel_crawl'] or depth > max_depth:
        return
    if url in visited:
        stats.skipped_count += 1
        print(f"跳过(已访问): {url}")
        return

    print(f"正在爬取 [{depth}级]: {url}")

    try:
        random_delay(2, 4)
        driver.get(url)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body')))

        # 抓取页遇到验证页面：统一走收尾流程并标明验证页原因
        if "Verification" in driver.title:
            print("遇到验证页面，提前结束并保存进度！")
            stats.mark_verification()
            global_state['cancel_crawl'] = True
            return

        title = driver.title
        content = clean_content(driver.page_source)

        with open(csv_file_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([title, url, content])

        # 成功写入 CSV 后立即持久化该 URL，中断也不丢进度
        visited.add(url)
        append_visited_url(visited_urls_file, url)
        stats.new_count += 1

        if depth < max_depth:
            links = driver.find_elements(By.TAG_NAME, 'a')
            for link in links:
                href = link.get_attribute('href')
                if href and href.startswith('http'):
                    absolute_url = urljoin(url, href)
                    if absolute_url not in visited:
                        crawl_page(absolute_url, visited, stats, driver, depth+1, max_depth)

    except Exception as e:
        print(f"爬取失败: {str(e)}")

def main(search_fn=None, driver=None):
    """主流程。

    search_fn: 可注入的搜索入口，签名 search_fn(query, driver, stats) -> [url,...]，
               默认使用真实 Yahoo 搜索 search_yahoo。
    driver:    可注入的抓取驱动（如本地假页面驱动），默认启动真实 Chrome。
    注入二者后无需真实浏览器与在线搜索即可驱动同一套收尾与持久化逻辑。
    """
    global_state['cancel_crawl'] = False

    if not os.path.exists(csv_file_path):
        with open(csv_file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['标题', 'URL', '内容'])

    visited = load_visited_urls(visited_urls_file)
    stats = CrawlStats()

    own_driver = driver is None
    if own_driver:
        driver = init_driver()
    if search_fn is None:
        search_fn = search_yahoo

    urls = []
    try:
        print("\n开始Yahoo搜索...")
        urls = search_fn(QUERY, driver, stats)

        for idx, url in enumerate(urls):
            if global_state['cancel_crawl']:
                break
            crawl_page(url, visited, stats, driver)
            if global_state['cancel_crawl']:
                break
            if (idx+1) % 5 == 0:  # 降低请求频率
                time.sleep(random.randint(15, 25))

    finally:
        if own_driver and driver is not None:
            driver.quit()
        # 无论正常结束、用户中断还是验证页，都保存当前已访问集合并交代进度
        save_visited_urls(visited_urls_file, visited)
        if global_state['cancel_crawl']:
            stats.mark_interrupted()  # 验证页原因优先，不会被覆盖
        print_summary(stats)
        print(f"本次搜索共获得 {len(urls)} 个结果")

if __name__ == '__main__':
    main()
