#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雨课堂视频自动播放器（STU 版 / Edge 兼容版）

思路与源码参考：https://github.com/LetMeFly666/YuketangAutoPlayer
文章：汕头大学雨课堂自动播放器（YuketangAutoPlayer For STU）

和原版的区别（都是为了让它在你这台电脑上直接能跑）：
  1. 你电脑没装 Chrome，只有 Edge —— 脚本会自动挑一个能用的浏览器（Chrome 优先，没有就用 Edge），
     driver 由 selenium 自带的 Selenium Manager 自动下载，不需要手动找 chromedriver。
  2. 配置从 config.ini 读，避免把 sessionid 写死在代码里；首次运行自动生成模板。
  3. 支持扫码登录，登录成功后把 sessionid 自动写回 config.ini，下次不用再扫。
  4. 想不出画面 / 找不到视频时，用 --dump 把页面结构存下来，方便排查。

用法：
  python main.py              # 开始播放
  python main.py --list       # 只列出没看完的视频，不播放
  python main.py --login      # 强制扫码登录并保存 sessionid
  python main.py --dump       # 保存调试信息到 debug/
  python main.py --self-test  # 只测试浏览器能不能启动
"""

from __future__ import annotations

import argparse
import configparser
import os
import random
import re
import sys
import time
import traceback
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


# 控制台中文/符号在 Windows 下偶尔会炸，能改就改成 utf-8
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# 打包成 exe 后 __file__ 指向解压出来的临时目录，配置和 debug 要跟着 exe 走
if getattr(sys, 'frozen', False):
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent
CONFIG_NAME = 'config.ini'

# 这些是页面上代表「视频」和「未开放」的 class 片段，随雨课堂版本不同而不同，
# 原版就是靠这几个字符串认元素的，这里两边都保留。
ICON_VIDEO = 'icon--shipin'   # 旧版列表：视频图标
ICON_LOCK = 'icon--suo'       # 锁图标：本节没开放
NEW_UI_FLAG = 'leaf-flag'     # 新版（www.yuketang.cn）列表：类型标签
CLASS_OLD = 'leaf-detail'     # 旧版列表行
CLASS_NEW = 'study-unit'      # 新版列表行

# 第三种界面：雨课堂「学习空间」(ai-workspace/lms-graph) 的课程树，2025 年这版在用
CLASS_SPACE = 'leaf-item'         # 每一行
SPACE_TAG = 'leaf-item-tag'       # 类型标签：视频 / 图文 / 作业
SPACE_TITLE = 'leaf-item-title'   # 标题
SPACE_STATUS = 'leaf-item-status' # 状态区
# 状态区里的图标（2026-11 实测）：
#   icon-yuanquangou-mianzhuang  = 对勾，已完成
#   status-pie                   = 半圆，看了一半（必须继续播）
SPACE_DONE_ICON = 'yuanquangou'   # 用子串匹配，兼容 icon-yuanquangou / 带后缀的版本
SPACE_PIE_ICON = 'status-pie'     # 半圆：看了一半，不能当成已完成


def say(msg=''):
    print(msg, flush=True)


def load_config():
    """读 config.ini；不存在就生成模板并退出。"""
    path = HERE / CONFIG_NAME
    if not path.exists():
        path = Path.cwd() / CONFIG_NAME
    if not path.exists():
        path = HERE / CONFIG_NAME
        create_config_template(path)
        say(f'没有找到配置文件，已生成模板：{path}')
        say('请先编辑这个文件，填好 course_url 和 cookie，然后重新运行。')
        return None

    cfg = configparser.ConfigParser()
    cfg.read(path, encoding='utf-8')
    if not cfg.has_section('Settings'):
        say(f'配置文件格式不对：{path} 里缺少 [Settings] 段')
        return None
    return cfg, path


def create_config_template(path: Path):
    cfg = configparser.ConfigParser()
    cfg['Settings'] = {
        'headless': 'false',
        'browser': 'auto',
        'course_url': 'https://在此填写你的课程URL',
        'cookie': '',
        'playback_rate': '2',
        'restart_from_start': 'true',
        'implicitly_wait': '20',
        'max_minutes_per_video': '180',
    }
    with open(path, 'w', encoding='utf-8') as f:
        f.write('; 雨课堂视频自动播放器 配置文件\n')
        f.write(';\n')
        f.write('; headless        : true=不弹浏览器窗口(后台跑) / false=弹出窗口(推荐)\n')
        f.write(';                   扫码登录必须用 false。\n')
        f.write('; browser         : auto / chrome / edge，默认 auto 自动挑\n')
        f.write('; course_url      : 课程「学习内容」页面的地址，必须 https:// 开头\n')
        f.write(';                   例：https://stu.yuketang.cn/pro/lms/XXXXXXXX/12345678/studycontent\n')
        f.write('; cookie          : sessionid 的值；也可以把整串 cookie 粘进来。留空=每次扫码登录\n')
        f.write('; playback_rate   : 播放倍速，2 表示 2 倍速\n')
        f.write('; restart_from_start : true=没看完的视频从 0 开始播；false=从上次停的位置续播\n')
        f.write('; implicitly_wait : 等页面元素的最长秒数\n')
        f.write('; max_minutes_per_video : 单个视频最多等多少分钟，超时就跳过\n\n')
        cfg.write(f)


def save_cookie_to_config(path: Path, sessionid: str):
    """把 sessionid 写回 config.ini（没变化就不写）。

    按行处理，只动 `cookie = ...` 那一行：
      - 注释、其它键、CRLF 换行全部原样保留；
      - 绝不能用 `\\s*` 之类的正则去匹配，`\\s` 会把换行一起吃掉，
        把值写到下一行去（这个坑踩过一次，直接把配置文件写坏）。
    """
    sessionid = (sessionid or '').strip()
    if len(sessionid) < 16:      # 明显不是真 sessionid，别污染配置
        return
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return

    lines = text.splitlines(keepends=True)
    in_settings = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            in_settings = (stripped == '[Settings]')
            continue
        if not in_settings:
            continue
        m = re.match(r'^([ \t]*)cookie[ \t]*=[ \t]*(.*)$', line.rstrip('\r\n'))
        if not m:
            continue
        if m.group(2).strip() == sessionid:
            return
        ending = '\r\n' if line.endswith('\r\n') else '\n'
        lines[i] = f'{m.group(1)}cookie = {sessionid}{ending}'
        break
    else:
        # 没有 cookie 这一行：插到 [Settings] 段末尾
        for i, line in enumerate(lines):
            if line.strip() == '[Settings]':
                ending = '\r\n' if line.endswith('\r\n') else '\n'
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith('['):
                    j += 1
                lines.insert(j, f'cookie = {sessionid}{ending}')
                break
        else:
            lines.append(f'\n[Settings]\ncookie = {sessionid}\n')

    try:
        path.write_text(''.join(lines), encoding='utf-8')
        say(f'已把 sessionid 写回 {path.name}，下次不用再登录了。')
    except Exception as e:
        say(f'[警告] sessionid 写回失败（不影响本次播放）：{e}')


# ---------------------------------------------------------------- 浏览器

def build_driver(browser: str, headless: bool):
    """按优先级尝试创建浏览器；返回 (driver, 浏览器名)。"""
    errors = []

    def chrome_options():
        o = webdriver.ChromeOptions()
        apply_common_options(o, headless)
        return o

    def edge_options():
        o = webdriver.EdgeOptions()
        apply_common_options(o, headless)
        return o

    def apply_common_options(o, headless_):
        if headless_:
            o.add_argument('--headless=new')
        o.add_argument('--autoplay-policy=no-user-gesture-required')  # 允许自动播放
        o.add_argument('--mute-audio')                                # 浏览器级静音
        o.add_argument('--disable-gpu')
        o.add_argument('--no-first-run')
        o.add_argument('--no-default-browser-check')
        o.add_argument('--window-size=1280,900')
        o.add_experimental_option('excludeSwitches', ['enable-logging'])

    # 默认先 Edge：Windows 10/11 自带，driver 也只有十几 MB。
    # 没装 Edge 再试 Chrome（Chrome 没装的话 Selenium 会下载 400MB+ 的测试版，很慢）。
    order = [browser] if browser in ('chrome', 'edge') else ['edge', 'chrome']
    for name in order:
        maker = chrome_options if name == 'chrome' else edge_options
        try:
            say(f'正在启动 {name} ...')
            # Selenium Manager 会自动下载匹配版本的 driver（需要联网，第一次会慢一点）
            d = (webdriver.Chrome if name == 'chrome' else webdriver.Edge)(options=maker())
            if not headless:
                try:
                    d.maximize_window()
                except WebDriverException:
                    pass
            say(f'{name} 启动成功。')
            return d, name
        except Exception as e:
            errors.append(f'{name}: {type(e).__name__}: {str(e).splitlines()[0][:160]}')
            say(f'{name} 启动失败，换下一个。')

    msg = '\n  '.join(errors) if errors else '没有可用浏览器'
    raise RuntimeError(
        '启动浏览器失败：\n  ' + msg +
        '\n\n请确认本机能正常打开网页，并且能联网（第一次要下载 driver）。\n'
        '也可以在 config.ini 里把 browser 改成 edge 或 chrome 指定一个。'
    )


# ---------------------------------------------------------------- cookie

_COOKIE_ATTRS = {'path', 'domain', 'expires', 'max-age', 'secure', 'httponly', 'samesite', 'priority'}


def parse_cookie(raw: str) -> dict:
    """
    兼容三种写法：
      1. 只有 sessionid 的值
      2. sessionid=xxxx
      3. 整串 cookie：a=1; b=2; sessionid=xxxx
    """
    raw = (raw or '').strip().strip('"').strip("'")
    if not raw:
        return {}
    if '=' not in raw:
        return {'sessionid': raw}

    pairs = {}
    for part in raw.replace('\n', ';').split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        k, v = part.split('=', 1)
        k, v = k.strip(), v.strip().strip('"')
        if k.lower() in _COOKIE_ATTRS or not k:
            continue
        pairs[k] = v
    return pairs


def install_cookies(driver, cookies: dict):
    for name, value in cookies.items():
        try:
            driver.add_cookie({'name': name, 'value': value, 'path': '/'})
        except Exception as e:
            say(f'[警告] cookie {name} 写入失败：{str(e).splitlines()[0][:80]}')


# ---------------------------------------------------------------- 页面

def home_of(course_url: str) -> str:
    m = re.match(r'(https?)://([^/]+)', course_url.strip(), re.I)
    if not m:
        raise ValueError(f'course_url 看起来不对，必须以 https:// 开头：{course_url}')
    return f'{m.group(1).lower()}://{m.group(2)}/'


def looks_like_login(driver) -> bool:
    """
    是否处在未登录状态。

    几种登录页都见过：
      - 老版：跳 /pro/portal/home/，右上角有个 .login-btn
      - 新版（www.yuketang.cn/web）：标题「雨课堂网页版-登录」，直接显示二维码
    注意：老版的登录框 <div class="login-dialog"> 在 DOM 里一直存在，只是隐藏着，
    所以判断 .login-btn 必须用 is_displayed()，否则会把已登录误判成未登录、
    进而把好用的 cookie 删掉。
    """
    url = (driver.current_url or '').lower()
    if '/login' in url or 'portal/home' in url:
        return True
    if re.search(r'//[^/]+/web(\?|$|/)', url):     # www.yuketang.cn/web?next=...
        return True
    try:
        if '登录' in (driver.title or ''):
            return True
    except WebDriverException:
        pass
    try:
        btn = driver.find_element(By.CLASS_NAME, 'login-btn')
        if btn.is_displayed():
            return True
    except (NoSuchElementException, WebDriverException):
        pass
    return False


def wait_login_by_qrcode(driver, home: str, timeout: float = 600) -> bool:
    """等用户在浏览器窗口里完成登录（扫码/手机号都行），以 sessionid 出现为准。"""
    say('')
    say('=' * 56)
    say('需要登录：请在弹出的浏览器窗口里登录（微信扫码 / 手机号都行）。')
    say(f'最多等 {int(timeout // 60)} 分钟，登录成功后会自动开始，不用管这个黑窗口。')
    say('=' * 56)

    login_url = driver.current_url
    # 失效的 sessionid 会让登录页的二维码接口报「二维码获取失败」，所以必须先清掉
    driver.delete_all_cookies()
    time.sleep(1)
    try:
        driver.get(login_url)
    except WebDriverException:
        driver.get(home)
    time.sleep(2.5)

    # 老界面要手动点一下「登录」才会弹二维码
    try:
        btn = driver.find_element(By.CLASS_NAME, 'login-btn')
        if btn.is_displayed():
            btn.click()
            time.sleep(2)
    except (NoSuchElementException, WebDriverException):
        pass

    # 二维码万一没出来，点一下「重新获取」
    try:
        if '二维码获取失败' in driver.find_element(By.TAG_NAME, 'body').text:
            for e in driver.find_elements(By.XPATH, "//*[contains(text(),'重新获取')]"):
                try:
                    e.click()
                    say('二维码重新获取中……')
                    break
                except WebDriverException:
                    continue
    except WebDriverException:
        pass

    deadline = time.time() + timeout
    last_hint = time.time()
    told = False
    while time.time() < deadline:
        if driver.get_cookie('sessionid'):
            say('登录成功。')
            time.sleep(1.5)
            return True

        if not told:
            try:
                if driver.find_elements(By.CSS_SELECTOR, 'img.logma, .login-dialog'):
                    say('窗口里已经显示登录框/二维码，扫一下就行。')
                    told = True
            except WebDriverException:
                pass

        if time.time() - last_hint > 30:
            say('还在等你登录……')
            last_hint = time.time()
        time.sleep(0.8)
    say('等待登录超时。')
    return False


def find_list(driver, timeout: float):
    """
    找课程列表行，并判断是哪种界面。

    三种都见过：
      space —— 新版「学习空间」课程树（leaf-item），2025 年 www.yuketang.cn 在用
      new   —— 新版列表（study-unit + span.leaf-flag）
      old   —— 旧版列表（leaf-detail + i.icon--shipin）
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        space = driver.find_elements(By.CLASS_NAME, CLASS_SPACE)
        if space:
            return 'space', space

        has_new = bool(driver.find_elements(By.CLASS_NAME, CLASS_NEW))
        has_old = bool(driver.find_elements(By.CLASS_NAME, CLASS_OLD))
        has_flag = bool(driver.find_elements(By.CSS_SELECTOR, f'span.{NEW_UI_FLAG}'))

        if has_new and has_flag:
            return 'new', driver.find_elements(By.CLASS_NAME, CLASS_NEW)
        if has_old:
            return 'old', driver.find_elements(By.CLASS_NAME, CLASS_OLD)
        if has_new:
            return 'new', driver.find_elements(By.CLASS_NAME, CLASS_NEW)
        time.sleep(1)
    return None, []


