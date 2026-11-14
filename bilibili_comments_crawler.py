# -*- coding: utf-8 -*-
from traceback import print_exc
import asyncio
import httpx
import json
import threading
import time
from typing import List, Dict, Any, TypeAlias, Optional
from async_pool import AsyncPool
import os
from bilibili_api.credential import Credential
from config import *

JSON_TYPE: TypeAlias = Dict[str, Any]
# 同时最多运行多少个协程，可调，但不建议改太大，不要给 B 站造成负担
ASYNC_POOL_MAX_SIZE = 16
# 对话链的最短有效长度，超过此长度的对话才会被保存
MIN_DIALOG_LENGTH = 5
# oid 即为视频的 av 号，可以在某个视频的源码中找到（右键-查看源代码-搜索 oid）
VIDEO_OID = 2
# 爬取每一页评论（即视频下面）后的休眠时间，单位秒
SLEEP_TIME_ONE_PAGE = 0.1
# 爬取每一条评论后的休眠时间，单位秒
SLEEP_TIME_ONE_REPLY = 0.1
# 被 B 站限制访问时的休眠时间，单位秒
SLEEP_TIME_WHEN_LIMITED = 30
# 被 B 站限制访问时的最高重试次数，超过此次数则放弃
MAX_RETRY_TIME_WHEN_LIMITED = 3
# 当某条数据已经爬过时，是否跳过，而不是重新爬取（对于新视频，有可能评论会随时间更新；老视频一般无影响）
SKIP_EXISTED_DATA = True

# 爬取时的公共请求头
COMMON_HEADERS = {
    "Origin": "https://www.bilibili.com",
    "Authority": "api.bilibili.com",
    "Sec-Ch-Ua": '"Chromium";v="116", "Not)A;Brand";v="24", "Microsoft Edge";v="116"',
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
}

# https://nemo2011.github.io/bilibili-api/#/get-credential
if not (SESSDATA and BILI_JCT and BUVID3 and DEDE_USER_ID and AT_TIME_VALUE):
    raise ValueError(
        "请在 .env 中填写 SESSDATA, BILI_JCT, BUVID3, DEDE_USER_ID, AT_TIME_VALUE")

credential = Credential(sessdata=SESSDATA, bili_jct=BILI_JCT,
                        buvid3=BUVID3, dedeuserid=DEDE_USER_ID, ac_time_value=AT_TIME_VALUE)
print("credential: ", credential.get_cookies())
pool = AsyncPool(maxsize=ASYNC_POOL_MAX_SIZE)


def save_obj(obj: JSON_TYPE, filename: str):
    dirname = os.path.dirname(filename)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


class RateLimitExceededError(Exception):
    """触发 B 站 412 限流且重试次数用尽时抛出，用于替代直接退出进程"""


