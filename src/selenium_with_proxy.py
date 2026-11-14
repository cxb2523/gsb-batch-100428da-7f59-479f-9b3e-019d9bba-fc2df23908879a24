# -*- coding: utf-8 -*-
"""
使用 Mihomo 代理池的 Selenium 访问脚本
"""
import os
import time
import random
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
PROXY_RESULTS_PATH = BASE_DIR / "proxies" / "proxy_test_results.json"

# ========== 配置区 ==========
CONFIG = {
    # 基础配置
    "URL": "https://google.com",
    "MAX_VISITS": 5,  # 最大访问次数，0表示无限
    "WAIT_AFTER_LOAD": 2,  # 页面加载后等待秒数
    
    # 访问间隔模式（两种模式）
    "INTERVAL_MODE": "poisson",  # 间隔模式：'fixed'（固定间隔）或 'poisson'（泊松分布，推荐）
    "INTERVAL_MEAN": 10,  # 间隔均值（秒）
    #   - fixed 模式：每次等待固定的 INTERVAL_MEAN 秒
    #   - poisson 模式：平均等待 INTERVAL_MEAN 秒，但每次随机（更自然，模拟真实用户）
    
    # 代理配置
    "USE_PROXY": True,  # 是否使用代理
    "HEADLESS": True,  # 是否使用无头模式（不打开浏览器窗口）
    "MIHOMO_API": "http://127.0.0.1:19090",  # Mihomo REST API 地址
    "MIHOMO_PROXY": "http://127.0.0.1:17890",  # Mihomo HTTP 代理地址
    "PROXY_GROUP": "NODE_TEST",  # Mihomo 代理组名称（用于读取节点列表）
    "SWITCH_GROUP": "GLOBAL",  # Mihomo 切换组名称（实际切换的组，流量走这个组）
    # 节点切换重试：首次切换失败后，最多再换多少个“本轮未尝试过”的节点重试（不含首次）
    # 总尝试次数 = 1 + SWITCH_MAX_RETRIES；重试仍失败则本轮记为 SWITCH_FAILED 并跳过浏览器访问
    "SWITCH_MAX_RETRIES": 2,
    "PROXY_RESULTS": str(PROXY_RESULTS_PATH),  # 测试结果文件（固定到仓库根目录）
    "CSV_FILE": "visit_log.csv",  # 日志文件名
}

# ========== 日志结局标记（CSV 列结构不变，仅约定 status / 占位值的含义）==========
STATUS_SUCCESS = "SUCCESS"                    # 首次切换即成功，访问正常完成
STATUS_SUCCESS_RETRY = "SUCCESS_RETRY"        # 切换失败后换节点重试成功，访问正常完成
STATUS_SWITCH_FAILED = "SWITCH_FAILED"        # 切换重试耗尽，本轮未打开浏览器
# exit_ip 占位：切换成功但出口 IP 查询失败（本轮仍照常访问）
EXIT_IP_QUERY_FAILED = "IP_QUERY_FAILED"
# proxy_node 占位：本轮所有切换尝试均失败，没有任何节点切换成功
PROXY_NODE_SWITCH_FAILED = "SWITCH_FAILED"