UI_NAMES = {
    'space': '学习空间(leaf-item)',
    'new': '新版(study-unit)',
    'old': '旧版(leaf-detail)',
}

_SPEED_HINTED = [False]   # 倍速没点中的提示只打一次，免得 100 多个视频刷屏

# 按「文本内容」找倍速菜单项。要点：
#   - 大小写无关（页面上显示 2.00X，DOM 里可能是 2.00x，靠 CSS 转大写）
#   - 用 textContent 而不是 text，元素嵌在 <span> 里也能找到
#   - querySelectorAll 能拿到 display:none 的隐藏菜单
#   - 返回最里层那个（不会被外层容器“顺带命中”）
_JS_FIND_SPEED_ITEM = r"""
var want = arguments[0];
var all = document.querySelectorAll('*');
var hits = [];
for (var i = 0; i < all.length; i++) {
  var e = all[i];
  var t = (e.textContent || '').trim().toUpperCase().replace(/\s+/g, '');
  if (t === want) { hits.push(e); }
}
if (!hits.length) { return null; }
for (var i = 0; i < hits.length; i++) {
  var outer = false;
  for (var j = 0; j < hits.length; j++) {
    if (i !== j && hits[i].contains(hits[j])) { outer = true; break; }
  }
  if (!outer) { return hits[i]; }
}
return hits[0];
"""

# 找控制栏上显示当前倍速（如 1.00X）的那个元素
_JS_FIND_SPEED_BTN = r"""
var re = /^[0-9]\.[0-9]{2}X$/;
var all = document.querySelectorAll('*');
var best = null;
for (var i = 0; i < all.length; i++) {
  var e = all[i];
  var own = '';
  for (var j = 0; j < e.childNodes.length; j++) {
    if (e.childNodes[j].nodeType === 3) { own += e.childNodes[j].textContent; }
  }
  own = own.trim().toUpperCase().replace(/\s+/g, '');
  if (re.test(own)) { return e; }
}
return best;
"""


