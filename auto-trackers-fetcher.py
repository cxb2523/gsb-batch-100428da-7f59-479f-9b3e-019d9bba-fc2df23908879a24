#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tracker 列表自动更新脚本（v2，改进版）。

相较 v1 的主要改进：
- 数据安全：质检后无有效结果时保留旧文件，不再用空列表覆盖；
- HTTP 探测改为 GET + 最小 announce 参数 + 复用 Session/UA（HEAD 常被服务器拒绝）；
- 文件写入改为"临时文件 + os.replace"原子替换，中断不会损坏旧文件；
- 多线程各线程独立 requests.Session（Session 非线程安全）；
- DNS 解析改用 getaddrinfo（支持 IPv6-only 主机），并加缓存；
- 拉取/探测重试改为指数退避 + 随机抖动；URL 拉取结果按 URL 缓存（跨组复用）；
- 新增命令行参数：--output-dir / --no-validate / --dry-run / --log-level / --max-workers；
- 失败时返回非零退出码，便于 cron/CI 感知；
- 有效数骤降保护：本轮有效数比旧文件少 20%（且 >5 条）时拒绝写入；
- 探测结果按 URL 缓存，BEST/ALL 重叠的条目每轮只探一次；
- 只收 udp:// http:// https://，源里的 ws/wss 条目会被丢弃（当前源约 1 条）；
- 一轮的资源（拉取/探测/DNS 缓存 + 连接池）收进 RunResources，一轮 = 一个实例；
- 解析产出 Tracker；质检判定与 Tracker 解析各自是不碰 I/O 的纯函数/module；
- 中断不白跑：Ctrl-C 打断某一组时，该组已确认有效的条目照常走保护规则落盘，
  已完成的组不回滚，未开始的组不再发起拉取/探测，并输出逐组结局摘要、退出码 130。