class FailedFloorManager:
    """
    记录因限流未抓完的楼层，落盘到 data/video_{oid}/failed_floors.json。
    重启后据此判断楼层是否需要重试：已失败过的楼层即使存在部分数据文件也重新爬取，
    补抓成功后从清单中移除。
    线程安全：AsyncPool 的回调运行在独立线程中，所有读写统一加锁。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: Dict[int, Dict[str, Any]] = {}

    def _load(self, oid: int) -> Dict[str, Any]:
        if oid in self._cache:
            return self._cache[oid]
        path = f"data/video_{oid}/failed_floors.json"
        floors: Dict[str, Any] = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for item in json.load(f):
                        floors[str(item["rpid"])] = item
            except (json.JSONDecodeError, KeyError, TypeError):
                print("失败清单 {} 内容损坏，将重新记录".format(path))
        self._cache[oid] = {"path": path, "floors": floors}
        return self._cache[oid]

    def _flush(self, entry: Dict[str, Any]):
        records = sorted(entry["floors"].values(),
                         key=lambda x: (x["page"], x["rpid"]))
        save_obj(records, entry["path"])

    def is_failed(self, oid: int, rpid: int) -> bool:
        """该楼层是否处于失败待重试状态"""
        with self._lock:
            return str(rpid) in self._load(oid)["floors"]

    def mark_failed(self, oid: int, page: int, rpid: int, reason: str):
        """记录一个未抓完的楼层（同一 rpid 重复记录会覆盖，不会产生重复条目）"""
        with self._lock:
            entry = self._load(oid)
            entry["floors"][str(rpid)] = {
                "oid": oid,
                "page": page,
                "rpid": rpid,
                "reason": reason,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._flush(entry)
        print("--- 已将 rpid 为 {:12d} 的楼层记入失败清单（{}）".format(rpid, reason))

    def clear(self, oid: int, rpid: int):
        """楼层补抓成功后，将其从失败清单移除"""
        with self._lock:
            entry = self._load(oid)
            if entry["floors"].pop(str(rpid), None) is not None:
                self._flush(entry)
                print("--- rpid 为 {:12d} 的楼层已补抓成功，从失败清单移除".format(rpid))

    def failed_count(self, oid: int) -> int:
        with self._lock:
            return len(self._load(oid)["floors"])


failed_floor_manager = FailedFloorManager()


async def get_html(url: str, params: Dict = None, headers: Dict = None, cookies: Dict = None, timeout: int = 30, client: httpx.AsyncClient = None, retry_time: int = 0):
    m_client = client
    try:
        if client is None:
            m_client = httpx.AsyncClient()
        # print("当前发送请求的 client ID: ", id(m_client))
        r = await m_client.get(url, timeout=timeout, params=params, headers=headers, cookies=cookies)
        if r.status_code == 412:
            if retry_time >= MAX_RETRY_TIME_WHEN_LIMITED:
                raise RateLimitExceededError(
                    "请求频率过高，引发 B 站限流，已重试 {} 次仍失败，放弃该请求: {}".format(retry_time, url))
            print(f"请求频率过高，引发 B 站限流了，我正在尝试休眠 {SLEEP_TIME_WHEN_LIMITED}s... （当前重试次数: {retry_time}）")
            await asyncio.sleep(SLEEP_TIME_WHEN_LIMITED)
            return await get_html(url, params, headers, cookies, timeout, client, retry_time + 1)
        r.raise_for_status()  # 如果状态不是200，引发HTTPError异常
        return r.text
    except Exception as e:
        print_exc()
        raise e
    finally:
        if client is None and m_client is not None:
            await m_client.aclose()


async def get_one_page(oid: int, pagination_str: str, client: httpx.AsyncClient = None):
    """获取范围：一个回复页"""
    params = {
        "type": 1,
        "oid": oid,
        "mode": 3,
        "pagination_str": '{"offset":"%s"}' % pagination_str.replace('"', r"\""),
    }
    # pagination_str: {"offset":"{\"type\":1,\"direction\":1,\"session_id\":\"1733963713068881\",\"data\":{}}"}
    url = "https://api.bilibili.com/x/v2/reply/main"
    # enc_wbi(params, await get_mixin_key())
    # print("-- url: ", url + "?" + urlencode(params))
    text = await get_html(url, params, COMMON_HEADERS, cookies=credential.get_cookies(), client=client)
    obj = json.loads(text)
    return obj


async def crawl_one_page_video(oid: int, page: int, pagination_str: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    爬取一个视频一页的评论，返回下一页的 pagination_str
    """

    print("-- 开始爬取视频 {} 的第 {} 页评论".format(oid, page))
    obj = await get_one_page(oid, pagination_str, client)

    if obj["code"] != 0:
        print("爬取视频 {} 的第 {} 页评论失败，原因是 {} (code={})".format(
            oid, page, obj["message"], obj["code"]))
        return None
    video_replies = obj["data"]["replies"]

    for root_reply in video_replies:
        rpid = root_reply["rpid"]
        saved_file_name =  f"data/video_{oid}/page_{page}/rpid_{rpid}_convs.json"
        # 已爬过且不在失败清单中的楼层才跳过；失败过的楼层需要重试并覆盖旧的部分数据
        if SKIP_EXISTED_DATA and os.path.exists(saved_file_name) and not failed_floor_manager.is_failed(oid, rpid):
            print("--- rpid 为 {:12d} 的评论已经爬过，跳过爬取".format(rpid))
            continue
        comment_replies, failed_pages = await get_reply(oid, rpid, root_reply["rcount"])
        conversations = build_conv_from_replies(root_reply, comment_replies)
        if conversations:
            save_obj(conversations, saved_file_name)
            print("--- 保存 rpid 为 {:12d} 的评论并构建对话完毕，共 {:6d} 条".format(
                rpid, len(conversations)))
        else:
            print("--- rpid 为 {:12d} 的评论没有符合要求的对话，跳过保存".format(rpid))
        if failed_pages:
            reason = "；".join("第 {} 页回复：{}".format(p, r) for p, r in failed_pages)
            failed_floor_manager.mark_failed(oid, page, rpid, reason)
        else:
            # 本次抓取完整，若之前在失败清单中则移除
            failed_floor_manager.clear(oid, rpid)
        await asyncio.sleep(SLEEP_TIME_ONE_REPLY)

    print("爬取到的第 {} 页，第一条评论是 {}".format(
        page, video_replies[0]["content"]["message"]))
    return obj["data"]["cursor"]["pagination_reply"].get("next_offset")