def _js_find_speed_item(driver, rate: float):
    try:
        return driver.execute_script(_JS_FIND_SPEED_ITEM, f'{rate:.2f}X')
    except WebDriverException:
        return None


def _js_find_speed_btn(driver):
    try:
        return driver.execute_script(_JS_FIND_SPEED_BTN)
    except WebDriverException:
        return None


# 收集页面上所有「像倍速控件」的小元素，用于排查菜单结构
_JS_COLLECT_SPEED = r"""
var out = [];
var all = document.querySelectorAll('span,div,p,li,a,b,em,i');
for (var i = 0; i < all.length; i++) {
  var e = all[i];
  var t = (e.textContent || '').replace(/\s+/g, ' ').trim();
  if (!t || t.length > 14) { continue; }
  var u = t.toUpperCase();
  if (u.indexOf('X') < 0 && t.indexOf('倍') < 0) { continue; }
  var r = e.getBoundingClientRect();
  out.push(e.tagName + ' | class=' + (e.className || '(无)') + ' | text=' + t +
           ' | 可见=' + (r.width > 0 && r.height > 0));
  if (out.length >= 50) { break; }
}
return out;
"""


def _dump_speed_debug(driver):
    """倍速菜单点不中时，把播放器页面结构和候选元素存下来，方便远程对准。"""
    out_dir = HERE / 'debug'
    try:
        out_dir.mkdir(exist_ok=True)
        (out_dir / 'player.html').write_text(driver.page_source, encoding='utf-8')
        info = driver.execute_script(_JS_COLLECT_SPEED) or []
        (out_dir / 'speed_candidates.txt').write_text(
            '\n'.join(str(x) for x in info), encoding='utf-8')
        say('  已把播放器结构存到 debug/player.html 和 debug/speed_candidates.txt。')
    except Exception:
        pass


def _wake_controls(driver):
    """鼠标不动时播放器会隐藏控制栏，先把鼠标移到画面上让控件出来。"""
    try:
        v = driver.find_element(By.TAG_NAME, 'video')
        ActionChains(driver).move_to_element(v).perform()
        time.sleep(0.6)
    except WebDriverException:
        pass


def expand_all(driver, ui: str):
    """
    学习空间里的章节可以折叠，折叠起来的小节虽然还在 DOM 里，但点不动。
    这里把所有没展开的章节都点开（幂等，重复点不会出错）。
    """
    if ui != 'space':
        return
    for _ in range(4):
        clicked = 0
        for ic in driver.find_elements(By.CSS_SELECTOR, '.expand-icon'):
            try:
                cls = ic.get_attribute('class') or ''
                if 'is-expanded' in cls:
                    continue
                driver.execute_script('arguments[0].click();', ic)
                clicked += 1
            except WebDriverException:
                pass
        if not clicked:
            return
        time.sleep(0.4)


def _src_key(src: str) -> str:
    """
    片源的「身份」。签名参数（auth_key/token 之类）每次都变，要去掉；
    普通参数要保留，否则不同的视频会被当成同一个。
    """
    if not src:
        return ''
    base, _, query = src.partition('?')
    q = query.lower()
    if any(w in q for w in ('auth_key', 'authkey', 'token', 'sign', 'expire')):
        return base
    return src


def video_src(driver) -> str:
    try:
        return driver.execute_script(
            'var v=document.querySelector("video"); return v ? (v.currentSrc || v.src || "") : "";')
    except WebDriverException:
        return ''


def current_rate(driver) -> float:
    try:
        return driver.execute_script(
            'var v=document.querySelector("video"); return v ? (v.playbackRate || 1) : 0;') or 0
    except WebDriverException:
        return 0


def set_speed(driver, rate: float, verbose: bool = True) -> bool:
    """
    用播放器自带的倍速菜单选倍速。

    为什么不能只改 JS：雨课堂的播放器（xt_video_player）自己存着倍速状态，
    会把 <video>.playbackRate 不断改回它内部的值，所以光设 playbackRate 会被覆盖，
    菜单上还显示 1.00X。必须点它自己的「2.00X」那一条。

    菜单项（li）平时是隐藏的，但 Vue 的点击处理不看可见性，所以先直接 JS 点；
    不行再悬停展开、用真实鼠标点。
    """
    label = f'{rate:.2f}X'

    def ok():
        return abs(current_rate(driver) - rate) < 0.01

    # 先关掉我们自己的 JS 兜底，等播放器把它真实的倍速写回来。
    # 否则「rate 已经是 2」只是我们自己设的，菜单其实还停在 1.00X。
    try:
        driver.execute_script('if (window.__lp) window.__lp.enforce = false;')
    except WebDriverException:
        pass
    time.sleep(0.7)
    if ok():
        if verbose:
            say(f'倍速已是 {label}。')
        return True

    # 播放器菜单只有 0.50 / 1.00 / 1.25 / 1.50 / 2.00 这几档。
    # 想要更快（或别的奇数倍速）就直接走「锁定 playbackRate」那条路，
    # 别在这儿白点一通菜单。
    if not any(abs(rate - r) < 0.001 for r in (0.5, 1.0, 1.25, 1.5, 2.0)):
        if verbose and not _SPEED_HINTED[0]:
            _SPEED_HINTED[0] = True
            say(f'  播放器菜单最高只到 2.00X，{rate:g}x 用直接锁定倍速的方式设置。')
        return ok()

    def try_click(el, real=False):
        try:
            if real:
                ActionChains(driver).move_to_element(el).click().perform()
            else:
                driver.execute_script('arguments[0].click();', el)
        except WebDriverException:
            return False
        time.sleep(0.8)
        return ok()

    def synthetic_click(el):
        """派发一整套指针/鼠标事件：有的播放器监听的是 mousedown 而不是 click。"""
        try:
            driver.execute_script(
                "var e=arguments[0];"
                "['pointerdown','mousedown','pointerup','mouseup','click'].forEach(function(t){"
                "  try {"
                "    var Ev = (t.indexOf('pointer') === 0) ? PointerEvent : MouseEvent;"
                "    e.dispatchEvent(new Ev(t, {bubbles:true, cancelable:true, view:window}));"
                "  } catch (x) {}"
                "});", el)
            time.sleep(0.8)
            return ok()
        except WebDriverException:
            return False

    def visible(el):
        try:
            return el.is_displayed()
        except WebDriverException:
            return False

    def force_show(el):
        """菜单是被 CSS 隐藏的，直接把它和它的父级显示出来，之后就能真实点击了。"""
        try:
            driver.execute_script(
                "var e=arguments[0];"
                "for (var k=0; k<3 && e; k++) {"
                "  try { e.style.display='block'; e.style.visibility='visible'; e.style.opacity='1'; } catch (x) {}"
                "  e = e.parentElement;"
                "}", el)
        except WebDriverException:
            pass

    def done_msg():
        if verbose:
            say(f'倍速已设为 {label}。')

    # ① 菜单项可能已经在 DOM 里（只是 display:none）
    item = _js_find_speed_item(driver, rate)
    if item is not None and try_click(item):
        done_msg()
        return True

    # ② 让控制栏显示出来，找到「1.00X」那个按钮，悬停 / 点开菜单
    _wake_controls(driver)
    btn = _js_find_speed_btn(driver)
    if btn is not None:
        try:
            driver.execute_script('arguments[0].scrollIntoView({block:"center"});', btn)
        except WebDriverException:
            pass
        try:
            ActionChains(driver).move_to_element(btn).perform()
            time.sleep(0.8)
        except WebDriverException:
            pass

        item = _js_find_speed_item(driver, rate)
        if item is not None:
            if visible(item) and try_click(item, real=True):
                done_msg()
                return True
            if synthetic_click(item):
                done_msg()
                return True

        # 真实点击打开菜单
        try:
            ActionChains(driver).move_to_element(btn).click().perform()
            time.sleep(0.8)
        except WebDriverException:
            pass

        item = _js_find_speed_item(driver, rate)
        if item is not None:
            if visible(item) and try_click(item, real=True):
                done_msg()
                return True
            if synthetic_click(item):
                done_msg()
                return True

        # 菜单还是没出来就强制显示，再用真实鼠标点
        if item is not None:
            force_show(item)
            time.sleep(0.4)
            if try_click(item, real=True):
                done_msg()
                return True
            if synthetic_click(item):
                done_msg()
                return True

    if verbose and not _SPEED_HINTED[0]:
        _SPEED_HINTED[0] = True
        say('  [提示] 播放器菜单点不中，已改为直接锁定倍速，播放器上的标签也会一并改掉。')
        _dump_speed_debug(driver)
    return ok()


