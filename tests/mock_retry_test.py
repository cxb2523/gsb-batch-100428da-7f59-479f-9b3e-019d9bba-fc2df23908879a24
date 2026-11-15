#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「片源异常 → 重启浏览器 → 重试同一节」链路的本地模拟验证。

不起真实雨课堂：本脚本起一个本地 HTTP 服务，返回一个仿「学习空间」界面的
课程页（多个 .leaf-item + 可切换的假 <video> 源），然后用 subprocess 跑
仓库根目录的 main.py 指向这个页面，检查：

  场景一：第 2 节片源永远是坏的（media error）
    - 程序应重启浏览器两次，每次都在新列表里「重定位」到第 2 节再试；
    - 两次都失败后把第 2 节标记跳过，继续播第 3 节；
    - 重试期间自动恢复出来的视频不能被当成目标播掉（服务端 done 记录里
      只能有第 1、3 节）。

  场景二：第 2 节被点过一次之后就从列表里消失（模拟列表顺序/折叠状态变化）
    - 重启后应输出「目标小节定位失败」并给该节记一次失败；
    - 程序不能随手播页面自动恢复出来的那一节；
    - 其余小节照常播完。

注意：这只是本地 mock 验证，不代表真实雨课堂链路已通过。

用法：
    python tests/mock_retry_test.py            # 跑全部场景
    python tests/mock_retry_test.py 1          # 只跑场景一
    python tests/mock_retry_test.py 2          # 只跑场景二
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent
MAIN = REPO / 'main.py'

# ---------------------------------------------------------------- 模拟课程页

NODES = [
    {'id': '1', 'title': '第 1 节 绪论',   'dur': 6, 'bad': False},
    {'id': '2', 'title': '第 2 节 坏片源', 'dur': 6, 'bad': True},
    {'id': '3', 'title': '第 3 节 进阶',   'dur': 6, 'bad': False},
]

ROW_HTML = (
    '<div class="leaf-item" data-node="{id}" data-src="{src}" '
    'data-dur="{dur}" data-bad="{bad}">'
    '<span class="leaf-item-tag">视频</span>'
    '<span class="leaf-item-title">{title}</span>'
    '<span class="leaf-item-status"></span>'
    '</div>'
)

PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>模拟课程-学习内容</title>
<style>
  body { font-family: sans-serif; }
  #now-playing { padding: 10px; background: #eef; margin-bottom: 10px; }
  .leaf-item { display: block; padding: 10px; border: 1px solid #ccc;
               margin: 6px; cursor: pointer; }
  .leaf-item-tag { color: #888; margin-right: 8px; }
  #vid { width: 320px; height: 180px; background: #000; }
</style></head>
<body>
<div id="now-playing">当前播放：<span id="np-title"></span></div>
<div id="tree">
__ROWS__
</div>
<video id="vid" muted></video>
<script>
var STATE = __STATE__;
(function () {
  var v = document.getElementById('vid');
  // 假的媒体状态：不用真实视频文件，属性全部由脚本驱动，行为可预期
  var cur = { node: '', src: '', t: 0, d: 0, paused: true, ended: false,
              err: null, ready: 0, net: 0 };
  function prop(name, get, set) {
    var d = { configurable: true, get: get };
    if (set) { d.set = set; }
    Object.defineProperty(v, name, d);
  }
  prop('currentSrc', function () { return cur.src; });
  prop('src', function () { return cur.src; }, function (x) { cur.src = x; });
  prop('currentTime', function () { return cur.t; },
       function (x) { cur.t = x; if (x < cur.d) { cur.ended = false; } });
  prop('duration', function () { return cur.d; });
  prop('paused', function () { return cur.paused; });
  prop('ended', function () { return cur.ended; });
  prop('error', function () { return cur.err; });
  prop('readyState', function () { return cur.ready; });
  prop('networkState', function () { return cur.net; });
  v.play = function () {
    cur.paused = !!cur.err;
    return { catch: function () { return this; }, then: function () { return this; } };
  };
  v.pause = function () { cur.paused = true; };
  v.load = function () {};

  function beac(type) {
    try { fetch('/event?type=' + type + '&node=' + encodeURIComponent(cur.node)); }
    catch (e) {}
  }

  function selectNode(row) {
    cur.node = row.getAttribute('data-node');
    cur.src = row.getAttribute('data-src');
    cur.d = parseFloat(row.getAttribute('data-dur') || '6');
    cur.t = 0; cur.ended = false; cur.paused = true;
    var bad = row.getAttribute('data-bad') === '1';
    cur.err = bad ? { code: 4, message: 'MEDIA_ERR_SRC_NOT_SUPPORTED(模拟)' } : null;
    cur.ready = bad ? 0 : 4;
    cur.net = bad ? 3 : 1;
    document.getElementById('np-title').textContent =
      row.querySelector('.leaf-item-title').textContent;
    try { history.replaceState(null, '', '/course?node_id=' + cur.node); } catch (e) {}
    beac('open');
  }

  var rows = document.querySelectorAll('.leaf-item');
  for (var i = 0; i < rows.length; i++) {
    (function (row) {
      row.addEventListener('click', function () { selectNode(row); });
    })(rows[i]);
  }

  // 平台状态：已完成的小节打勾（和真实界面一样的图标 class）
  (STATE.done || []).forEach(function (id) {
    var row = document.querySelector('.leaf-item[data-node="' + id + '"]');
    if (row) {
      row.querySelector('.leaf-item-status').innerHTML =
        '<i class="icon-yuanquangou-mianzhuang"></i>';
    }
  });

  // 模拟 SPA「按上次节点自动恢复」：页面一打开就切到上次那一节。
  // 真实站点会自动播，这里保持暂停只是为了让测试节奏可控；
  // 对 main.py 来说关键是「片源和标题被恢复成了别的/同一节」。
  if (STATE.last) {
    var row = document.querySelector('.leaf-item[data-node="' + STATE.last + '"]');
    if (!row && rows.length) { row = rows[0]; }   // 上次那节消失了就落在第一节
    if (row) { selectNode(row); }
  }

  // 假播放：每 100ms 走 0.5s 进度，走到头算 ended 并上报「已完成」
  setInterval(function () {
    if (cur.paused || cur.ended || cur.err || !cur.src) { return; }
    cur.t += 0.5;
    if (cur.t >= cur.d) {
      cur.t = cur.d; cur.ended = true; cur.paused = true;
      var row = document.querySelector('.leaf-item[data-node="' + cur.node + '"]');
      if (row) {
        row.querySelector('.leaf-item-status').innerHTML =
          '<i class="icon-yuanquangou-mianzhuang"></i>';
      }
      beac('done');
    }
  }, 100);
})();
</script>
</body></html>
"""

CONFIG_TEMPLATE = """[Settings]
headless = true
browser = auto
course_url = http://127.0.0.1:9/course
cookie =
playback_rate = 2
restart_from_start = true
implicitly_wait = 10
max_minutes_per_video = 5
"""


class State:
    """服务端保存的「平台状态」：新浏览器实例没有本地存储，只能靠服务端记住。"""

    def __init__(self, vanish_node: str | None = None):
        self.done: set[str] = set()
        self.last: str | None = None
        self.open_counts: dict[str, int] = {}
        self.events: list[tuple[str, str]] = []
        self.loads = 0
        # 被打开过一次之后就从列表里消失的小节（模拟列表顺序/折叠状态变化）
        self.vanish_node = vanish_node

    def done_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for typ, node in self.events:
            if typ == 'done':
                out[node] = out.get(node, 0) + 1
        return out


def render_course(state: State, base: str) -> str:
    rows = []
    for n in NODES:
        if state.vanish_node and n['id'] == state.vanish_node \
                and state.open_counts.get(n['id'], 0) > 0:
            continue  # 这节被点过之后就「消失」了
        rows.append(ROW_HTML.format(
            id=n['id'], title=n['title'], dur=n['dur'],
            bad='1' if n['bad'] else '0',
            src=f'{base}/media/n{n["id"]}.mp4'))
    st = json.dumps({'done': sorted(state.done), 'last': state.last},
                    ensure_ascii=False)
    return PAGE.replace('__ROWS__', '\n'.join(rows)).replace('__STATE__', st)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):   # 安静一点
        pass

    def _send(self, body: str, ctype='text/html; charset=utf-8'):
        data = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        st: State = self.server.state            # type: ignore[attr-defined]
        base = f'http://127.0.0.1:{self.server.server_address[1]}'
        if u.path == '/':
            self._send('<html><head><title>home</title></head><body>home</body></html>')
        elif u.path == '/course':
            st.loads += 1
            self._send(render_course(st, base))
        elif u.path == '/event':
            q = parse_qs(u.query)
            typ = q.get('type', [''])[0]
            node = q.get('node', [''])[0]
            st.events.append((typ, node))
            if typ == 'open':
                st.last = node
                st.open_counts[node] = st.open_counts.get(node, 0) + 1
            elif typ == 'done':
                st.done.add(node)
            self._send('ok', 'text/plain')
        else:
            self.send_error(404)


# ---------------------------------------------------------------- 场景运行

def pick_browser() -> str:
    """挑一个本机可用的浏览器。可用 MOCK_BROWSER 环境变量强制指定。"""
    forced = os.environ.get('MOCK_BROWSER')
    if forced:
        return forced
    for name, path in (
        ('chrome', r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
        ('chrome', r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'),
        ('edge', r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
    ):
        if Path(path).exists():
            return name
    return 'auto'


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # 浏览器中途断连（ConnectionResetError 之类）很正常，别刷屏


def run_scenario(name: str, vanish_node: str | None = None):
    print('=' * 64)
    print(f'场景：{name}')
    print('=' * 64)
    browser = pick_browser()
    state = State(vanish_node)
    server = QuietServer(('127.0.0.1', 0), Handler)
    server.state = state                          # type: ignore[attr-defined]
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{port}/course'
    try:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'config.ini').write_text(CONFIG_TEMPLATE, encoding='utf-8')
            # 本测试运行的沙箱环境里：1) 浏览器解析不到默认 profile 路径，
            # 要显式指定用户数据目录；2) 后台联网（组件更新/同步）会触发
            # 浏览器被环境杀掉，要关掉。main.py 会读这两个环境变量。
            env = dict(os.environ)
            env['YKT_BROWSER_USER_DATA_DIR'] = str(Path(tmp) / 'browser-profile')
            env['YKT_BROWSER_EXTRA_ARGS'] = (
                '--disable-background-networking --disable-component-update '
                '--disable-sync --metrics-recording-only --no-pings')
            # Selenium Manager 在受限网络里可能下不动 driver，
            # 可以用 MOCK_DRIVER 环境变量指定本机已有的 chromedriver/msedgedriver。
            if os.environ.get('MOCK_DRIVER'):
                env['YKT_BROWSER_DRIVER'] = os.environ['MOCK_DRIVER']
            proc = subprocess.run(
                [sys.executable, str(MAIN), '--no-login', '--headless',
                 '--browser', browser, '--url', url],
                cwd=tmp, capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=900, env=env)
    finally:
        server.shutdown()
        server.server_close()
    return state, proc


class Checker:
    def __init__(self):
        self.failures: list[str] = []

    def check(self, cond: bool, msg: str):
        print(f'  [{"OK" if cond else "FAIL"}] {msg}')
        if not cond:
            self.failures.append(msg)


def scenario_1(c: Checker):
    """坏片源小节：两次重启重试同一节，重定位都要成功，最后标记跳过。"""
    state, proc = run_scenario('第 2 节片源一直坏，重启重试同一节')
    out = proc.stdout or ''
    c.check(proc.returncode == 0, f'main.py 正常退出（returncode={proc.returncode}）')
    c.check('第 1 次重启浏览器后重试这一节' in out, '日志出现「第 1 次重启」')
    c.check('第 2 次重启浏览器后重试这一节' in out, '日志出现「第 2 次重启」')
    c.check(out.count('次重启后重定位：按') == 2, '两次重启后都有重定位日志')
    c.check(out.count('按序号+标题找到「第 2 节 坏片源」') == 2,
            '两次都重定位到第 2 节（序号+标题）')
    c.check('这一节已经是当前播放的' in out,
            '自动恢复正是目标小节时，走了「已是当前播放」分支而不是乱切')
    c.check('重启浏览器两次后仍然加载失败' in out, '两次重启都失败后标记跳过')
    c.check('目标小节定位失败' not in out, '本场景不应出现「目标小节定位失败」')
    # 第 2 节永远播不了，所以正确收尾是「剩下的都跳过了」而不是「全部播放完毕」
    c.check('剩下的视频都跳过了' in out, '坏片源小节被跳过后正常收工')
    c.check((out or '').count('这一节播放完毕') == 2,
            '只有第 1、3 节报告播完（重试没把别的节算到第 2 节头上）')
    done = state.done_counts()
    c.check(done == {'1': 1, '3': 1},
            f'服务端只记录第 1、3 节完成，坏片源的第 2 节从未完成：{done}')
    return out + '\n----- stderr -----\n' + (proc.stderr or '')


def scenario_2(c: Checker):
    """重启后目标小节从列表消失：必须报「目标小节定位失败」，不乱播。"""
    state, proc = run_scenario('重启后目标小节从列表消失', vanish_node='2')
    out = proc.stdout or ''
    c.check(proc.returncode == 0, f'main.py 正常退出（returncode={proc.returncode}）')
    c.check('第 1 次重启浏览器后重试这一节' in out, '日志出现「第 1 次重启」')
    c.check('目标小节定位失败' in out, '明确输出「目标小节定位失败」')
    c.check('不播自动恢复的视频' in out, '日志说明没有播自动恢复的视频')
    c.check('全部播放完毕' in out, '其余小节播完，正常收工')
    c.check((out or '').count('这一节播放完毕') == 2,
            '只有第 1、3 节报告播完（自动恢复出来的第 1 节没有被重播）')
    done = state.done_counts()
    c.check(done == {'1': 1, '3': 1},
            f'服务端只记录第 1、3 节各完成一次：{done}')
    return out + '\n----- stderr -----\n' + (proc.stderr or '')


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    c = Checker()
    outputs = []
    if only in (None, '1'):
        outputs.append(scenario_1(c))
    if only in (None, '2'):
        outputs.append(scenario_2(c))

    print()
    if c.failures:
        print(f'共 {len(c.failures)} 项未通过：')
        for f in c.failures:
            print(f'  - {f}')
        print('\n----- 程序输出（供排查） -----')
        for o in outputs:
            print(o)
        sys.exit(1)
    print('全部场景通过。')
    print('（注意：这只是本地模拟页面验证，不代表真实雨课堂链路已通过。）')


if __name__ == '__main__':
    main()
