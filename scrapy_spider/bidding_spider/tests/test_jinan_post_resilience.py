"""jinan_post 采集链容错行为测试

用本地构造的假响应验证三条路径：
1. 分页全部成功：翻页正常、配置项标记完整、运行记录保持 success；
2. 某一页请求超时 / 返回坏 JSON：记录配置项+页偏移+失败原因，已抓到的条目保留，
   其余配置项照常采集，整次运行不判为 failed；
3. 全部配置项成功跑完时，运行记录不出现“采集不完整”标记。
"""
import json

import scrapy
from scrapy.http import TextResponse
from twisted.internet.error import TimeoutError as TwistedTimeoutError
from twisted.python.failure import Failure

from bidding_spider.spiders.jinan_post import JinanPostSpider

TODAY = '2026-11-15'


def make_spider():
    spider = JinanPostSpider()
    # 单元测试不连监控数据库
    spider.monitor = None
    spider.monitor_run_id = None
    return spider


def init_states(spider):
    """模拟 start_requests 执行，初始化各配置项状态"""
    list(spider.start_requests())
    return spider._get_configs()


def search_html_item(idx, date=TODAY):
    return (
        '<li><span class="span1">[济南]</span>'
        f'<a href="javascript:void(0);" onclick="showview(\'ID{idx}\',1,\'招标公告\')" '
        f'title="测试项目{idx}">测试项目{idx}</a><span class="span2">{date}</span></li>'
    )


def search_body(ids, pagesum, date=TODAY):
    html = ''.join(search_html_item(i, date) for i in ids)
    return json.dumps({'success': True, 'params': {'str': html, 'pagesum': pagesum}})


def page_request(config, page_num):
    payload = dict(config['payload'])
    if config['api_type'] in ('tendering', 'soa'):
        payload['index'] = str(page_num)
    else:
        payload['pagenum'] = str(page_num)
    return scrapy.FormRequest(
        url=config['url'],
        formdata=payload,
        meta={
            'config': config,
            'today_date': TODAY,
            'page_num': page_num,
            'item_index': 0,
        },
    )


def make_response(request, body):
    return TextResponse(
        url=request.url,
        body=body.encode('utf-8'),
        encoding='utf-8',
        status=200,
        request=request,
    )


def drive_chain(spider, config, page_bodies, fail_pages=None):
    """手工驱动某个配置项的分页链

    Args:
        page_bodies: {页偏移: 响应 body 文本}
        fail_pages: {页偏移: 异常实例}，模拟请求最终失败走到 errback

    Returns:
        (items, failure_request_pages) —— 已抓到的条目、触发失败的页
    """
    fail_pages = fail_pages or {}
    items = []
    failed_requests = []
    guard = 0

    request = page_request(config, 1)
    while request is not None:
        guard += 1
        assert guard < 50, '翻页链未收敛'
        page = request.meta['page_num']

        if page in fail_pages:
            failure = Failure(fail_pages[page])
            failure.request = request
            spider.handle_error(failure)
            failed_requests.append(page)
            break

        assert page in page_bodies, f'缺少第{page}页假响应'
        response = make_response(request, page_bodies[page])
        results = list(spider.parse_api_response(response))

        next_request = None
        for result in results:
            if isinstance(result, scrapy.Item):
                items.append(result)
            elif getattr(result, 'meta', None) is not None:
                next_request = result
        request = next_request

    return items, failed_requests


class FakeMonitor:
    """记录 end_run 参数，避免测试依赖数据库"""

    def __init__(self):
        self.end_run_calls = []

    def end_run(self, **kwargs):
        self.end_run_calls.append(kwargs)
        return True

    def log_interface_warning(self, *args, **kwargs):
        return True

    def log_timeout(self, *args, **kwargs):
        return True


def close_and_capture(spider, reason='finished'):
    monitor = FakeMonitor()
    spider.monitor = monitor
    spider.monitor_run_id = 1
    spider.closed(reason)
    assert monitor.end_run_calls, 'closed() 未写入运行结束记录'
    return monitor.end_run_calls[-1]


def test_seven_configs_defined():
    spider = make_spider()
    assert len(spider._get_configs()) == 7


def test_all_pages_success():
    """路径一：分页全部成功，条目全部产出且配置项标记完整"""
    spider = make_spider()
    configs = init_states(spider)
    search_config = configs[0]

    items, failed = drive_chain(
        spider,
        search_config,
        page_bodies={
            1: search_body([1, 2], pagesum=3),
            2: search_body([3, 4], pagesum=3),
            3: search_body([5], pagesum=3),
        },
    )

    assert failed == []
    assert [item['project_name'] for item in items] == [
        '测试项目1', '测试项目2', '测试项目3', '测试项目4', '测试项目5'
    ]
    assert spider.page_failures == []
    assert spider.config_states[search_config['name']] == 'completed'