# ========== Mihomo 代理池类 ==========
class MihomoProxyPool:
    """Mihomo 代理池管理"""
    def __init__(self, results_file=None, api_url=None, group_name=None, switch_group=None):
        self.available_nodes = []
        self.failed_nodes = []
        if results_file is None:
            results_file = CONFIG["PROXY_RESULTS"]
        if api_url is None:
            api_url = CONFIG["MIHOMO_API"]
        if group_name is None:
            group_name = CONFIG["PROXY_GROUP"]
        if switch_group is None:
            switch_group = CONFIG["SWITCH_GROUP"]
        
        self.api_url = api_url
        self.group_name = group_name
        self.switch_group = switch_group
        self.load_test_results(results_file)
    
    def load_test_results(self, filepath):
        """从测试结果文件加载可用节点"""
        path = Path(filepath)
        if not path.is_absolute():
            path = (BASE_DIR / filepath).resolve()
        
        if not path.exists():
            print(f"⚠️  测试结果文件不存在: {path}")
            return
        
        with path.open("r", encoding="utf-8") as f:
            results = json.load(f)
        
        # 兼容 check_proxies.py 多种格式
        if "meta" in results:
            # 新格式: {"meta": {...}, "ok": [...], "failed": [...]}
            self.available_nodes = results.get("ok", [])
            self.failed_nodes = results.get("failed", [])
        elif "ok" in results:
            # 中间格式: {"ok": [...], "failed": [...]} (没有 meta)
            self.available_nodes = results.get("ok", [])
            self.failed_nodes = results.get("failed", [])
        else:
            # 旧格式: {"available": [...], "failed": [...]}
            self.available_nodes = results.get("available", [])
            self.failed_nodes = results.get("failed", [])
        
        print(f"✅ 加载 {len(self.available_nodes)} 个可用节点")
        if len(self.failed_nodes) > 0:
            print(f"ℹ️  {len(self.failed_nodes)} 个节点不可用")
    
    def get_random_node(self):
        """随机获取可用节点"""
        if not self.available_nodes:
            return None
        return random.choice(self.available_nodes)

    def get_round_candidates(self):
        """
        本轮候选节点：随机打乱后的可用节点列表（按 name 去重）。
        切换重试时按顺序取用，保证每次重试都换成一个本轮没试过的节点，
        不会静默沿用切换失败前的旧节点。
        """
        seen = set()
        candidates = []
        for node in random.sample(self.available_nodes, len(self.available_nodes)):
            node_name = node.get("name")
            if node_name and node_name not in seen:
                seen.add(node_name)
                candidates.append(node)
        return candidates

    def switch_node(self, node_name):
        """切换 Mihomo 代理节点"""
        url = f"{self.api_url}/proxies/{self.switch_group}"
        payload = {"name": node_name}

        try:
            response = requests.put(url, json=payload, timeout=5)
            if response.status_code == 204:
                # 添加短暂延迟，等待节点切换生效
                time.sleep(0.3)
                return True, "切换成功"
            else:
                return False, f"HTTP {response.status_code}"
        except Exception as e:
            return False, str(e)

    def switch_with_retry(self, max_retries=None):
        """
        在本轮候选节点上切换代理，切换失败则换一个本轮未尝试过的节点重试。

        Args:
            max_retries: 首次切换失败后的最大重试次数（不含首次）；
                         None 时取 CONFIG["SWITCH_MAX_RETRIES"]。

        Returns:
            dict:
              success:      是否最终切换成功
              node:         切换成功的节点 dict（失败为 None）
              attempts:     每次尝试的记录 [{"node": 名称, "ok": bool, "msg": str}, ...]
              retries_used: 实际使用的重试次数
        """
        if max_retries is None:
            max_retries = CONFIG["SWITCH_MAX_RETRIES"]
        max_retries = max(0, int(max_retries))

        candidates = self.get_round_candidates()
        attempts = []
        # 总尝试次数 = 1（首次）+ max_retries，但不超过候选节点数
        attempt_limit = min(1 + max_retries, len(candidates))

        for idx in range(attempt_limit):
            node = candidates[idx]
            node_name = node.get("name")
            ok, msg = self.switch_node(node_name)
            attempts.append({"node": node_name, "ok": ok, "msg": msg})
            if ok:
                return {
                    "success": True,
                    "node": node,
                    "attempts": attempts,
                    "retries_used": idx,
                }
            print(f"  ⚠️  节点 {node_name} 切换失败（{msg}）"
                  f"，这是第 {idx + 1}/{attempt_limit} 次尝试")

        return {
            "success": False,
            "node": None,
            "attempts": attempts,
            "retries_used": len(attempts) - 1,
        }

    def get_current_ip_detail(self):
        """
        获取当前出口 IP（带失败原因）。
        不引入新的外部服务，沿用原有的 ipify 查询。

        Returns:
            (ip, error): 成功时 error 为 None；失败时 ip 为 None，error 为原因字符串
        """
        try:
            response = requests.get(
                "https://api.ipify.org?format=json",
                proxies={
                    "http": CONFIG["MIHOMO_PROXY"],
                    "https": CONFIG["MIHOMO_PROXY"]
                },
                timeout=10
            )
            if response.status_code == 200:
                ip = response.json().get("ip")
                if ip:
                    return ip, None
                return None, f"响应缺少 ip 字段: {response.text[:100]}"
            return None, f"HTTP {response.status_code}"
        except Exception as e:
            return None, str(e)

    def get_current_ip(self):
        """获取当前出口 IP（失败返回 None，保留旧接口供诊断脚本使用）"""
        ip, _ = self.get_current_ip_detail()
        return ip
    
    def __len__(self):
        return len(self.available_nodes)