def _classes_of(el, tag: str) -> str:
    try:
        return el.find_element(By.TAG_NAME, tag).get_attribute('class') or ''
    except (NoSuchElementException, StaleElementReferenceException):
        return ''


def _space_text(el, cls: str) -> str:
    try:
        return el.find_element(By.CLASS_NAME, cls).text.strip()
    except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
        return ''


def is_video(el, ui: str) -> bool:
    """判断这一个列表行是不是视频，并且没上锁。"""
    try:
        icons = el.find_elements(By.TAG_NAME, 'i')
    except (StaleElementReferenceException, WebDriverException):
        return False
    for i in icons:
        if ICON_LOCK in (i.get_attribute('class') or ''):
            return False

    if ui == 'space':
        return '视频' in _space_text(el, SPACE_TAG)

    if ui == 'new':
        try:
            span = el.find_element(By.CSS_SELECTOR, f'span.{NEW_UI_FLAG}')
        except (NoSuchElementException, WebDriverException):
            return False
        return '视频' in span.text.strip()

    cls = _classes_of(el, 'i')
    return ICON_VIDEO in cls


def is_done(el, ui: str = None) -> bool:
    """
    只认「确定已完成」的情况。

    特别注意：状态区里**有图标不等于已完成** —— 看了一半的视频也有图标（半圆），
    那种是「待继续看」，必须当成未完成。所以这里不能用「有子元素就算完成」。
    """
    if ui == 'space':
        try:
            st = el.find_element(By.CLASS_NAME, SPACE_STATUS)
            for k in st.find_elements(By.XPATH, './/*'):
                cls = k.get_attribute('class') or ''
                if SPACE_DONE_ICON in cls:     # 对勾 = 已完成
                    return True
                if SPACE_PIE_ICON in cls:      # 半圆 = 看了一半
                    return False
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            pass

    try:
        text = el.text or ''
    except StaleElementReferenceException:
        return True
    return ('已完成' in text) or ('截止' in text)


def space_status_icons(el) -> str:
    """把这一行的状态图标 class 拼出来（排查用）。"""
    try:
        st = el.find_element(By.CLASS_NAME, SPACE_STATUS)
        kids = st.find_elements(By.XPATH, './/*')
        if not kids:
            return '(空)'
        names = sorted({(k.get_attribute('class') or '').strip() for k in kids})
        return ' | '.join(n for n in names if n) or '(空class)'
    except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
        return '(无状态区)'


def header_has_title(driver, title: str) -> bool:
    """播放器标题是不是这一节（用来判断「它其实已经是当前播放的那一节」）。"""
    title = (title or '').strip()
    if not title:
        return False
    try:
        nodes = driver.find_elements(By.XPATH, f"//*[normalize-space(text())='{title}']")
    except WebDriverException:
        return False
    for n in nodes:
        try:
            if not n.is_displayed():
                continue
            if n.find_elements(
                    By.XPATH,
                    "./ancestor::*[contains(@class,'leaf-item') or contains(@class,'nav-item')]"):
                continue
            return True
        except (StaleElementReferenceException, WebDriverException):
            continue
    return False


def header_marked_done(driver, title: str) -> bool:
    """
    播放器标题旁边是否显示「已完成」。

    做法：找到与这一节同名的标题元素（排除左侧目录里的同名项），再往上几层看
    同一块里有没有「已完成」。这是目录勾选图标识别失灵时的第二道保险。
    """
    title = (title or '').strip()
    if not title:
        return False
    try:
        nodes = driver.find_elements(By.XPATH, f"//*[normalize-space(text())='{title}']")
    except WebDriverException:
        return False

    for n in nodes:
        try:
            if not n.is_displayed():
                continue
            # 跳过左侧目录里的同名标题
            if n.find_elements(
                    By.XPATH,
                    "./ancestor::*[contains(@class,'leaf-item') or contains(@class,'nav-item')]"):
                continue
            cur = n
            for _ in range(5):
                cur = cur.find_element(By.XPATH, '..')
                if '已完成' in (cur.text or ''):
                    return True
        except (StaleElementReferenceException, WebDriverException):
            continue
    return False


def label_of(el, ui: str) -> str:
    if ui == 'space':
        t = _space_text(el, SPACE_TITLE)
        if t:
            return t
    try:
        return (el.text or '').strip().replace('\n', ' ')
    except StaleElementReferenceException:
        return ''


# 一次 JS 读完整棵树的状态，比逐个元素问快得多，也方便统计「完成/半圆」
_JS_READ_ROWS = r"""
var out = [];
var rows = document.querySelectorAll('.leaf-item');
for (var i = 0; i < rows.length; i++) {
  var el = rows[i];
  var tagEl = el.querySelector('.leaf-item-tag');
  var titleEl = el.querySelector('.leaf-item-title');
  var stEl = el.querySelector('.leaf-item-status');
  var cls = '';
  if (stEl) {
    var kids = stEl.querySelectorAll('*');
    for (var k = 0; k < kids.length; k++) { cls += ' ' + (kids[k].className || ''); }
  }
  out.push({
    tag: tagEl ? (tagEl.textContent || '').trim() : '',
    title: titleEl ? (titleEl.textContent || '').trim() : '',
    status: cls.trim()
  });
}
return out;
"""


def read_space_rows(driver):
    try:
        return driver.execute_script(_JS_READ_ROWS) or []
    except WebDriverException:
        return []


def space_counts(rows):
    """返回 (视频数, 已完成数, 半圆数, 图标统计)。"""
    videos = [r for r in rows if '视频' in (r.get('tag') or '')]
    done = sum(1 for r in videos if SPACE_DONE_ICON in (r.get('status') or ''))
    partial = sum(1 for r in videos if SPACE_PIE_ICON in (r.get('status') or ''))
    stats = {}
    for r in videos:
        key = (r.get('status') or '(空)')[:70]
        stats[key] = stats.get(key, 0) + 1
    return len(videos), done, partial, stats


def is_partial(el, ui: str) -> bool:
    """是不是「看了一半」的（学习空间里是 status-pie 半圆）。"""
    if ui != 'space':
        return False
    try:
        st = el.find_element(By.CLASS_NAME, SPACE_STATUS)
        for k in st.find_elements(By.XPATH, './/*'):
            if SPACE_PIE_ICON in (k.get_attribute('class') or ''):
                return True
    except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
        pass
    return False


def list_pending(driver, ui: str, els):
    """
    返回 [(元素, 文字, 唯一键)]，只包含「视频 + 未完成」。

    唯一键用「元素在整棵树里的序号」：这门课里有同名小节（比如两个"第十三讲"），
    只用标题当键会让它们互相顶掉。序号在同一次运行里是稳定的。

    排序：看了一半的（半圆）排最前面 —— 先把残留清干净，再按目录顺序补新的。
    同一组内保持页面上的原有顺序（sort 是稳定排序）。
    """
    raw = []
    for idx, el in enumerate(els):
        try:
            if is_video(el, ui) and not is_done(el, ui):
                raw.append((0 if is_partial(el, ui) else 1, idx, el, label_of(el, ui)))
        except (StaleElementReferenceException, WebDriverException):
            continue
    raw.sort(key=lambda x: x[0])
    return [(el, text, str(idx)) for _, idx, el, text in raw]


def dump_debug(driver, ui: str, els):
    """把页面结构和元素 class 存下来，方便远程排查「找不到视频」。"""
    out_dir = HERE / 'debug'
    out_dir.mkdir(exist_ok=True)
    html_path = out_dir / 'page.html'
    html_path.write_text(driver.page_source, encoding='utf-8')

    lines = [f'最终 URL: {driver.current_url}', f'识别到的界面: {ui}', f'列表行数量: {len(els)}', '']
    for idx, el in enumerate(els[:60]):
        try:
            text = (el.text or '').strip().replace('\n', ' / ')
        except StaleElementReferenceException:
            text = '<stale>'
        icons = []
        try:
            icons = [(i.get_attribute('class') or '') for i in el.find_elements(By.TAG_NAME, 'i')]
        except Exception:
            pass
        span_cls = _classes_of(el, 'span')
        lines.append(f'[{idx}] class={el.get_attribute("class")} text={text[:120]}')
        lines.append(f'     i_classes={icons}  span_class={span_cls}')
        if ui == 'space':
            lines.append(f'     tag={_space_text(el, SPACE_TAG)!r} '
                         f'title={_space_text(el, SPACE_TITLE)!r} '
                         f'status={space_status_icons(el)[:60]!r} '
                         f'is_video={is_video(el, ui)} is_done={is_done(el, ui)}')
    (out_dir / 'diagnose.txt').write_text('\n'.join(lines), encoding='utf-8')

    say('已保存调试信息：')
    say(f'  {html_path}')
    say(f'  {out_dir / "diagnose.txt"}')


