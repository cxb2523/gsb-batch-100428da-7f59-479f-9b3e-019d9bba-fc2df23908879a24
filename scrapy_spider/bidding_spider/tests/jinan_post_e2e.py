"""jinan_post 采集链端到端验证脚本（本地构造假响应，不访问真实站点）

用法：
    python tests/jinan_post_e2e.py <scenario> <result.json>

scenario:
    ok      —— 所有配置项分页全部成功；
    bad     —— 政府采购公告第2页返回坏 JSON，其余配置项正常；
    timeout —— 政府采购公告第2页不响应（触发请求超时），其余配置项正常。

脚本会启动一个本地假 HTTP 服务模拟济南接口，用真实 Scrapy 引擎跑一次
jinan_post，并把条目、运行记录（end_run 参数）、接口告警写入 result.json。
"""
import datetime
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 超时场景的等待时间（秒），需大于脚本内 DOWNLOAD_TIMEOUT
SLOW_PAGE_SECONDS = 6


class ItemCollectorPipeline:
    collected = []

    def process_item(self, item, spider):
        ItemCollectorPipeline.collected.append(dict(item))
        return item


class RunRecorder:
    """替代监控数据库，记录 start_run/end_run/告警参数"""

    def __init__(self):
        self.end_calls = []
        self.warnings = []

    def start_run(self, *args, **kwargs):
        return 1

    def end_run(self, **kwargs):
        self.end_calls.append(kwargs)
        return True

    def log_interface_warning(self, *args, **kwargs):
        self.warnings.append({'args': list(args), 'kwargs': kwargs})
        return True

    def log_timeout(self, *args, **kwargs):
        return True

    def increment_items_stored(self, *args, **kwargs):
        return True

    def get_current_run_id(self):
        return 1


def _search_item(idx, date):
    return (
        '<li><span class="span1">[济南]</span>'
        f'<a href="javascript:void(0);" onclick="showview(\'ID{idx}\',1,\'招标公告\')" '
        f'title="采购项目{idx}">采购项目{idx}</a><span class="span2">{date}</span></li>'
    )


def _tendering_item(code, date):
    return (
        '<li><span class="span1"><a href="/jnggzyztb/front/tendering/info.do?code=%s" '
        'title="计划项目%s">计划项目%s</a></span>'
        '<span class="span3" style="...">%s</span></li>' % (code, code, code, date)
    )


def _api_body(html, pagesum):
    return json.dumps({'success': True, 'params': {'str': html, 'pagesum': pagesum}})


class FakeJinanHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    scenario = 'ok'
    today = ''
    hits = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        form = parse_qs(self.rfile.read(length).decode('utf-8'))
        path = self.path

        def value(name, default=''):
            return form.get(name, [default])[0]

        if '/search.do' in path:
            kind = 'search-type-%s' % value('type')
            page = int(value('pagenum', '1') or 1)
        elif '/tendering/list.do' in path:
            kind = 'tendering-type-%s' % value('type')
            page = int(value('index', '1') or 1)
        else:
            kind = 'soa'
            page = int(value('index', '1') or 1)

        type(self).hits.append([kind, page])
        scenario = type(self).scenario
        today = type(self).today

        # 第二个配置项（政府采购公告 search type=1）的第 2 页用于模拟失败
        if kind == 'search-type-1' and page == 2:
            if scenario == 'bad':
                self._send('<html>502 Bad Gateway</html>')
                return
            if scenario == 'timeout':
                time.sleep(SLOW_PAGE_SECONDS)
                self._send(_api_body('', pagesum=2))
                return

        if kind == 'search-type-1':
            body = _api_body(
                _search_item(1, today) + _search_item(2, today), pagesum=2
            )
        elif kind == 'search-type-0':  # 建设工程招标公告
            body = _api_body(_search_item(3, today), pagesum=1)
        elif kind == 'tendering-type-0':  # 建设工程招标计划
            body = _api_body(_tendering_item(7918, today), pagesum=1)
        else:  # 其余配置项正常空页
            body = _api_body('', pagesum=0)
        self._send(body)

    def _send(self, body):
        raw = body.encode('utf-8')
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    scenario = sys.argv[1]
    result_path = sys.argv[2]

    from scrapy.crawler import CrawlerProcess
    from scrapy.settings import Settings
    from bidding_spider.spiders.jinan_post import JinanPostSpider

    FakeJinanHandler.scenario = scenario
    FakeJinanHandler.today = datetime.datetime.now().strftime('%Y-%m-%d')
    FakeJinanHandler.hits = []

    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeJinanHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:%d' % server.server_address[1]

    recorder = RunRecorder()

    class LocalJinanPostSpider(JinanPostSpider):
        SEARCH_API_URL = base + '/search.do'
        TENDERING_LIST_URL = base + '/tendering/list.do'
        SOA_LIST_URL = base + '/querySoaList.do'
        allowed_domains = []
        custom_settings = {
            'ITEM_PIPELINES': {'__main__.ItemCollectorPipeline': 300},
        }

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.monitor = recorder
            self.monitor_run_id = 1

    settings = Settings()
    settings.setmodule('bidding_spider.settings', priority='project')
    settings.setdict(
        {
            'ITEM_PIPELINES': {'__main__.ItemCollectorPipeline': 300},
            'LOG_LEVEL': 'ERROR',
            'DOWNLOAD_DELAY': 0,
            'RANDOMIZE_DOWNLOAD_DELAY': False,
            'AUTOTHROTTLE_ENABLED': False,
            'CONCURRENT_REQUESTS': 8,
            'CONCURRENT_REQUESTS_PER_DOMAIN': 8,
            'RETRY_ENABLED': False,
            'DOWNLOAD_TIMEOUT': 2,
            'TELNETCONSOLE_ENABLED': False,
            'CLOSESPIDER_TIMEOUT': 60,
        },
        priority='cmdline',
    )

    process = CrawlerProcess(settings)
    process.crawl(LocalJinanPostSpider)
    process.start(stop_after_crawl=True)

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)

    result = {
        'scenario': scenario,
        'items': [
            {'project_name': item.get('project_name'), 'publish_date': str(item.get('publish_date'))}
            for item in ItemCollectorPipeline.collected
        ],
        'end_run': recorder.end_calls[-1] if recorder.end_calls else None,
        'warnings': recorder.warnings,
        'hits': FakeJinanHandler.hits,
    }
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