注意：缓存与连接池是 RunResources 的实例状态——一轮更新 = 一个实例；想重来就换一个实例，实例之间互不串味。
"""

import argparse
import logging
import os
import random
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from ipaddress import ip_address
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple
from urllib.parse import urlparse

try:
    import requests
except ImportError as e:  # pragma: no cover
    raise SystemExit("缺少依赖 requests，请先运行: pip install -r requirements.txt") from e

# --- 配置 ---

# 重试配置
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0          # 指数退避基数（秒），实际延迟 = 基数 * 2^(n-1) + 抖动
FETCH_TIMEOUT = (5, 15)         # 拉取超时 (连接, 读取)
PROBE_TIMEOUT = (3, 5)          # 探测超时 (连接, 读取)

# Tracker 源 URL
BEST_TRACKER_URLS = [
    "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_best.txt",
    "https://cf.trackerslist.com/best.txt",
    "https://newtrackon.com/api/stable",
]

ALL_TRACKER_URLS = [
    "https://raw.githubusercontent.com/ngosang/trackerslist/master/trackers_all.txt",
    "https://cf.trackerslist.com/all.txt",
    "https://newtrackon.com/api/stable",
]

BEST_OUTPUT_FILE = "tracker.txt"
ALL_OUTPUT_FILE = "all_trackers.txt"


class TrackerGroup(NamedTuple):
    """一组 Tracker 的全部定义；加一组 = 加一行数据，不改逻辑。"""

    name: str
    urls: List[str]
    output_file: str


GROUPS: List[TrackerGroup] = [
    TrackerGroup("BEST", BEST_TRACKER_URLS, BEST_OUTPUT_FILE),
    TrackerGroup("ALL", ALL_TRACKER_URLS, ALL_OUTPUT_FILE),
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# HTTP tracker 的最小 announce 探测参数（无 info_hash 时多数 tracker 返回 400，
# 这通常意味着 tracker 是存活的；404 才说明端点/路径不存在）
ANNOUNCE_QUERY = (
    "info_hash=%1a%2b%3c%4d%5e%6f%70%71%72%73%74%75%76%77%78%79%7a%0a%0b%0c%0d"
    "&peer_id=-TR3000-abcdefghijklmnop&port=6881&uploaded=0&downloaded=0&left=0"
    "&compact=1&numwant=0&event=started"
)

logger = logging.getLogger("tracker-fetcher")

# --- Tracker 文本解析与规范化 ---

TRACKER_SCHEMES = ("udp", "http", "https")  # 唯一一份协议白名单；ws/wss 不在其列


class Tracker(NamedTuple):
    """一条 tracker：协议、主机、规范化 URL。解析是确定性的，相等即同一条。"""

    scheme: str
    host: str
    url: str


def parse_tracker(line: str) -> Optional[Tracker]:
    """一行文本 → Tracker：小写 scheme/host、去尾部斜杠；非法返回 None。"""
    line = line.strip()
    if not line:
        return None
    try:
        parsed = urlparse(line)
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme not in TRACKER_SCHEMES or not parsed.hostname:
        return None
    normalized = parsed._replace(scheme=scheme, netloc=parsed.netloc.lower()).geturl()
    # 仅去掉 URL 末尾的斜杠（不影响路径内的 /announce 等）
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return Tracker(scheme, parsed.hostname, normalized)


def parse_tracker_lines(text: str) -> Set[Tracker]:
    """解析文本中的 Tracker 行：跳过空行/注释行，并做规范化去重。"""
    trackers: Set[Tracker] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tracker = parse_tracker(line)
        if tracker:
            trackers.add(tracker)
    return trackers


# --- 一轮的资源：缓存 + 连接池 ---

class Verdict(NamedTuple):
    """一次质检的结论：有效吗、真的探测过吗、给人看的理由。"""

    valid: bool
    probed: bool  # False = 只查了域名（udp），没验 announce
    reason: str


class RunResources:
    """一轮更新用到的缓存与连接池：调用方建一个、用完 close()。

    fetch / verdict / resolved 都是读穿缓存（未命中就调用传进来的计算函数并记住结果），
    session 是线程隔离的懒建连接，close 关掉本轮建过的所有连接。
    不变量：一个实例 = 一轮更新；想重来就换一个实例，实例之间互不串味。
    写进缓存也算接口事实：拉取只记成功（非空）结果，失败的留待下一轮重试。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fetched: Dict[str, Set[Tracker]] = {}  # 源 URL -> 拉到的列表
        self._verdicts: Dict[str, Verdict] = {}  # tracker url -> 质检结论
        self._hosts: Dict[str, Tuple[bool, str]] = {}  # 主机名 -> DNS 结论
        self._local = threading.local()  # 每线程独立 Session（Session 非线程安全）
        self._sessions: List[requests.Session] = []

    def fetch(self, url: str, fetch_one: Callable[[str], Set[Tracker]]) -> Set[Tracker]:
        """源 URL -> 拉到的 tracker：命中就用，未命中调 fetch_one。"""
        with self._lock:
            if url in self._fetched:
                return self._fetched[url]
        trackers = fetch_one(url)
        if trackers:  # 只记成功，失败的留待下次重试
            with self._lock:
                self._fetched[url] = trackers
        return trackers

    def verdict(self, url: str, check_one: Callable[[str], Verdict]) -> Verdict:
        """tracker url -> 质检结论；两组重叠的条目因此只探一次。"""
        with self._lock:
            if url in self._verdicts:
                return self._verdicts[url]
        result = check_one(url)
        with self._lock:
            self._verdicts[url] = result
        return result

    def resolved(self, hostname: str, resolve_one: Callable[[str], Tuple[bool, str]]) -> Tuple[bool, str]:
        """主机名 -> DNS 结论（是否含公网 IP + 说明）。"""
        with self._lock:
            if hostname in self._hosts:
                return self._hosts[hostname]
        result = resolve_one(hostname)
        with self._lock:
            self._hosts[hostname] = result
        return result

    def session(self) -> requests.Session:
        """当前线程的 Session（懒建）。"""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = USER_AGENT
            self._local.session = session
            with self._lock:
                self._sessions.append(session)
        return session

    def close(self) -> None:
        """关闭本轮建过的所有 Session。"""
        with self._lock:
            for session in self._sessions:
                session.close()
            self._sessions.clear()


# --- 质检策略（纯规则，不碰 I/O）---