# ---------------------------------------------------------------- 播放

PLAY_JS = r"""
window.__lp = window.__lp || {};
var RATE = __RATE__;
var RESTART = __RESTART__;   // true = 每次从 0 开始播，不从上次的位置续播

window.__lp.finished = false;
window.__lp.lastT = -1;
window.__lp.lastTick = Date.now();
window.__lp.enforce = false;  // 默认不抢着设倍速：先把播放器自己的倍速读准，再由 set_speed 决定

// 0) 换视频时复位（学习空间是单页应用，点别的节时 <video> 会换源甚至换元素，
//    不复位的话「上一节的进度」会变成「进度倒退」，被误判成播完）
function lp_check_src(v) {
  var src = v.currentSrc || v.src || '';
  if (window.__lp.src !== src) {
    window.__lp.src = src;
    window.__lp.lastT = -1;
    window.__lp.finished = false;
    window.__lp.seekInit = false;
    window.__lp.seekCount = 0;
  }
}

// 把进度拉回 0：站里会记住上次看到哪儿并自动续播，这里强制从头放
function lp_seek_start(v) {
  if (!RESTART) { return; }
  if (!v.duration || v.duration <= 0) { return; }
  if (!window.__lp.seekInit) {
    window.__lp.seekInit = true;
    window.__lp.seekCount = 0;
  }
  if (window.__lp.seekCount >= 3) { return; }

  var t = v.currentTime;

  // 正常播放这一跳能走多远？阈值必须随倍速和时间间隔放大，
  // 否则 16 倍速下「正常前进」会被当成「被拽回远处」而反复拉回 0。
  var nowMs = Date.now();
  var dt = (nowMs - (window.__lp.seekTickMs || nowMs)) / 1000;
  window.__lp.seekTickMs = nowMs;
  if (dt <= 0 || dt > 5) { dt = 0.3; }
  var jumpLimit = RATE * dt * 1.8 + 8;

  var need = false;
  if (window.__lp.seekCount === 0 && t > 1.5) {
    need = true;                                   // 一上来就在续播位置
  } else if (window.__lp.lastT >= 0 && (t - window.__lp.lastT) > jumpLimit) {
    need = true;                                   // 播放器又把进度拽回上次的位置了
  }
  if (need) {
    try { v.currentTime = 0; } catch (e) { return; }
    window.__lp.seekCount += 1;
    window.__lp.lastT = -1;       // 别把「拉回开头」当成进度倒退
    window.__lp.finished = false;
  }
}

// 1) 暂停就继续播（原版就是这么干的：video.addEventListener('pause', () => video.play())）
clearInterval(window.__lp.playTimer);
window.__lp.playTimer = setInterval(function () {
  var v = document.querySelector('video');
  if (!v) { return; }
  lp_check_src(v);
  lp_seek_start(v);
  if (v.paused && !v.ended) {
    var p = v.play();
    if (p && p.catch) { p.catch(function () {}); }
  }
}, 300);

// 2) 静音 + 倍速（每 0.5 秒校正一次，页面重置了也能拉回来）
clearInterval(window.__lp.rateTimer);
window.__lp.rateTimer = setInterval(function () {
  var v = document.querySelector('video');
  if (!v) { return; }
  lp_check_src(v);
  v.muted = true;
  if (!window.__lp.enforce) { return; }
  // 播放器会不停把 playbackRate 改回它自己的状态，跟它硬拼只能到 1.3 倍。
  // 这里直接在这个 <video> 实例上把属性锁死：它每次「改回 1」都会落到我们的
  // setter 上，照样是 RATE，于是媒体始终保持目标倍速。
  if (!v.__lp_forced) { lp_force_rate(v); }
  try {
    if (Math.abs((v.playbackRate || 1) - RATE) > 0.01) { v.playbackRate = RATE; }
  } catch (e) {}
}, 500);

function lp_force_rate(v) {
  var desc = null, p = v;
  while (p && !desc) {
    desc = Object.getOwnPropertyDescriptor(p, 'playbackRate');
    p = Object.getPrototypeOf(p);
  }
  if (!desc || !desc.set || !desc.get) {
    try { v.playbackRate = RATE; } catch (e) {}
    return;
  }
  try {
    Object.defineProperty(v, 'playbackRate', {
      configurable: true,
      get: function () { return RATE; },
      set: function (x) { try { desc.set.call(this, RATE); } catch (e) {} }
    });
    v.__lp_forced = true;
    desc.set.call(v, RATE);
  } catch (e) {
    try { v.playbackRate = RATE; } catch (x) {}
  }
  // 顺手把播放器上那个倍速标签也改成目标值（它自己的状态没变，原来一直显示 1.00X）
  try {
    var sb = document.querySelector('xt-speedbutton');
    if (sb) {
      var val = sb.querySelector('.xt_video_player_common_value');
      if (val) { val.textContent = RATE.toFixed(2) + 'X'; }
    }
  } catch (e) {}
}

// 3) 判断「这一节看完了」：播到头(ended) 或者进度倒退(平台自动重播/循环)
clearInterval(window.__lp.doneTimer);
window.__lp.doneTimer = setInterval(function () {
  var v = document.querySelector('video');
  if (!v) { return; }
  lp_check_src(v);
  lp_seek_start(v);

  // 必须真的播到尾巴才算 ended，防止还没加载完的 <video> 被当成播完
  if (v.ended && v.duration > 0 && v.currentTime >= v.duration - 1) {
    window.__lp.finished = true;
  }
  if (window.__lp.lastT >= 1 && v.currentTime < window.__lp.lastT - 0.5) {
    window.__lp.finished = true;
  }
  if (v.currentTime !== window.__lp.lastT) { window.__lp.lastTick = Date.now(); }
  window.__lp.lastT = v.currentTime;
}, 200);
"""

STATUS_JS = r"""
var v = document.querySelector('video');
if (!v) { return null; }
return {
  t: v.currentTime || 0,
  d: v.duration || 0,
  paused: !!v.paused,
  rate: v.playbackRate || 1,
  finished: !!(window.__lp && window.__lp.finished),
  err: v.error ? v.error.code : 0,
  ready: v.readyState,
  net: v.networkState
};
"""


def _is_playing(driver) -> bool:
    try:
        return not driver.execute_script(
            'var v=document.querySelector("video"); return v ? v.paused : true;')
    except WebDriverException:
        return False


def _video_center_clickable(driver) -> bool:
    """画面中央是不是可以直接点（防止点中盖在上面的课堂弹题）。"""
    try:
        return bool(driver.execute_script(r"""
            var v = document.querySelector('video');
            if (!v) { return false; }
            var r = v.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) { return false; }
            var el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            if (!el) { return false; }
            var t = '', p = el;
            for (var i = 0; i < 3 && p; i++) { t += (p.textContent || ''); p = p.parentElement; }
            t = t.slice(0, 300);
            if (/单选|多选|判断|填空|提交答案|请作答/.test(t)) { return false; }
            return true;
        """))
    except WebDriverException:
        return False


def try_resume(driver) -> bool:
    """
    视频停着不动时，把它弄起来。

    经验：这个播放器的「播放」状态得靠真实点击解除 —— 光调 video.play() 会被
    播放器自己的状态机立刻暂停回去（日志里表现为「一直暂停」，进度停在 0）。
    所以依次尝试：点画面 → 点大播放键/控制栏播放键 → play()+空格。
    """
    if _is_playing(driver):
        return True

    # ① 真实点一下画面中央（多数播放器点画面就是播放/暂停）
    if _video_center_clickable(driver):
        try:
            v = driver.find_element(By.TAG_NAME, 'video')
            ActionChains(driver).move_to_element(v).click().perform()
            time.sleep(0.8)
            if _is_playing(driver):
                return True
        except WebDriverException:
            pass

    # ② 大播放键 / 控制栏播放键（有好几种元素名，挨个试）
    for sel in ('xt-bigbutton',
                'button.xt_video_bit_play_btn',
                '.xt_video_player_big_play_layer',
                'xt-playbutton',
                '.xt_video_player_play_btn'):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
        except WebDriverException:
            continue
        for el in els[:2]:
            try:
                ActionChains(driver).move_to_element(el).click().perform()
            except WebDriverException:
                try:
                    driver.execute_script('arguments[0].click();', el)
                except WebDriverException:
                    pass
            time.sleep(0.7)
            if _is_playing(driver):
                return True

    # ③ 直接调 play()，再补一个空格（很多播放器把空格当播放/暂停）
    try:
        driver.execute_script(
            'var v=document.querySelector("video");'
            'if (v) { v.muted = true; var p = v.play(); if (p && p.catch) { p.catch(function(){}); } }')
    except WebDriverException:
        pass
    try:
        driver.find_element(By.TAG_NAME, 'video').send_keys(' ')
    except WebDriverException:
        pass
    time.sleep(0.8)
    return _is_playing(driver)