# https://api.bilibili.com/x/v2/reply/reply?csrf=9b261f0d10434bbefd17d7f4bd8247f2&oid=2&pn=1&ps=10&root=917945205&type=1
async def get_one_page_reply(oid: int, page: int, r_root: int):
    """获取一楼的一页评论"""
    params = {
        "type": 1,
        "oid": oid,
        "ps": 20,
        "pn": page,
        "root": r_root
    }
    url = "https://api.bilibili.com/x/v2/reply/reply"
    text = await get_html(url, params, COMMON_HEADERS)
    obj = json.loads(text)
    print("爬取 r_root: {}, page: {} 完毕".format(r_root, page))
    await asyncio.sleep(0.01)
    return obj["data"].get("replies") or []


async def get_reply(oid: int, r_root: int, total_count: int):
    """获取某个楼层的所有回复

    返回 (replies, failed_pages)：
    - replies 为已成功获取的回复（可能不完整）
    - failed_pages 为未拿到的分页列表 [(page, reason), ...]，为空表示该楼层抓取完整
    """
    total_page = total_count // 20 + 1
    replies = []
    failed_pages = []
    # AsyncPool 的回调运行在独立线程中，用 Event 在线程间传递“停止提交”信号
    rate_limited = threading.Event()

    def make_callback(page: int):
        def callback(future):
            try:
                replies.extend(future.result())
            except RateLimitExceededError as e:
                # 该分页限流重试耗尽：停止提交后续分页，并记录失败页
                rate_limited.set()
                failed_pages.append((page, str(e)))
            except Exception:
                print_exc()
        return callback

    for page in range(1, total_page + 1):
        if rate_limited.is_set():
            # 已有限流失败，停止提交该楼层的后续分页请求，剩余页一并记为未抓取
            for rest_page in range(page, total_page + 1):
                failed_pages.append((rest_page, "同楼层其他分页触发限流，未再提交请求"))
            break
        if page % 10 == 0:
            print("---- 正在爬取 r_root: {}, page: {}".format(r_root, page))
        pool.submit(get_one_page_reply(oid, page, r_root),
                    callback=make_callback(page))
    pool.wait()
    return replies, failed_pages