def retryable(exc: requests.RequestException) -> bool:
    """这个异常值不值得退避重试：网络错/5xx/429 值得，4xx（404 等）是永久错误。"""
    status = exc.response.status_code if exc.response is not None else None
    return status is None or status >= 500 or status == 429


def retry_delay(attempt: int) -> float:
    """第 attempt 次失败后的等待秒数：指数退避 + 抖动。"""
    return RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 1)


def verdict_for(status: Optional[int]) -> Verdict:
    """HTTP 探测的状态码 → 质检结论；None 表示网络失败。"""
    if status is None:
        return Verdict(False, True, "连接失败/超时")
    if status == 404:
        return Verdict(False, True, "HTTP 404(端点不存在)")
    if status >= 500:
        return Verdict(False, True, "服务器错误(%d)" % status)
    return Verdict(True, True, "存活(HTTP %d)" % status)


# --- 拉取（带指数退避重试 + 结果缓存）---

def abandon_executor(executor: ThreadPoolExecutor, futures) -> None:
    """中断时放弃剩余任务：取消还没开始的，不等正在跑的（requests 自带超时）。

    不用 with 语句收尾，是因为它的 shutdown(wait=True) 会把 Ctrl-C 卡到所有
    在途请求超时为止；这里只放弃、不等待。
    """
    cancelled = sum(1 for future in futures if future.cancel())
    executor.shutdown(wait=False)
    if cancelled:
        logger.warning("中断：已取消 %d 个尚未开始的任务", cancelled)


def fetch_trackers_from_url(url: str, resources: RunResources,
                            max_retries: int = MAX_RETRIES) -> Set[Tracker]:
    """拉取单源 Tracker 列表，指数退避 + 抖动重试；最终失败返回空集合（不抛）。"""
    for attempt in range(1, max_retries + 1):
        try:
            response = resources.session().get(url, timeout=FETCH_TIMEOUT)
            response.raise_for_status()
            trackers = parse_tracker_lines(response.text)
            logger.info("[拉取成功] %s -> %d 条", url, len(trackers))
            return trackers
        except requests.RequestException as e:
            if attempt < max_retries and retryable(e):
                delay = retry_delay(attempt)
                logger.warning("[重试 %d/%d] %s -> %s（%.1f 秒后重试）", attempt, max_retries, url, e, delay)
                time.sleep(delay)
            else:
                logger.error("[拉取失败] %s -> %s", url, e)
                return set()
    return set()