def dump_play_state(driver):
    """卡住时把播放器内部状态打出来，方便定位到底是哪种卡法。"""
    try:
        st = driver.execute_script(r"""
            var v = document.querySelector('video');
            if (!v) { return null; }
            var err = window.__lp_err || '';
            try {
              var p = v.play();
              if (p && p.catch) {
                p.catch(function (e) { window.__lp_err = e.name + ': ' + e.message; });
              }
            } catch (x) { err = '' + x; }
            return {
              paused: v.paused,
              readyState: v.readyState,
              networkState: v.networkState,
              duration: v.duration,
              currentTime: v.currentTime,
              muted: v.muted,
              mediaError: v.error ? (v.error.code + ' ' + (v.error.message || '')) : '',
              playError: err,
              src: (v.currentSrc || v.src || '').split('?')[0]
            };
        """)
        if st:
            say(f'  播放器状态：{st}')
    except WebDriverException:
        pass


def wait_for_video(driver, timeout: float) -> bool:
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.5).until(
            lambda d: d.execute_script('return !!document.querySelector("video");')
        )
        return True
    except Exception:
        return False


def _node_id(url: str) -> str:
    m = re.search(r'node_id=(\d+)', url or '')
    return m.group(1) if m else ''


def switch_to_item(driver, el, ui: str, per_try: float = 2.5) -> bool:
    """
    点开左侧这一节，并且**确认播放器真的换片了**。

    为什么要验证：学习空间加载时会自动播 URL 里那一节（可能是已经看完的）。
    如果点击没生效，不验证就会把「自动播的那一节」当成目标，播完刷新又回来，
    永远在同一个视频上打转。
    """
    before_url = driver.current_url
    before_node = _node_id(before_url)
    before_src = video_src(driver)

    # 有的界面要点标题/左侧区域才有反应，逐个试（行中心可能正好压在右侧状态区上）
    targets = [el]
    if ui == 'space':
        for cls in (SPACE_TITLE, 'leaf-item-left'):
            try:
                targets.append(el.find_element(By.CLASS_NAME, cls))
            except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
                pass

    for t in targets:
        try:
            driver.execute_script('arguments[0].scrollIntoView({block:"center"});', t)
            time.sleep(0.25)
        except WebDriverException:
            pass

        clicked = False
        try:
            ActionChains(driver).move_to_element(t).click().perform()
            clicked = True
        except WebDriverException:
            try:
                driver.execute_script('arguments[0].click();', t)
                clicked = True
            except WebDriverException:
                pass
        if not clicked:
            continue

        deadline = time.time() + per_try
        while time.time() < deadline:
            # 新开标签也算成功
            if len(driver.window_handles) > 1:
                return True
            now_src = video_src(driver)
            if now_src and now_src != before_src:
                return True
            now_node = _node_id(driver.current_url)
            if now_node and before_node and now_node != before_node:
                # URL 变了还不够（页面自己也会改写 node_id），再等片源确认
                time.sleep(1.0)
                if video_src(driver) != before_src:
                    return True
            time.sleep(0.3)

    return False


def play_one(driver, el, ui: str, rate: float, max_minutes: float,
             title: str = '', seen_srcs=None, restart: bool = True):
    """播放一个视频。返回 True=播完 / False=失败 / 'already'=平台显示已完成。"""
    # 页面加载时 SPA 会自动播 URL 里那一节，先停掉，免得它抢戏
    try:
        driver.execute_script(
            'var v=document.querySelector("video"); if (v && !v.paused) v.pause();')
    except WebDriverException:
        pass

    switched = switch_to_item(driver, el, ui)
    if not switched:
        # 有一种常见情况：目标本来就是当前正在播的那一节（刷新后 SPA 会恢复上一次的节点），
        # 这时点它不会换片源，但确实就是对的那一节，照常播，别跳过。
        if header_has_title(driver, title):
            say('这一节已经是当前播放的，直接从头播。')
        else:
            say('点了这一节，但播放器没有换片（可能页面结构变了），跳过。')
            return False

    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
        say('已切到播放页面（新标签）')

    if not wait_for_video(driver, 25):
        say('这个页面里没等到 <video> 元素，跳过。')
        return False

    # 一进来视频源就是坏的（地址没签名 / 格式不支持），别浪费时间点菜单和唤醒
    try:
        _st = driver.execute_script(STATUS_JS)
    except WebDriverException:
        _st = None
    if _st and (_st.get('err') or 0):
        say(f'  这个视频源加载失败（media error {_st["err"]}）。')
        return 'media'

    # 换片后看一眼播放器标题旁边是不是写着「已完成」——目录图标可能识别不到，
    # 这里以平台自己显示的状态为准，避免把看过的视频一遍遍重播
    time.sleep(0.7)
    if header_marked_done(driver, title):
        say('这一节平台已标记「已完成」，跳过。')
        return 'already'

    src_key = _src_key(video_src(driver))
    if seen_srcs is not None and src_key and src_key in seen_srcs:
        say('这个片源本次已经播过了，跳过。')
        return 'already'

    time.sleep(0.8)
    driver.execute_script(
        PLAY_JS.replace('__RATE__', str(rate))
               .replace('__RESTART__', 'true' if restart else 'false'))

    # 倍速必须点播放器自己的菜单，否则会被播放器改回去（菜单上还显示 1.00X）
    speed_ui = set_speed(driver, rate)
    try:
        driver.execute_script('window.__lp.enforce = arguments[0];', not speed_ui)
    except WebDriverException:
        pass

    # 把鼠标从倍速按钮上挪开，把下拉菜单收起来（否则它会一直挡住画面）
    try:
        ActionChains(driver).move_to_element(
            driver.find_element(By.TAG_NAME, 'video')).perform()
    except WebDriverException:
        pass

    # 「拉回 0 秒」可能让播放器自己进入暂停，光调 play() 它不认，得点一下画面。
    # 先立刻唤醒一次，省得白等 8 秒。
    time.sleep(1.0)
    if not _is_playing(driver):
        if try_resume(driver):
            say('  已开始播放。')

    began = time.time()
    last_report = time.time() - 10   # 第一次进度上报延迟 5 秒，好让「实际倍速」有样本
    stall_warned = False
    paused_since = None
    resume_total = 0
    speed_retries = 0
    speed_check_at = time.time() + 20
    max_t = 0.0     # 这一节实际走到的最远位置，用来判断是不是真的播到结尾
    last_obs_t = None   # 上次采样时的进度
    last_obs_wall = None
    last_move_t = None      # 进度最后一次变化的时刻（用来发现「显示在播但不走」）
    last_move_wall = time.time()
    nudges = 0              # 主动重新加载的次数

    while True:
        waited = time.time() - began
        if waited > max_minutes * 60:
            say(f'单个视频等待超过 {int(max_minutes)} 分钟，跳过。')
            return False

        status = None
        try:
            status = driver.execute_script(STATUS_JS)
        except WebDriverException:
            pass  # 页面可能正在跳转/刷新

        if status is None:
            if waited > 60:
                say('页面里一直没有 <video>，跳过。')
                return False
        else:
            # 视频源本身坏了（CDN 返回的不是视频 / 地址没签名）—— 唤醒多少次都没用。
            # 交回主循环：先重启浏览器换一份新会话再试，而不是直接判这一节有罪。
            if (status.get('err') or 0) and waited > 6:
                say(f'  这个视频源加载失败（media error {status["err"]}）。')
                return 'media'

            # 一直没有任何可播放数据（readyState 一直 0）也别耗着
            if waited > 120 and (status.get('ready') or 0) < 2 \
                    and (status.get('t') or 0) < 0.5:
                say('  这一节一直没有可播放的数据。')
                return 'media'

            # 先更新「走到的最远位置」，再判断是否播完（否则会少算一次）
            max_t = max(max_t, status.get('t') or 0)
            if status.get('finished'):
                if seen_srcs is not None:
                    k = _src_key(video_src(driver))
                    if k:
                        seen_srcs.add(k)
                dur = status.get('d') or 0
                if dur > 1:
                    if max_t < dur * 0.9:
                        say(f'这一节判定播完，但只走到 {max_t:.0f}/{dur:.0f} 秒'
                            f'（{max_t / dur * 100:.0f}%），没到结尾。')
                    else:
                        say(f'这一节播放完毕（走到 {max_t:.0f}/{dur:.0f} 秒）。')
                else:
                    say('这一节播放完毕。')
                say('等 5 秒再翻页。')
                time.sleep(5)
                return True
            dur = status.get('d') or 0
            t_now = status.get('t') or 0

            # 量一下「实际倍速」：进度增量 ÷ 真实经过的时间。
            # 播放器那个 1.00X 标签可能是它自己的状态，不一定反映真实播放速度。
            obs = None
            wall = time.time()
            if last_obs_t is not None and last_obs_wall is not None:
                dw = wall - last_obs_wall
                dm = t_now - last_obs_t
                if dw > 0.8 and dm >= 0:
                    obs = dm / dw
            last_obs_t, last_obs_wall = t_now, wall

            # 「显示在播，但进度一动不动」= 视频数据没下来（卡流）。
            # 这种情况不打 [暂停]，光等没用，得主动重新加载。
            if last_move_t is None or abs(t_now - last_move_t) > 0.05:
                last_move_t = t_now
                last_move_wall = wall
            elif not status.get('paused') and wall - last_move_wall > 15:
                if nudges < 2:
                    nudges += 1
                    say(f'  在播但进度不动（{wall - last_move_wall:.0f} 秒），'
                        f'第 {nudges} 次主动重新加载……')
                    js = ('var v=document.querySelector("video"); if(!v){return;}'
                          + ('try{v.load();}catch(e){}' if nudges >= 2 else
                             'try{v.currentTime=0;}catch(e){}')
                          + 'var p=v.play(); if(p&&p.catch){p.catch(function(){});}')
                    try:
                        driver.execute_script(js)
                    except WebDriverException:
                        pass
                    last_move_wall = wall
                else:
                    say('  重新加载后进度还是不动，交给主循环重启浏览器。')
                    return 'media'

            could_report = dur > 0.5 and ((status.get('t', 0) > 0.2) or (not status.get('paused')))
            if could_report and time.time() - last_report > 15:
                t = status.get('t', 0)
                obs_txt = f'  实际{obs:.1f}x' if obs is not None else ''
                say(f'  播放中 {t / dur * 100:5.1f}%  {t:7.1f}/{dur:7.1f}s  倍速{status.get("rate")}{obs_txt}'
                    f'{"  [暂停]" if status.get("paused") else ""}')
                last_report = time.time()

            # 时长一直是 0 = 视频流没加载出来（网络或播放器卡住）
            if dur <= 0.5 and not stall_warned and waited > 45:
                say('  [提示] 视频一直没加载出来（时长还是 0），可能是网络慢或被卡住。')
                say('         可以点一下画面中间的播放键，或稍等一会儿。')
                stall_warned = True

            # 播放器有时会在切清晰度/拖动进度后把倍速重置，发现掉回 1 倍就再点一次
            if (speed_ui and speed_retries < 3 and time.time() > speed_check_at
                    and abs(float(status.get('rate') or 1) - rate) > 0.01):
                say('  倍速被播放器重置了，再点一次菜单。')
                speed_ui = set_speed(driver, rate)
                try:
                    driver.execute_script('window.__lp.enforce = arguments[0];', not speed_ui)
                except WebDriverException:
                    pass
                speed_retries += 1
                speed_check_at = time.time() + 20

            if status.get('paused'):
                if paused_since is None:
                    paused_since = time.time()
                elif time.time() - paused_since > 8 and resume_total < 12:
                    resume_total += 1
                    say(f'  暂停中，尝试唤醒（第 {resume_total} 次）……')
                    if try_resume(driver):
                        say('  已恢复播放。')
                        paused_since = None
                    else:
                        paused_since = time.time()
                        if resume_total == 1:
                            dump_play_state(driver)
            else:
                paused_since = None

            # 连续暂停很久，多半是弹题或者卡住了 —— 提示一下，但不中断
            if status.get('paused') and not stall_warned and waited > 120:
                say('  [提示] 视频一直处于暂停状态，可能是弹出了课堂问题或网络卡住。')
                say('         如果窗口里出现了题目，请手动答一下；程序会继续尝试。')
                stall_warned = True

        time.sleep(2)