async def crawl_one_video(oid: int):
    """
    爬取一个视频的所有评论
    """
    print("- 开始爬取视频 {} 的评论".format(oid))
    url = "https://api.bilibili.com/x/v2/reply/count"
    params = {
        "type": 1,
        "oid": oid
    }
    text = await get_html(url, params, COMMON_HEADERS)
    obj = json.loads(text)
    total_page: int = obj["data"]["count"] // 20 + 1
    print("- 视频 {} 一共有 {} 页评论".format(oid, total_page))
    pagination = ''
    async with httpx.AsyncClient() as client:
        for page in range(1, total_page + 1):
            try:
                next_page = await crawl_one_page_video(oid, page, pagination_str=pagination, client=client)
            except RateLimitExceededError as e:
                # 评论列表页本身限流重试耗尽：已抓数据均已保存，提前结束本次运行，可重启续跑
                print("- 视频 {} 的第 {} 页评论列表请求触发限流且重试耗尽（{}），提前结束本次运行，可重启续跑".format(oid, page, e))
                break

            print("-- 爬取视频 {} 的第 {} 页评论完毕，下一页: {}".format(oid, page, next_page))
            if next_page is None:
                print("- 视频 {} 的评论爬取完毕".format(oid))
                break
            await asyncio.sleep(SLEEP_TIME_ONE_PAGE)
            pagination = next_page
    failed = failed_floor_manager.failed_count(oid)
    if failed:
        print("- 视频 {} 本次运行有 {} 个楼层未抓完，详见 data/video_{}/failed_floors.json，重启后将自动重试".format(oid, failed, oid))


def build_conv_from_replies(root_reply: JSON_TYPE, replies: List[JSON_TYPE]) -> List[List[Dict]]:
    if not replies:
        return []

    conv = []
    replies_dict = {}
    replies.insert(0, root_reply)

    # 将replies数据转换成字典形式
    for reply in replies:
        rpid = reply['rpid']
        parent = reply['parent']
        content = reply['content']['message']
        uname = reply['member']['uname']
        replies_dict[rpid] = {'parent': parent,
                              'content': content, 'uname': uname}

    conv_tree = {}

    # 构建对话树
    for reply_id, reply in replies_dict.items():
        parent_id = reply['parent']
        if parent_id in conv_tree:
            conv_tree[parent_id].append(reply_id)
        else:
            conv_tree[parent_id] = [reply_id]

    # print(conv_tree)
    longest_paths = []
    path = []

    # DFS遍历所有根节点到叶子节点的路径
    def dfs(node):
        nonlocal path
        path.append(node)
        if node not in conv_tree:
            # 当前节点是叶子节点，保存路径
            longest_paths.append(path.copy())
        else:
            for child in conv_tree[node]:
                dfs(child)
        path.pop()

    # 从每个根节点开始进行DFS搜索
    for root in conv_tree[0]:
        dfs(root)

    # 根据路径获取对话链
    longest_conversations = []
    for path in longest_paths:
        conversation = []
        for node in path:
            conversation.append(replies_dict[node])
        longest_conversations.append(conversation)

    conv = longest_conversations

    conversations = []
    for c in conv:
        # 过滤：
        # 1. 评论数小于5的对话
        if len(c) < MIN_DIALOG_LENGTH:
            continue
        temp = []
        for item in c:
            content = item['content']
            if content.startswith('回复 @'):
                content = content.split(':')[1]
            temp.append({
                'from': item['uname'],
                'value': content
            })
        conversations.append(temp)

    return conversations


async def refresh_cookie_if_necessary():
    need_refresh = await credential.check_refresh()
    if need_refresh:
        print("cookie 已过期，正在刷新")
        await credential.refresh()
        print("cookie 刷新成功")
    else:
        print("cookie 未过期，无需刷新")


async def main():
    await refresh_cookie_if_necessary()
    # oid 即为视频的 av 号，可以在某个视频的源码中找到
    await crawl_one_video(VIDEO_OID)


if __name__ == "__main__":
    asyncio.run(main())