def fetch_all_trackers(urls: List[str], resources: RunResources) -> Set[Tracker]:
    """多线程拉取全部源并去重；结果按源 URL 记进本轮资源（跨组复用）。"""
    if not urls:
        return set()

    def fetch_cached(url: str) -> Set[Tracker]:
        return resources.fetch(url, lambda source: fetch_trackers_from_url(source, resources))

    all_trackers: Set[Tracker] = set()
    results: Dict[str, Set[Tracker]] = {}
    max_workers = max(1, min(len(urls), 10))
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures: Dict[object, str] = {}
    try:
        futures = {executor.submit(fetch_cached, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            result = future.result()
            results[url] = result
            all_trackers.update(result)
        executor.shutdown(wait=True)
    except BaseException as exc:
        # 中断时不再等剩余拉取（requests 自带超时，线程会自行收尾）；
        # 已拉到的结果挂在异常上，--no-validate 的组还能拿它当有效条目落盘。
        try:
            exc.raw_trackers = set(all_trackers)
        except (AttributeError, TypeError):  # 属性只读的异常对象；不影响主流程
            pass
        abandon_executor(executor, futures)
        raise

    original_count = sum(len(v) for v in results.values())
    if original_count > len(all_trackers):
        logger.info("去重: 原始 %d 条 -> 去重后 %d 条", original_count, len(all_trackers))
    return all_trackers


# --- 有效性校验 ---

def resolve_global(hostname: str, resources: RunResources) -> Tuple[bool, str]:
    """解析主机名是否含公网 IP，返回 (是否含公网IP, 说明)；结论记进本轮资源。"""
    return resources.resolved(hostname, _resolve_uncached)


def _resolve_uncached(hostname: str) -> Tuple[bool, str]:
    """真正执行 DNS 解析（IPv4/IPv6）。"""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        addresses = sorted({info[4][0] for info in infos})
        for ip_str in addresses:
            try:
                if ip_address(ip_str).is_global:
                    return True, ip_str
            except ValueError:
                continue
        return False, "无非公网IP(%s)" % "/".join(addresses)
    except socket.gaierror:
        return False, "DNS解析失败(死链)"
    except OSError as e:
        return False, "DNS解析异常(%s)" % e


def probe_http_tracker(tracker_url: str, resources: RunResources) -> Optional[int]:
    """GET + 最小 announce 参数探测 HTTP tracker，返回状态码；网络失败返回 None。"""
    sep = "&" if "?" in tracker_url else "?"
    probe_url = tracker_url + sep + ANNOUNCE_QUERY
    try:
        response = resources.session().get(probe_url, timeout=PROBE_TIMEOUT, stream=True, allow_redirects=True)
        try:
            # 读取少量响应体以复用连接（避免 tracker 返回大体积页面被完整下载）
            if response.raw:
                response.raw.read(4096)
        except Exception:
            pass
        finally:
            response.close()
        return response.status_code
    except requests.RequestException:
        return None


def validate_tracker_url(tracker: Tracker, resources: RunResources) -> Verdict:
    """验证一条 tracker（结论记进本轮资源，BEST/ALL 重叠的只探一次）。"""
    return resources.verdict(tracker.url, lambda url: _validate_tracker_url_uncached(tracker, resources))


def _validate_tracker_url_uncached(tracker: Tracker, resources: RunResources) -> Verdict:
    """真正执行验证：DNS + HTTP 探测（协议与主机在解析时已验过，这里不再重解析）。"""
    ok, info = resolve_global(tracker.host, resources)
    if not ok:
        return Verdict(False, True, info)

    if tracker.scheme in ("http", "https"):
        return verdict_for(probe_http_tracker(tracker.url, resources))

    # shortcut: udp 只查 DNS，不验 announce；要真验需实现 BT 的 UDP 握手
    return Verdict(True, False, "UDP(未探测)")


def filter_valid_trackers(trackers: Set[Tracker], resources: RunResources,
                          max_workers: Optional[int] = None) -> Set[Tracker]:
    """多线程质检：筛掉无效的 tracker，返回有效的那些。

    被中断时二次抛出异常，但异常对象上挂着 `.valid_trackers`（中断前已确认有效的
    条目集合），调用方据此仍可落盘——见 save_partial_trackers。
    """
    if not trackers:
        return set()
    valid_trackers: Set[Tracker] = set()
    invalid_count = 0
    unprobed_count = 0
    workers = max_workers or min(len(trackers), 20)
    workers = max(1, workers)

    executor = ThreadPoolExecutor(max_workers=workers)
    futures: Dict[object, Tracker] = {}
    completed = 0
    total = 0
    try:
        futures = {executor.submit(validate_tracker_url, tracker, resources): tracker for tracker in trackers}
        total = len(futures)
        for future in as_completed(futures):
            completed += 1
            verdict = future.result()
            if verdict.valid:
                valid_trackers.add(futures[future])
                if not verdict.probed:
                    unprobed_count += 1
            else:
                invalid_count += 1
            if completed % 50 == 0 or completed == total:
                logger.info("质检进度: %d/%d (已过滤: %d)", completed, total, invalid_count)
        executor.shutdown(wait=True)
    except BaseException as exc:
        # 先记下中断前的结果，再原样抛出（KeyboardInterrupt 不能被吞掉）。
        # 注意挂的是副本：中断落盘会读它，别让后续线程改动同一个集合。
        try:
            exc.valid_trackers = set(valid_trackers)
            exc.completed = completed
            exc.total = total
        except (AttributeError, TypeError):  # 少数异常对象属性只读；不影响主流程
            pass
        abandon_executor(executor, futures)
        raise

    logger.info("质检完毕: 总计 %d 条, 有效 %d 条（其中 UDP 未探测 %d 条，只确认了域名）, 过滤 %d 条",
                len(trackers), len(valid_trackers), unprobed_count, invalid_count)
    return valid_trackers


# --- 文件读写 ---

def read_old_trackers(file_path: str) -> Set[Tracker]:
    """读取旧 Tracker 文件。"""
    if not os.path.exists(file_path):
        return set()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            # 与写出口径一致：读进来也解析一遍，手改过的旧文件才不会有假增量
            return {t for t in (parse_tracker(line) for line in f) if t}
    except (IOError, OSError) as e:
        logger.warning("读取旧文件失败 %s: %s（按空处理）", file_path, e)
        return set()


def save_trackers(file_path: str, trackers: Set[Tracker]) -> bool:
    """原子写入 Tracker 文件（临时文件 + os.replace）；按 url 排序，与解析顺序无关。"""
    try:
        urls = sorted(t.url for t in trackers)
        content = "\n".join(urls) + ("\n" if urls else "")
        dir_path = os.path.dirname(os.path.abspath(file_path)) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".trackers-", suffix=".tmp", dir=dir_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            os.replace(tmp_path, file_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        logger.info("写入 %s 完成: %d 条", file_path, len(trackers))
        return True
    except (IOError, OSError) as e:
        logger.error("写入失败 %s: %s", file_path, e)
        return False


# --- 分组处理 ---

class UpdatePlan(NamedTuple):
    """写盘判断的结果：一次算清，让调用方只剩「落日志 + 落盘」。"""

    write: bool  # 是否写文件
    ok: bool  # 是否算成功（决定退出码）；write 为真时必为真
    logs: Tuple[Tuple[int, str], ...]  # (logging 级别, 文案)，按序落


def plan_update(
    raw_trackers: Set[Tracker],
    valid_trackers: Set[Tracker],
    old_trackers: Set[Tracker],
    dry_run: bool,
) -> UpdatePlan:
    """决定这一组写不写、算不算成功（纯函数：不联网、不碰磁盘、不记日志）。

    调用方把 plan.logs 落成日志、按 plan.write 落盘即可。不变量：write 为真时 ok 必为真。
    """
    if not raw_trackers:
        return UpdatePlan(False, False, ((logging.ERROR, "未获取到任何数据，保留旧文件不变"),))
    if not valid_trackers:
        return UpdatePlan(False, False, ((logging.ERROR, "质检后无有效 Tracker，保留旧文件不变"),))

    logs: List[Tuple[int, str]] = []
    if old_trackers:
        added = valid_trackers - old_trackers
        removed = old_trackers - valid_trackers
        if added:
            logs.append((logging.INFO, "新增 %d 个 Tracker" % len(added)))
        if removed:
            logs.append((logging.INFO, "移除/失效 %d 个 Tracker" % len(removed)))
        if not added and not removed:
            logs.append((logging.INFO, "列表无变化，跳过写入"))
            return UpdatePlan(False, True, tuple(logs))
    else:
        logs.append((logging.INFO, "首次生成: %d 条" % len(valid_trackers)))

    # shortcut: 20% 只是防网络抖动的阈值；源真被大规模清理时也会拦下，需删旧文件后重跑
    if old_trackers and len(old_trackers) - len(valid_trackers) > max(5, len(old_trackers) // 5):
        logs.append((logging.ERROR, "有效数从 %d 骤降到 %d（超过 20%%），保留旧文件不变；确认源确已清理请删除旧文件后重跑"
                                  % (len(old_trackers), len(valid_trackers))))
        return UpdatePlan(False, False, tuple(logs))

    if dry_run:
        logs.append((logging.INFO, "干跑模式（--dry-run），不写入文件"))
        return UpdatePlan(False, True, tuple(logs))

    return UpdatePlan(True, True, tuple(logs))


class GroupOutcome:
    """一组在一轮里的结局；前四种就是摘要里逐组打印的内容。

    第五种只出现在「已拿到有效条目，但保护规则/干跑/写盘失败让它没落盘」时——
    这种情况既不是「无有效数据」，也不该谎称「已保存」。
    """

    DONE = "已完成"
    SAVED_AFTER_INTERRUPT = "中断后已保存"
    NO_DATA_AFTER_INTERRUPT = "中断且无有效数据"
    NOT_STARTED = "未开始"
    KEPT_AFTER_INTERRUPT = "中断但未落盘"


class Interrupted(Exception):
    """一次中断：打断哪一组、该组中断后的结局、原始异常（留给 main 收口成 130）。

    用异常而不是返回值，是因为「中断」必须在调用栈上冒泡；但它不是致命错误——
    被打断的那一组已经按保护规则落过盘了。
    """

    def __init__(self, group: str, outcome: str, error: BaseException) -> None:
        super().__init__("%s: %s" % (group, outcome))
        self.group = group
        self.outcome = outcome
        self.error = error


def process_tracker_group(
    name: str,
    urls: List[str],
    output_file: str,
    resources: RunResources,
    validate: bool = True,
    dry_run: bool = False,
    max_workers: Optional[int] = None,
    fetch: Callable[[List[str], RunResources], Set[Tracker]] = fetch_all_trackers,
    check: Callable[[Set[Tracker], RunResources, Optional[int]], Set[Tracker]] = filter_valid_trackers,
) -> Tuple[str, bool]:
    """处理单组 Tracker：拉取 → 质检 → 判断（plan_update）→ 落日志 → 写盘。

    返回 (结局, 是否成功)。结局是 GroupOutcome 里的常量：
    - 正常跑完是 DONE（成功与否看 plan / 写盘）；
    - 拉取或质检抛 KeyboardInterrupt 时，不再向外抛：已确认有效的条目照常走
      plan_update 的保护规则落盘，然后标记为 SAVED_AFTER_INTERRUPT；没有有效
      条目则旧文件原样保留，标记为 NO_DATA_AFTER_INTERRUPT；有有效条目却被
      保护规则/干跑/写盘失败拦下，标记为 KEPT_AFTER_INTERRUPT；被中断的一组
      一律不算成功，且整轮退出码为 130。其他异常照旧向上抛。
    注入的两个 callable 的契约：预期失败返回空集/子集、不抛异常——由 plan_update 的
    no-data / no-valid 两条分支接住；真正的中断才向上抛。
    """
    logger.info("=== 处理 %s ===", name)
    raw_trackers: Set[Tracker] = set()
    try:
        raw_trackers = fetch(urls, resources)
        if raw_trackers and validate:
            logger.info("%s 开始质检过滤...", name)
            valid_trackers = check(raw_trackers, resources, max_workers)
        else:
            valid_trackers = raw_trackers
    except KeyboardInterrupt as exc:
        # 中断不算这一组的「正常完成」：已到手的结果照常落盘，然后由 main 收口
        if getattr(exc, "valid_trackers", None) is not None:  # 质检阶段被打断
            valid_trackers = set(exc.valid_trackers)
        elif not validate:  # 拉取阶段被打断：--no-validate 时拉到的就是有效条目
            raw_trackers = set(getattr(exc, "raw_trackers", ()))
            valid_trackers = set(raw_trackers)
        else:
            valid_trackers = set()
        outcome = save_partial_trackers(name, output_file, raw_trackers, valid_trackers, dry_run)
        logger.warning("%s 处理被打断（Ctrl-C）：%s", name, outcome)
        raise Interrupted(name, outcome, exc)

    plan = plan_update(raw_trackers, valid_trackers, read_old_trackers(output_file), dry_run)
    for level, text in plan.logs:
        logger.log(level, "%s %s", name, text)
    if not plan.write:
        return GroupOutcome.DONE, plan.ok
    return GroupOutcome.DONE, save_trackers(output_file, valid_trackers)


def save_partial_trackers(name: str, output_file: str, raw_trackers: Set[Tracker],
                          valid_trackers: Set[Tracker], dry_run: bool) -> str:
    """中断后为这一组落盘：已确认有效的条目走与正常一轮完全相同的保护规则。

    返回该组的结局字符串。没有有效条目时什么都不写（旧文件逐字节保持原样）；
    空结果保护、骤降保护、无变化跳过、干跑、原子写入都与正常一轮一致。
    """
    if not valid_trackers:
        logger.warning("%s 中断时尚未确认任何有效条目，旧文件保持原样", name)
        return GroupOutcome.NO_DATA_AFTER_INTERRUPT

    plan = plan_update(raw_trackers, valid_trackers,
                       read_old_trackers(output_file), dry_run)
    for level, text in plan.logs:
        logger.log(level, "%s [中断后] %s", name, text)
    if not plan.write:
        # 无变化（磁盘上已经是这份内容）算「已保存」；被骤降保护拦下或干跑没写，如实说未落盘
        return GroupOutcome.SAVED_AFTER_INTERRUPT if plan.ok and not dry_run else GroupOutcome.KEPT_AFTER_INTERRUPT
    if save_trackers(output_file, valid_trackers):
        return GroupOutcome.SAVED_AFTER_INTERRUPT
    return GroupOutcome.KEPT_AFTER_INTERRUPT


# --- 主入口 ---

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tracker 列表自动更新脚本：拉取 → 质检 → 增量对比 → 原子写入",
    )
    parser.add_argument("--output-dir", default=".", help="输出目录（默认当前目录）")
    parser.add_argument("--no-validate", action="store_true", help="跳过有效性质检（仅合并去重）")
    parser.add_argument("--dry-run", action="store_true", help="干跑：拉取/质检但不写文件")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日志级别（默认 INFO）")
    parser.add_argument("--max-workers", type=int, default=None,
                        help="质检线程数上限（默认 min(条目数, 20)）")
    args = parser.parse_args(argv)
    if args.max_workers is not None and args.max_workers < 1:
        parser.error("--max-workers 必须 >= 1")
    return args


def main(argv: Optional[List[str]] = None, groups: Optional[List[TrackerGroup]] = None,
         fetch: Optional[Callable[[List[str], RunResources], Set[Tracker]]] = None,
         check: Optional[Callable[[Set[Tracker], RunResources, Optional[int]], Set[Tracker]]] = None) -> int:
    """跑一轮：逐组拉取/质检/落盘；被中断时保存已完成的组并输出逐组摘要。

    中断的三种来源（本组内 KeyboardInterrupt、线程里未回收的 KeyboardInterrupt、
    组与组之间的信号）都在这里收口，统一返回 130，绝不伪装成正常结束。
    groups / fetch / check 是给测试注入用的；生产路径一律用默认值。
    """
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper()),
                        format="%(levelname)s: %(message)s")
    os.makedirs(args.output_dir, exist_ok=True)

    injected = {}
    if fetch is not None:
        injected["fetch"] = fetch
    if check is not None:
        injected["check"] = check

    resources = RunResources()
    group_list = GROUPS if groups is None else groups
    outcomes: Dict[str, str] = {}
    ok = True
    interrupted = False
    current: Optional[str] = None

    logger.info("=== Tracker 更新开始 ===")
    try:
        for group in group_list:
            current = group.name
            try:
                outcome, group_ok = process_tracker_group(
                    group.name, group.urls, os.path.join(args.output_dir, group.output_file), resources,
                    validate=not args.no_validate, dry_run=args.dry_run, max_workers=args.max_workers,
                    **injected)
            except Interrupted as exc:
                outcomes[group.name] = exc.outcome
                interrupted = True
                break
            outcomes[group.name] = outcome
            if not group_ok:
                ok = False
    except KeyboardInterrupt:
        # 组与组之间、或落盘/比对阶段漏上来的中断：这一组不谎称完成，也不谎称已保存
        interrupted = True
        if current is not None and current not in outcomes:
            outcomes[current] = GroupOutcome.KEPT_AFTER_INTERRUPT
    finally:
        resources.close()

    if interrupted:
        logger.error("=== 本轮被 Ctrl-C 中断，退出码 130 ===")
    else:
        logger.info("=== 全部完成（%s）===", "成功" if ok else "部分失败")
    log_summary(group_list, outcomes)
    if interrupted:
        return 130
    return 0 if ok else 1


def log_summary(groups: List[TrackerGroup], outcomes: Dict[str, str]) -> None:
    """逐组结局摘要；没跑到（或没跑完）的组按「未开始」计。"""
    lines = ["=== 本轮结局摘要 ==="]
    for group in groups:
        outcome = outcomes.get(group.name, GroupOutcome.NOT_STARTED)
        lines.append("  %s: %s" % (group.name, outcome))
    logger.info("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