def close_player_tab(driver, list_handle: str, course_url: str):
    """关掉播放标签，回到课程列表。"""
    handles = driver.window_handles
    for h in list(handles):
        if h != list_handle:
            try:
                driver.switch_to.window(h)
                driver.close()
            except WebDriverException:
                pass

    if list_handle in driver.window_handles:
        driver.switch_to.window(list_handle)
    else:
        # 同一个窗口内跳转了，就重新打开课程页
        driver.switch_to.window(driver.window_handles[0])
        driver.get(course_url)
        time.sleep(3)


# ---------------------------------------------------------------- 主流程

def validate_config(cfg):
    course_url = cfg.get('Settings', 'course_url', fallback='').strip()
    if '在此填写' in course_url or not course_url:
        raise SystemExit('config.ini 里的 course_url 还没填。请填入课程「学习内容」页面的地址。')
    if not course_url.lower().startswith('http'):
        raise SystemExit('config.ini 里的 course_url 必须以 https:// 开头。')
    # 只放行本机回环地址的 http，方便离线自测；真实课程一律要求 https
    if course_url.lower().startswith('http://') and not re.match(
            r'http://(localhost|127\.0\.0\.1)([:/]|$)', course_url, re.I):
        raise SystemExit('course_url 请用 https://（只有本机测试地址才允许 http://）。')
    return course_url


def open_course(driver, cfg, path: Path, allow_qr_login: bool):
    course_url = cfg.get('Settings', 'course_url').strip()
    cookie_raw = cfg.get('Settings', 'cookie', fallback='').strip()
    timeout = float(cfg.getint('Settings', 'implicitly_wait', fallback=20))

    home = home_of(course_url)
    driver.get(home)
    time.sleep(2)

    cookies = parse_cookie(cookie_raw)
    if cookies:
        say(f'写入 {len(cookies)} 个 cookie ...')
        install_cookies(driver, cookies)

    driver.get(course_url)
    time.sleep(3)

    if looks_like_login(driver):
        say('当前 cookie 无效或未填写，需要登录。')
        if cfg.getboolean('Settings', 'headless', fallback=False):
            raise SystemExit('无窗口模式（headless=true）不能扫码登录，请改成 false 再跑。')
        if not allow_qr_login:
            raise SystemExit('需要登录，但当前模式不允许交互登录。')
        if not wait_login_by_qrcode(driver, home):
            raise SystemExit('扫码登录没完成，程序退出。')
        sid = driver.get_cookie('sessionid')
        if sid and sid.get('value'):
            save_cookie_to_config(path, sid['value'])
        driver.get(course_url)
        time.sleep(3)

    return course_url, timeout