def test_bad_json_page_records_failure_and_keeps_other_configs():
    """路径二：某一页返回坏 JSON，记录配置项/页偏移/原因，其余配置项照常采集"""
    spider = make_spider()
    configs = init_states(spider)
    search_config = configs[0]       # 政府采购公告（search）
    other_config = configs[1]        # 建设工程招标计划（tendering）

    # 第1页成功抓到 2 条，第2页返回坏 JSON
    items, failed = drive_chain(
        spider,
        search_config,
        page_bodies={
            1: search_body([1, 2], pagesum=3),
            2: '<html>not a json</html>',
        },
    )

    assert failed == []
    assert len(items) == 2, '已抓到条目应保留，不得丢弃'
    assert spider.config_states[search_config['name']] == 'incomplete'
    assert len(spider.page_failures) == 1
    failure = spider.page_failures[0]
    assert failure['config'] == '政府采购公告'
    assert failure['page'] == 2
    assert failure['type'] == 'json_error'
    assert 'JSON解析失败' in failure['reason']

    # 坏 JSON 页不会继续翻页（不做无限重试）
    assert spider.page_failures[-1]['page'] == 2

    # 其他配置项照常采集
    other_items, other_failed = drive_chain(
        spider,
        other_config,
        page_bodies={1: json.dumps({'success': True, 'params': {'str': '', 'pagesum': 0}})},
    )
    assert other_failed == []
    assert other_items == []
    assert spider.config_states[other_config['name']] == 'completed'

    # 整次运行仍为 success，但 close_reason 留下可核对的“采集不完整”信息
    end_run = close_and_capture(spider, reason='finished')
    assert end_run['status'] == 'success', '单页失败不应把整次运行判为失败'
    assert '[采集不完整]' in end_run['close_reason']
    assert '政府采购公告' in end_run['close_reason']
    assert end_run['warning_count'] == 1, '失败页数应计入 warning_count'


def test_timeout_page_records_failure():
    """路径二补充：请求最终超时（errback），同样记录配置项/页偏移/原因并停止该链"""
    spider = make_spider()
    configs = init_states(spider)
    other_config = configs[2]  # 建设工程招标公告

    drive_chain(
        spider,
        other_config,
        page_bodies={1: search_body([1], pagesum=2)},
        fail_pages={2: TwistedTimeoutError('request timed out')},
    )

    assert len(spider.page_failures) == 1
    failure = spider.page_failures[0]
    assert failure['config'] == '建设工程招标公告'
    assert failure['page'] == 2
    assert failure['type'] == 'timeout'
    assert spider.config_states[other_config['name']] == 'incomplete'
    assert spider.timeout_errors == 1

    end_run = close_and_capture(spider)
    assert end_run['status'] == 'success'
    assert '[采集不完整]' in end_run['close_reason']
    assert end_run['timeout_count'] == 1
    assert end_run['warning_count'] == 1


def test_all_configs_success_marks_run_complete():
    """路径三：全部配置项完整跑完，运行记录不出现不完整标记，行为与现状一致"""
    spider = make_spider()
    configs = init_states(spider)

    # 每个配置项都返回空页（正常结束路径）
    empty_body = json.dumps({'success': True, 'params': {'str': '', 'pagesum': 0}})
    for config in configs:
        items, failed = drive_chain(spider, config, page_bodies={1: empty_body})
        assert items == []
        assert failed == []

    assert spider.get_incomplete_configs() == []
    assert set(spider.config_states.values()) == {'completed'}

    end_run = close_and_capture(spider, reason='finished')
    assert end_run['status'] == 'success'
    assert end_run['close_reason'] == 'finished'
    assert '[采集不完整]' not in end_run['close_reason']
    assert end_run['warning_count'] == 0


def test_closed_keeps_failed_status_for_abnormal_close():
    """非正常关闭原因仍沿用原有 failed 判定，但同样携带采集完整性信息"""
    spider = make_spider()
    configs = init_states(spider)
    drive_chain(
        spider,
        configs[0],
        page_bodies={1: search_body([1], pagesum=2)},
        fail_pages={2: TwistedTimeoutError('request timed out')},
    )

    end_run = close_and_capture(spider, reason='shutdown')
    assert end_run['status'] == 'failed'
    assert '[采集不完整]' in end_run['close_reason']


def test_page_failure_after_config_completed_not_regressed():
    """配置项已完整跑完后，迟到告警不应把它误标为 incomplete"""
    spider = make_spider()
    configs = init_states(spider)
    config = configs[0]

    drive_chain(spider, config, page_bodies={1: search_body([1], pagesum=1)})
    assert spider.config_states[config['name']] == 'completed'

    # 异常场景：迟到的告警仍会记录明细，但不改变已完成的配置项状态
    spider._record_page_failure(config, 5, 'json_error', 'late failure')
    assert len(spider.page_failures) == 1
    assert spider.config_states[config['name']] == 'completed'
