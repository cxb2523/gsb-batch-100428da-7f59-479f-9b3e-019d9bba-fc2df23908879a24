import random
import time
import requests
from urllib.parse import quote
import json
import pandas as pd
import os
import datetime
import re
import sys
import threading
import logging
from logging.handlers import TimedRotatingFileHandler
from urllib import parse
import urllib.parse
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
import webbrowser
import csv
from functools import partial
import subprocess
subprocess.Popen = partial(subprocess.Popen, encoding='utf-8')



class DouyinUserPosted:
    """抖音用户主页作品采集模块

    负责：
    1. 遍历用户链接列表
    2. 调用 aweme/post/ API 分页获取作品列表
    3. 解析作品信息（标题、标签、互动数据等）
    4. CSV输出和可选视频下载
    """

    # 作品列表接口
    POST_API = 'https://www.douyin.com/aweme/v1/web/aweme/post/'
    # 每页作品数（用于推算 max_cursor 与最大页数）
    PAGE_SIZE = 20
    # top_num<=0（不限制条数）时的安全最大页数，防止接口异常导致死循环
    MAX_PAGES_FALLBACK = 200
    # 断点续跑进度文件（按博主 sec_uid 记录成功/失败页）
    PROGRESS_FILE = 'user_post_progress.json'

    def __init__(self, user_link_list, top_num, down_tag, txt_msglist, logger, session=None):
        self.txt_msglist = txt_msglist
        self.logger = logger
        self.describe = []
        self.cookie_val = self.get_cookie()
        self.user_link_list = user_link_list
        self.top_num = int(top_num)
        self.down_tag = down_tag
        self.wait_sec = self.get_config_pub()
        # 请求会话：默认自建，测试可注入假 session
        self.session = session if session is not None else self._build_session()
        # 每页作品数 / 进度文件路径，实例属性便于测试调整
        self.page_size = self.PAGE_SIZE
        self.progress_file = self.PROGRESS_FILE
        # 当前时间戳
        now = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        # 保存文件名
        self.result_file = '抖音博主视频_{}.csv'.format(now)

    def tk_show(self, context):
        """线程安全的日志输出到Tkinter Text控件"""
        self.logger.info(context)
        self.txt_msglist.delete('1.0', 'end')
        self.describe.append(context)
        self.txt_msglist.insert('insert', '\n'.join(self.describe))
        self.txt_msglist.see("end")

    def _safe_showinfo(self, title, message):
        """线程安全弹窗：子线程通过 after 回到主线程，并加入提示音"""
        try:
            if threading.current_thread() is threading.main_thread():
                self.txt_msglist.bell()
                messagebox.showinfo(title, message)
            else:
                self.txt_msglist.after(0, lambda: (self.txt_msglist.bell(), messagebox.showinfo(title, message)))
        except Exception as e:
            self.logger.error(f'[safe_showinfo] {e}')

    def _safe_showerror(self, title, message):
        """线程安全弹窗：子线程通过 after 回到主线程，并加入提示音"""
        try:
            if threading.current_thread() is threading.main_thread():
                self.txt_msglist.bell()
                messagebox.showerror(title, message)
            else:
                self.txt_msglist.after(0, lambda: (self.txt_msglist.bell(), messagebox.showerror(title, message)))
        except Exception as e:
            self.logger.error(f'[safe_showerror] {e}')

    def trans_time(self, v_timestamp):
        """10位时间戳转换为时间字符串"""
        timeArray = time.localtime(v_timestamp)
        otherStyleTime = time.strftime("%Y-%m-%d %H:%M:%S", timeArray)
        return otherStyleTime

    def trans_top(self, v_str):
        """转换-是否置顶"""
        if str(v_str) == '1':
            return '是'
        elif str(v_str) == '0':
            return '否'
        else:
            return '未知'

    def get_cookie(self):
        """从 cookie.txt 读取cookie，验证是否已配置"""
        try:
            with open('cookie.txt', mode='r', encoding='utf8') as f:
                ck_str = f.read().strip()
                if '换成自己的' in ck_str:
                    self._safe_showerror("警告", "检测到未配置cookie！先用《cookie小工具》配置好cookie，再运行采集！")
                    sys.exit()
            self.tk_show(f'cookie.txt读取成功, 5s后开始采集！')
            time.sleep(5)
        except Exception as e:
            ck_str = ''
            self.tk_show('cookie读取失败! 先用《cookie小工具》配置好cookie，再运行采集！')
            self._safe_showerror("警告", "cookie读取失败! 先用《cookie小工具》配置好cookie，再运行采集！")
            print(str(e))
            sys.exit()
        ck_str = str(ck_str).strip()
        return ck_str

    def get_config_pub(self):
        """读取公开配置文件 config_pub.json，获取等待间隔"""
        try:
            with open('config_pub.json', 'r') as file:
                text = json.load(file)
            # 读取等待时长
            wait_sec = text['wait_sec']
            if wait_sec < 1:
                self.tk_show('\n等待时长需至少1秒，请重新配置！')
                exit(1)
            self.tk_show(f'\n读取config_pub成功, 等待间隔是:{wait_sec}s')
        except Exception as e:
            wait_sec = ''
            self.tk_show('\n读取config_pub失败！请检查config_pub.json')
            self.tk_show(str(e))
            exit(1)
        return wait_sec

    def init_csv(self, csv_header):
        """初始化csv文件（已存在且非空时不重复写表头，保证续跑不会产生重复表头）"""
        need_header = (not os.path.exists(self.result_file)) or os.path.getsize(self.result_file) == 0
        if need_header:
            with open(self.result_file, 'a+', encoding='utf_8_sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(csv_header)
            self.tk_show('csv初始化完成')
        else:
            self.tk_show('csv文件已存在，追加写入（不重复表头）')

    def _build_session(self):
        """构造带cookie的请求会话"""
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36',
            'Referer': 'https://www.douyin.com/',
            'Cookie': self.cookie_val,
        })
        return session

    @staticmethod
    def _extract_sec_uid(user_link):
        """从博主主页链接提取 sec_uid，如 https://www.douyin.com/user/MS4wLjAB..."""
        m = re.search(r'/user/([A-Za-z0-9_\-]+)', user_link)
        if m:
            return m.group(1)
        # 兼容直接传入 sec_uid 的情况
        m = re.fullmatch(r'(MS4wLj[A-Za-z0-9_\-]+)', user_link.strip())
        if m:
            return m.group(1)
        return ''

    def _load_progress(self):
        """读取断点续跑进度：{sec_uid: {'success_pages': [...], 'failed_pages': {页码: 原因}}}"""
        try:
            with open(self.progress_file, 'r', encoding='utf8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            pass
        except Exception as e:
            self.logger.warning(f'[progress] 进度文件损坏，重新记录: {e}')
        return {}

    def _save_progress(self, progress):
        """落盘断点续跑进度"""
        try:
            with open(self.progress_file, 'w', encoding='utf8') as f:
                json.dump(progress, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f'[progress] 进度写入失败: {e}')

    def _request_page(self, sec_uid, max_cursor):
        """请求一页作品列表，返回解析后的JSON dict。网络异常直接向上抛出。"""
        params = {
            'device_platform': 'webapp',
            'aid': '6383',
            'channel': 'channel_pc_web',
            'sec_user_id': sec_uid,
            'max_cursor': max_cursor,
            'count': self.page_size,
            'publish_video_strategy_type': 2,
            'locate_query': 'false',
            'show_live_replay_strategy': 1,
        }
        resp = self.session.get(self.POST_API, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _is_risk_control(data):
        """判断是否命中验证码/风控（仅在拿不到作品列表时判定，避免误伤正常页）"""
        if not isinstance(data, dict):
            return False
        if data.get('aweme_list'):
            return False
        # 接口明确返回验证码/验证相关字段
        blob = json.dumps(data, ensure_ascii=False).lower()
        if any(k in blob for k in ('captcha', 'verify', '验证码', '风控')):
            return True
        # 状态码非0且没有作品列表，视为风控拦截
        if data.get('status_code') not in (0, None):
            return True
        return False

    def _parse_aweme(self, aweme, page, sec_uid, user_link):
        """解析单个作品为17列CSV行"""
        author = aweme.get('author', {}) or {}
        statistics = aweme.get('statistics', {}) or {}
        video = aweme.get('video', {}) or {}
        author_sec_uid = author.get('sec_uid', '') or sec_uid
        # 视频标签（话题）
        tags = ' '.join('#{}'.format(t.get('hashtag_name', ''))
                       for t in (aweme.get('text_extra') or []) if t.get('hashtag_name'))
        # 视频时长（毫秒 -> 秒）
        duration_ms = video.get('duration', 0) or 0
        duration = '{}s'.format(round(duration_ms / 1000, 1))
        aweme_id = aweme.get('aweme_id', '')
        row = [
            page,                                            # 页码
            author.get('nickname', ''),                      # 作者昵称
            author.get('uid', ''),                           # uid
            author_sec_uid,                                  # sec_uid
            'https://www.douyin.com/user/' + author_sec_uid, # 作者链接
            author.get('follower_count', ''),                # 作者粉丝数
            aweme.get('desc', ''),                           # 视频标题
            tags,                                            # 视频标签
            'https://www.douyin.com/video/' + aweme_id,      # 视频链接
            self.trans_time(aweme.get('create_time', 0)),    # 发布时间
            duration,                                        # 视频时长
            self.trans_top(aweme.get('is_top', 0)),          # 是否置顶
            statistics.get('digg_count', 0),                 # 点赞数
            statistics.get('comment_count', 0),              # 评论数
            statistics.get('collect_count', 0),              # 收藏数
            statistics.get('recommend_count', 0),            # 推荐数
            statistics.get('share_count', 0),                # 转发数
        ]
        return row

    def _append_rows(self, rows):
        """把一页解析结果立即追加写入CSV"""
        with open(self.result_file, 'a+', encoding='utf_8_sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(rows)

    def _maybe_download(self, aweme, nickname):
        """下载开关打开时才发起视频下载（去水印：playwm -> play）"""
        if not self.down_tag:
            return
        try:
            play_addr = (aweme.get('video', {}) or {}).get('play_addr', {}) or {}
            url_list = play_addr.get('url_list') or []
            if not url_list:
                return
            video_url = url_list[0].replace('playwm', 'play')
            self.down_dy_video(nickname, video_url)
        except Exception as e:
            self.logger.error(f'[download] 视频下载失败: {e}')

    def down_dy_video(self, v_dir_name, douyin_video_url, video_url=''):
        """下载抖音视频mp4文件（带进度显示和去水印）"""
        # 原实现约40行：流式HTTP下载 + 进度条显示 + 异常处理
        pass

    def get_user_post(self):
        """采集用户主页作品列表（分页请求、逐页落盘、断点续跑）

        流程：
        1. 初始化CSV（17列，已存在则不重复表头）
        2. 遍历用户链接列表，从URL提取 sec_uid
        3. 按页请求 aweme/post/ API，每页解析后立即追加写入CSV
        4. 按博主记录成功页/失败页到进度文件：
           - 失败页记录可判别原因（网络异常/接口返回空/验证码或风控），继续采集剩余页
           - 重跑同一链接时，已成功页跳过，只补采失败页与未完成页
        5. 下载开关关闭时不发起任何视频下载请求
        """
        # 初始化csv
        column_list = ['页码', '作者昵称', 'uid', 'sec_uid', '作者链接', '作者粉丝数',
                       '视频标题', '视频标签', '视频链接', '发布时间', '视频时长', '是否置顶',
                       '点赞数', '评论数', '收藏数', '推荐数', '转发数']
        self.init_csv(csv_header=column_list)

        progress = self._load_progress()

        for user_link in self.user_link_list:
            user_link = str(user_link).strip()
            if not user_link:
                continue
            sec_uid = self._extract_sec_uid(user_link)
            if not sec_uid:
                self.tk_show(f'无法从链接提取sec_uid，跳过: {user_link}')
                continue
            record = progress.setdefault(sec_uid, {'success_pages': [], 'failed_pages': {}})
            record.setdefault('success_pages', [])
            record.setdefault('failed_pages', {})
            self.tk_show(f'\n开始采集博主: {user_link} (sec_uid={sec_uid})')

            # 最大页数：top_num>0 时按每页 page_size 条推算；否则用安全上限
            if self.top_num > 0:
                max_pages = max(1, -(-self.top_num // self.page_size))  # 向上取整
            else:
                max_pages = self.MAX_PAGES_FALLBACK

            collected = 0
            for page in range(1, max_pages + 1):
                if 0 < self.top_num <= collected:
                    break
                # 断点续跑：已成功页跳过，不重复请求、不重复写入
                if page in record['success_pages']:
                    self.tk_show(f'第{page}页此前已成功，跳过（断点续跑）')
                    continue
                max_cursor = (page - 1) * self.page_size

                # 1) 请求一页，网络异常记录原因后继续剩余页
                try:
                    data = self._request_page(sec_uid, max_cursor)
                except Exception as e:
                    reason = f'网络异常: {e}'
                    record['failed_pages'][str(page)] = reason
                    self._save_progress(progress)
                    self.tk_show(f'第{page}页请求失败[{reason}]，继续采集剩余页')
                    continue

                # 2) 验证码/风控
                if self._is_risk_control(data):
                    reason = '验证码/风控: 接口返回验证拦截，请过验证码或更换cookie'
                    record['failed_pages'][str(page)] = reason
                    self._save_progress(progress)
                    self.tk_show(f'第{page}页被拦截[{reason}]，继续采集剩余页')
                    continue

                # 3) 空作品列表
                aweme_list = data.get('aweme_list') if isinstance(data, dict) else None
                if not aweme_list:
                    reason = '接口返回空: aweme_list为空'
                    record['failed_pages'][str(page)] = reason
                    self._save_progress(progress)
                    self.tk_show(f'第{page}页无数据[{reason}]，继续采集剩余页')
                    continue

                # 4) 解析并立即逐页落盘
                if self.top_num > 0:
                    aweme_list = aweme_list[:self.top_num - collected]
                rows = [self._parse_aweme(a, page, sec_uid, user_link) for a in aweme_list]
                self._append_rows(rows)
                collected += len(rows)

                # 5) 可选视频下载（开关关闭时不会进入）
                nickname = rows[0][1] if rows else ''
                for aweme in aweme_list:
                    self._maybe_download(aweme, nickname)

                # 6) 记录成功页，清除该页历史失败原因
                record['success_pages'].append(page)
                record['failed_pages'].pop(str(page), None)
                self._save_progress(progress)
                self.tk_show(f'第{page}页采集成功，写入{len(rows)}条（本博主累计{collected}条）')

                if not data.get('has_more'):
                    self.tk_show(f'第{page}页为最后一页，本博主采集结束')
                    break
                time.sleep(self.wait_sec)

            # 博主级汇总：失败页及原因在界面与日志中可判别
            if record['failed_pages']:
                fail_desc = '; '.join(f'第{p}页({r})' for p, r in sorted(record['failed_pages'].items()))
                self.tk_show(f'博主 {sec_uid} 采集结束，失败页待重跑: {fail_desc}')
            else:
                self.tk_show(f'博主 {sec_uid} 采集结束，全部页成功')

        self.tk_show('==软件作者：马哥python说==')
        self._safe_showinfo('提示', f'用户作品采集完成，结果保存于: {self.result_file}')