def run(args, cfg, path):
    headless = cfg.getboolean('Settings', 'headless', fallback=False)
    browser = cfg.get('Settings', 'browser', fallback='auto').strip().lower()
    rate = cfg.getfloat('Settings', 'playback_rate', fallback=2)
    max_minutes = cfg.getfloat('Settings', 'max_minutes_per_video', fallback=180)
    restart = cfg.getboolean('Settings', 'restart_from_start', fallback=True)

    if args.headless:
        headless = True
        cfg.set('Settings', 'headless', 'true')   # 让 open_course 也知道现在是后台模式
    if args.url:
        cfg.set('Settings', 'course_url', args.url)
    if args.cookie is not None:
        cfg.set('Settings', 'cookie', args.cookie)

    validate_config(cfg)

    say('=' * 56)
    say('雨课堂视频自动播放器')
    say('=' * 56)

    driver, _browser = build_driver(args.browser or browser, headless)
    save_at_end = False
    try:
        course_url, page_wait = open_course(driver, cfg, path, allow_qr_login=not args.no_login)
        save_at_end = (args.cookie is None) and not re.search(
            r'//(localhost|127\.0\.0\.1)', course_url, re.I)

        ui, els = find_list(driver, page_wait)
        if els:
            expand_all(driver, ui)
            if ui == 'space':
                ui, els = find_list(driver, page_wait)
        if not els:
            say('没找到课程列表。先保存调试信息：')
            dump_debug(driver, ui or '未知', [])
            say('请把 debug/page.html 和 debug/diagnose.txt 发给我，我按你这版界面改。')
            return

        pending = list_pending(driver, ui, els)
        say(f'界面识别为：{UI_NAMES.get(ui, ui)}')
        if ui == 'space':
            nvid, done_count, partial_n, stats = space_counts(read_space_rows(driver))
            say(f'视频 {nvid} 个，已完成 {done_count} 个，待播放 {nvid - done_count} 个')
            if partial_n:
                say(f'  其中看了一半的 {partial_n} 个 —— 会最先从头播掉（清理残留）')
            if stats:
                top = sorted(stats.items(), key=lambda x: -x[1])[:5]
                say('状态图标统计：' + '；'.join(f'{k} × {v}' for k, v in top))
        else:
            say(f'待播放 {len(pending)} 个')

        if args.list:
            for i, (_, text, _k) in enumerate(pending, 1):
                say(f'  {i}. {text[:110]}')
            if not pending:
                say('  （没有未完成的视频）')
            if args.dump:
                dump_debug(driver, ui, els)
            return

        if not pending:
            say('全部视频都已完成，收工。')
            return

        if args.dump:
            dump_debug(driver, ui, els)

        failed = {}
        played = {}
        media_tries = {}   # 每个视频重启浏览器重试的次数
        already = set()
        notified = set()   # 跳过提示只打一次，避免每轮刷屏
        seen_srcs = set()
        prev_done = None
        just_played = False
        list_handle = driver.current_window_handle
        index = 0

        while True:
            ui, els = find_list(driver, page_wait)
            if els:
                expand_all(driver, ui)
                if ui == 'space':
                    ui, els = find_list(driver, page_wait)
            if not els:
                say('列表没加载出来，刷新一次……')
                try:
                    driver.refresh()
                except WebDriverException:
                    pass
                time.sleep(5)
                ui, els = find_list(driver, page_wait)
                if not els:
                    # 有时播放页是同一个标签里打开的，刷新只刷新了播放页；
                    # 直接重新打开课程地址最稳。
                    say('刷新后还是没有，重新打开课程页……')
                    try:
                        driver.get(course_url)
                    except WebDriverException:
                        pass
                    time.sleep(5)
                    ui, els = find_list(driver, page_wait)
                if not els:
                    say('还是找不到列表，停止。可用 --dump 保存页面结构。')
                    return

            pending = list_pending(driver, ui, els)

            # 上一节播完后，看平台有没有把「已完成」加上去
            if ui == 'space':
                _, done_now, _, _ = space_counts(read_space_rows(driver))
                if just_played and prev_done is not None and done_now <= prev_done:
                    say('')
                    say(f'[注意] 上一节播完了，但平台完成数没变（{prev_done} → {done_now}）。')
                    say('       常见原因：平台不认 2 倍速，或者视频里弹题没答。')
                    say('       可以先跑 python main.py --rate 1 试一节看看。')
                    say('')
                prev_done = done_now
            just_played = False

            if not pending:
                say('')
                say('=' * 56)
                say('恭喜！本课程的视频全部播放完毕。')
                say('=' * 56)
                return

            target = None
            for el, text, key in pending:
                if key in already:
                    continue
                if failed.get(key, 0) >= 2:
                    if key not in notified:
                        say(f'  [跳过] 这个视频试了 2 次都没播完：{text[:60]}')
                        notified.add(key)
                    continue
                if played.get(key, 0) >= 3:
                    # 播成功了但平台一直不记「已完成」，再播也没意义，避免死循环
                    if key not in notified:
                        say(f'  [跳过] 播过 3 次仍未记录完成，可能平台不认：{text[:60]}')
                        notified.add(key)
                    continue
                target = (el, text, key)
                break

            if target is None:
                say('')
                say('剩下的视频都跳过了。可能是下面几种情况：')
                say('  - 视频被平台判为「不记录进度」（如已截止、类型不支持）')
                say('  - 平台不认倍速，或视频里有弹题没答')
                say('  - 页面结构和脚本认的不一样')
                say('可以先跑一遍 python main.py --dump，把 debug/ 里的文件发我。')
                return

            el, text, key = target
            index += 1
            say('')
            say(f'---- 第 {index} 个 / 待播 {len(pending)} 个 ----')
            say(f'{text[:120]}')

            try:
                res = play_one(driver, el, ui, rate, max_minutes,
                               title=text, seen_srcs=seen_srcs, restart=restart)
            except Exception as e:
                say(f'播放出错：{type(e).__name__}: {str(e).splitlines()[0][:150]}')
                res = False

            restarted = False
            if res == 'media':
                # 视频源异常。不一定是这一节的问题（可能是会话/播放器状态坏了），
                # 所以先整个重启浏览器、重新登录课程，再试同一节；两次都不行才跳过。
                media_tries[key] = media_tries.get(key, 0) + 1
                if media_tries[key] <= 2:
                    say('')
                    say(f'  视频源异常，第 {media_tries[key]} 次重启浏览器后重试这一节……')
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    try:
                        driver, _browser = build_driver(args.browser or browser, headless)
                        course_url, page_wait = open_course(
                            driver, cfg, path, allow_qr_login=not args.no_login)
                        list_handle = driver.current_window_handle
                        time.sleep(2)
                        restarted = True
                    except SystemExit:
                        raise
                    except Exception as e:
                        say(f'  重启浏览器失败：{type(e).__name__}: {str(e).splitlines()[0][:120]}')
                        say('  请手动重新运行程序。')
                        return
                else:
                    say('  重启浏览器两次后仍然加载失败，这一节先跳过。')
                    failed[key] = 2   # 直接标记够次数，别再多绕一轮
            elif res == 'already':
                already.add(key)
            elif res:
                played[key] = played.get(key, 0) + 1
                just_played = True
            else:
                failed[key] = failed.get(key, 0) + 1

            if restarted:
                continue

            close_player_tab(driver, list_handle, course_url)
            list_handle = driver.current_window_handle
            time.sleep(random.uniform(2, 5))
            driver.refresh()
            time.sleep(5)   # 原版 #8 的修复：刷新后等页面加载完

    finally:
        # 雨课堂有可能在会话中途轮换 sessionid，收工时把最新的存回去
        if save_at_end:
            try:
                sid = driver.get_cookie('sessionid')
                if sid and sid.get('value'):
                    save_cookie_to_config(path, sid['value'])
            except Exception:
                pass
        try:
            driver.quit()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(
        description='雨课堂视频自动播放器（学习空间 / 新版 / 旧版界面通用，Edge/Chrome 自适应）')
    ap.add_argument('--list', action='store_true', help='只列出未完成的视频，不播放')
    ap.add_argument('--dump', action='store_true', help='保存页面调试信息到 debug/')
    ap.add_argument('--login', dest='force_login', action='store_true', help='强制重新扫码登录')
    ap.add_argument('--no-login', action='store_true', help='不允许交互扫码（登录失败就直接退出）')
    ap.add_argument('--self-test', action='store_true', help='只测试浏览器能否启动')
    ap.add_argument('--headless', action='store_true', help='覆盖配置：无窗口运行')
    ap.add_argument('--browser', choices=['auto', 'chrome', 'edge'], help='指定浏览器')
    ap.add_argument('--url', help='覆盖配置：课程 URL')
    ap.add_argument('--cookie', help='覆盖配置：sessionid（可传整串 cookie）')
    ap.add_argument('--rate', type=float, help='覆盖配置：播放倍速')
    args = ap.parse_args()

    if args.self_test:
        driver, name = build_driver(args.browser or 'auto', True)
        try:
            driver.get('https://www.bing.com')
            say(f'浏览器 {name} 正常，页面标题：{driver.title[:40]}')
        finally:
            driver.quit()
        return

    loaded = load_config()
    if not loaded:
        return
    cfg, path = loaded

    if args.force_login:
        cfg.set('Settings', 'cookie', '')          # 清空，强制走扫码
        cfg.set('Settings', 'headless', 'false')   # 扫码必须有窗口
    if args.rate:
        cfg.set('Settings', 'playback_rate', str(args.rate))

    try:
        run(args, cfg, path)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        say('\n已手动中断。')
    except Exception:
        say('\n程序出错，完整信息如下：')
        traceback.print_exc()
        say('\n把上面的信息发给我，我按你的情况改。')


if __name__ == '__main__':
    main()