# User-Agent 列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
]

SCREEN_SIZES = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 414, "height": 896},
    {"width": 390, "height": 844},
]

def get_log_file_path():
    """获取日志文件路径"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return str(LOGS_DIR / CONFIG["CSV_FILE"])

def now_iso():
    """返回当前时间（UTC）"""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def get_interval(mode="poisson", mean=10):
    """
    获取访问间隔时间
    
    Args:
        mode: 间隔模式
            - 'fixed': 固定间隔，每次返回 mean
            - 'poisson': 泊松分布（指数间隔），平均为 mean，但每次随机
        mean: 间隔均值（秒）
    
    Returns:
        float: 间隔时间（秒）
    
    数学原理：
        泊松过程描述的是事件随机发生的过程（如网站访问、排队到达等）
        在泊松过程中，两次事件之间的时间间隔服从指数分布
        参数 λ = 1/mean，即平均间隔时间的倒数
        
        为什么用泊松分布？
        1. 真实用户访问不是机械地每隔固定时间点击
        2. 泊松分布更自然，不易被检测为机器人
        3. 符合实际的网络流量规律
    """
    if mode == "fixed":
        # 固定间隔模式
        return mean
    elif mode == "poisson":
        # 泊松分布模式（指数间隔）
        # np.random.exponential(mean) 生成服从指数分布的随机数
        # 平均值为 mean，但每次都不同
        interval = np.random.exponential(mean)
        # 设置最小间隔为2秒，避免请求过于频繁
        return max(2.0, interval)
    else:
        # 未知模式，默认使用泊松分布
        print(f"⚠️  未知间隔模式 '{mode}'，使用默认泊松分布")
        interval = np.random.exponential(mean)
        return max(2.0, interval)

def get_random_device():
    """随机设备配置"""
    ua = random.choice(USER_AGENTS)
    screen = random.choice(SCREEN_SIZES)
    return ua, screen

def create_driver(user_agent, screen_size, use_proxy=False, headless=False):
    """创建Chrome驱动"""
    options = webdriver.ChromeOptions()
    
    # 无头模式设置
    if headless:
        options.add_argument('--headless=new')  # 使用新版无头模式
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        print(f"  👻 模式: 无头模式（后台运行）")
    else:
        print(f"  🖥️  模式: 显示浏览器窗口")
    
    # 设置User-Agent
    options.add_argument(f'user-agent={user_agent}')
    
    # 设置窗口大小
    options.add_argument(f'--window-size={screen_size["width"]},{screen_size["height"]}')
    
    # Mihomo 代理设置
    if use_proxy:
        proxy_address = CONFIG["MIHOMO_PROXY"]
        print(f"  🌐 代理: {proxy_address}")
        options.add_argument(f'--proxy-server={proxy_address}')
    else:
        print(f"  🌐 代理: 无")
    
    # 反检测设置
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    # 创建driver
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
        '''
    })
    
    return driver

def write_csv_header(csvfile):
    """写入CSV头"""
    with open(csvfile, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if f.tell() == 0:
            writer.writerow([
                "timestamp_utc", "visit_number", "url", "proxy_node",
                "exit_ip", "user_agent", "screen_width", "screen_height",
                "status", "note"
            ])

def log_visit(visit_num, url, proxy_node, exit_ip, user_agent, screen_size, status, note=""):
    """记录访问日志"""
    log_file = get_log_file_path()
    with open(log_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            now_iso(), visit_num, url, proxy_node or "DIRECT",
            exit_ip or "N/A", user_agent, screen_size["width"], screen_size["height"],
            status, note
        ])

def visit_page(driver, url):
    """访问页面"""
    try:
        driver.get(url)
        time.sleep(CONFIG["WAIT_AFTER_LOAD"])
        
        # 获取页面标题
        page_title = driver.title[:50] if driver.title else "无标题"
        
        # 随机滚动
        scroll_times = random.randint(1, 3)
        for _ in range(scroll_times):
            scroll_amount = random.randint(300, 800)
            driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
            time.sleep(random.uniform(0.5, 1.5))
        
        if random.random() < 0.3:
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(random.uniform(0.5, 1.0))
        
        return "SUCCESS", page_title
    except Exception as e:
        return "ERROR", str(e)

def main():
    """主函数"""
    url = CONFIG["URL"]
    max_visits = CONFIG["MAX_VISITS"]
    use_proxy = CONFIG["USE_PROXY"]
    log_file = get_log_file_path()
    
    # 初始化 Mihomo 代理池
    proxy_pool = None
    if use_proxy:
        proxy_pool = MihomoProxyPool()
        if len(proxy_pool) == 0:
            print("⚠️  代理池为空，将不使用代理")
            use_proxy = False
    
    print(f"🚀 开始访问任务")
    print(f"📍 目标 URL: {url}")
    print(f"🔢 最大访问次数: {max_visits if max_visits > 0 else '无限'}")
    print(f"⏱️  间隔模式: {CONFIG['INTERVAL_MODE']} (均值: {CONFIG['INTERVAL_MEAN']}秒)")
    if CONFIG['INTERVAL_MODE'] == 'fixed':
        print(f"   → 固定间隔，每次等待 {CONFIG['INTERVAL_MEAN']} 秒")
    else:
        print(f"   → 泊松分布（指数间隔），平均 {CONFIG['INTERVAL_MEAN']} 秒，更自然")
    print(f"👻 无头模式: {'开启（后台运行）' if CONFIG['HEADLESS'] else '关闭（显示窗口）'}")
    print(f"🌐 使用代理: {'是' if use_proxy else '否'}")
    if use_proxy:
        print(f"📊 代理池大小: {len(proxy_pool)}")
        print(f"🔗 Mihomo API: {CONFIG['MIHOMO_API']}")
        print(f"🔌 Mihomo 代理: {CONFIG['MIHOMO_PROXY']}")
        print(f"🔄 切换代理组: {CONFIG['SWITCH_GROUP']}")
    print(f"💾 日志文件: {log_file}")
    print("-" * 60)
    
    write_csv_header(log_file)
    
    visit_count = 0
    driver = None
    
    try:
        while True:
            if max_visits > 0 and visit_count >= max_visits:
                print(f"\n✅ 已完成 {visit_count} 次访问")
                break
            
            visit_count += 1
            user_agent, screen_size = get_random_device()

            # 切换代理节点（失败时换本轮未尝试过的节点重试，不沿用旧节点）
            proxy_node = None
            exit_ip = None
            switch_base_status = None  # 切换阶段的结局分类：SUCCESS / SUCCESS_RETRY / SWITCH_FAILED
            ip_query_error = None      # 切换成功但出口 IP 查询失败时的原因
            skip_visit = False         # 切换耗尽失败时跳过浏览器访问
            if use_proxy and proxy_pool:
                print(f"\n[访问 #{visit_count}]")
                result = proxy_pool.switch_with_retry()
                attempts = result["attempts"]

                if not result["success"]:
                    # 切换重试耗尽：本轮记为失败并跳过浏览器访问，避免用错误出口污染采集结果
                    switch_base_status = STATUS_SWITCH_FAILED
                    proxy_node = PROXY_NODE_SWITCH_FAILED
                    skip_visit = True
                    tried = "; ".join(
                        f"{a['node']}->{'OK' if a['ok'] else a['msg']}" for a in attempts
                    )
                    note = (
                        f"节点切换重试耗尽（共尝试 {len(attempts)} 次，"
                        f"上限 {CONFIG['SWITCH_MAX_RETRIES']} 次重试），跳过浏览器访问；尝试明细: {tried}"
                    )
                    print(f"  ❌ {note}")
                    log_visit(visit_count, url, proxy_node, "N/A",
                              user_agent, screen_size, STATUS_SWITCH_FAILED, note)
                else:
                    node = result["node"]
                    node_name = node.get("name")
                    latency = node.get("latency_ms", node.get("latency", "N/A"))
                    proxy_node = node_name
                    switch_base_status = (
                        STATUS_SUCCESS_RETRY if result["retries_used"] > 0 else STATUS_SUCCESS
                    )

                    if result["retries_used"] > 0:
                        tried = "; ".join(
                            f"{a['node']}->{'OK' if a['ok'] else a['msg']}" for a in attempts[:-1]
                        )
                        print(f"  ✅ 节点切换成功（重试 {result['retries_used']} 次后）: "
                              f"{node_name} (延迟: {latency}ms)；失败尝试: {tried}")
                    else:
                        print(f"  🔄 切换节点: {node_name} (延迟: {latency}ms)")
                        print(f"  ✅ 节点切换成功")

                    # 验证出口 IP；查询失败不阻断本轮访问，仅在日志中明确标注
                    print(f"  🔍 查询出口 IP...")
                    exit_ip, ip_query_error = proxy_pool.get_current_ip_detail()
                    if exit_ip:
                        print(f"  🌍 当前 IP: {exit_ip}")
                    else:
                        print(f"  ⚠️  出口 IP 查询失败（不影响本轮访问）: {ip_query_error}")
            else:
                print(f"\n[访问 #{visit_count}]")

            if skip_visit:
                # 切换耗尽失败也占一条记录（上面已写入），visit_number 已自增、保持连续
                if max_visits == 0 or visit_count < max_visits:
                    interval = get_interval(CONFIG["INTERVAL_MODE"], CONFIG["INTERVAL_MEAN"])
                    mode_desc = "固定" if CONFIG["INTERVAL_MODE"] == "fixed" else "泊松分布"
                    print(f"  ⏳ 等待 {interval:.1f} 秒（{mode_desc}）...")
                    time.sleep(interval)
                continue

            # 关闭旧driver
            if driver:
                try:
                    driver.quit()
                except:
                    pass

            print(f"  🖥️  设备: {screen_size['width']}x{screen_size['height']}")

            try:
                driver = create_driver(user_agent, screen_size, use_proxy, CONFIG["HEADLESS"])
                page_status, page_note = visit_page(driver, url)

                if use_proxy and proxy_pool:
                    # 代理链路下，status 反映切换/IP 层的四类结局，浏览器结局写入 note
                    note_parts = []
                    if page_status == STATUS_SUCCESS:
                        note_parts.append(page_note)
                    else:
                        note_parts.append(f"浏览器访问失败({page_status}): {page_note}")
                    if switch_base_status == STATUS_SUCCESS_RETRY:
                        note_parts.append(
                            f"节点切换重试 {result['retries_used']} 次后成功"
                        )
                    if ip_query_error:
                        # 切换成功但出口 IP 查询失败：明确标注该异常
                        note_parts.append(
                            f"出口IP查询失败({ip_query_error})，已按切换成功的节点 {proxy_node} 照常访问"
                        )
                        if not exit_ip:
                            exit_ip = EXIT_IP_QUERY_FAILED
                    note = "；".join(note_parts)
                    status = switch_base_status
                else:
                    status, note = page_status, page_note

                if status == STATUS_SUCCESS:
                    print(f"  ✅ 访问成功 - 页面: {note}")
                else:
                    print(f"  ❌ 访问结束({status}): {note}")

                log_visit(visit_count, url, proxy_node, exit_ip, user_agent, screen_size, status, note)

            except Exception as e:
                error_msg = str(e)
                print(f"  ❌ 发生异常: {error_msg}")
                if use_proxy and proxy_pool:
                    # 浏览器异常也保留切换阶段的结局分类，避免与“没开代理”混淆
                    note_parts = [error_msg]
                    if switch_base_status == STATUS_SUCCESS_RETRY:
                        note_parts.append(
                            f"节点切换重试 {result['retries_used']} 次后成功"
                        )
                    if ip_query_error:
                        note_parts.append(f"出口IP查询失败({ip_query_error})")
                        if not exit_ip:
                            exit_ip = EXIT_IP_QUERY_FAILED
                    status = switch_base_status
                    log_visit(visit_count, url, proxy_node, exit_ip,
                              user_agent, screen_size, status, "；".join(note_parts))
                else:
                    log_visit(visit_count, url, proxy_node, exit_ip,
                              user_agent, screen_size, "EXCEPTION", error_msg)
            
            if max_visits == 0 or visit_count < max_visits:
                interval = get_interval(CONFIG["INTERVAL_MODE"], CONFIG["INTERVAL_MEAN"])
                mode_desc = "固定" if CONFIG["INTERVAL_MODE"] == "fixed" else "泊松分布"
                print(f"  ⏳ 等待 {interval:.1f} 秒（{mode_desc}）...")
                time.sleep(interval)
    
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    
    finally:
        if driver:
            try:
                driver.quit()
                print("🔒 已关闭浏览器")
            except:
                pass
        
        print(f"\n📊 总访问次数: {visit_count}")
        print(f"💾 日志: {log_file}")

if __name__ == "__main__":
    main()
