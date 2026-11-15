"""jinan_post 采集链端到端验证（本地假 HTTP 服务 + 真实 Scrapy 引擎）

复用 tests/jinan_post_e2e.py：每个场景在独立子进程中跑一次完整爬虫，
避免 Twisted reactor 无法在同一进程重启的问题。

覆盖：
1. ok      —— 分页全部成功：条目全部产出，运行记录与改造前一致（无“采集不完整”标记）；
2. bad     —— 某一页返回坏 JSON：已抓到的条目保留，其余配置项照常采集，
              运行记录标记“采集不完整”且不判为 failed；
3. timeout —— 某一页请求超时：同样记录配置项/页偏移/原因，不中断其他配置项。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / 'jinan_post_e2e.py'
PROJECT_ROOT = HERE.parent


def run_scenario(tmp_path, scenario):
    result_file = tmp_path / f'{scenario}.json'
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), scenario, str(result_file)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=180,
    )
    assert proc.returncode == 0, (
        f'场景 {scenario} 执行失败\nSTDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-2000:]}'
    )
    assert result_file.exists(), f'场景 {scenario} 未产出结果文件'
    return json.loads(result_file.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def results(tmp_path_factory):
    base = tmp_path_factory.mktemp('jinan_e2e')
    return {scenario: run_scenario(base, scenario) for scenario in ('ok', 'bad', 'timeout')}


def test_all_success_keeps_run_complete(results):
    """路径一：全部成功时运行记录标记完整，行为与现状一致"""
    result = results['ok']
    names = sorted(item['project_name'] for item in result['items'])
    assert names == sorted(
        ['采购项目1', '采购项目2', '采购项目3', '计划项目7918', '采购项目1', '采购项目2']
    ), names

    # 七个配置项都完整跑到链尾
    assert len(result['hits']) == 8, result['hits']

    end_run = result['end_run']
    assert end_run['status'] == 'success'
    assert end_run['close_reason'] == 'finished'
    assert '[采集不完整]' not in end_run['close_reason']
    assert end_run['warning_count'] == 0
    assert result['warnings'] == []


def test_bad_page_keeps_items_and_marks_incomplete(results):
    """路径二：某一页坏 JSON 后其余配置项照常采集，运行成功但标记不完整"""
    result = results['bad']
    names = sorted(item['project_name'] for item in result['items'])
    assert names == sorted(['采购项目1', '采购项目2', '采购项目3', '计划项目7918']), names

    # 七个配置项都发起了首页请求（单页失败不影响其他配置项）
    first_pages = {kind for kind, page in result['hits'] if page == 1}
    assert len(first_pages) == 7, first_pages

    # 坏 JSON 页不重试
    assert [h for h in result['hits'] if h == ['search-type-1', 2]] == [['search-type-1', 2]]

    end_run = result['end_run']
    assert end_run['status'] == 'success', '单页失败不应把整次运行判为失败'
    assert '[采集不完整]' in end_run['close_reason']
    assert '政府采购公告' in end_run['close_reason']
    assert end_run['items_crawled'] == 4
    assert end_run['warning_count'] >= 1

    # 接口告警带配置项 + 页偏移 + 失败原因
    messages = [w['kwargs'].get('error_message', '') for w in result['warnings']]
    assert any('政府采购公告' in m and '页:2' in m and 'JSON解析失败' in m for m in messages), messages


def test_timeout_page_marks_incomplete(results):
    """路径二补充：请求超时同样记录配置项/页偏移/原因，运行仍为 success"""
    result = results['timeout']
    names = sorted(item['project_name'] for item in result['items'])
    assert names == sorted(['采购项目1', '采购项目2', '采购项目3', '计划项目7918']), names

    end_run = result['end_run']
    assert end_run['status'] == 'success'
    assert '[采集不完整]' in end_run['close_reason']
    assert '政府采购公告' in end_run['close_reason']
    assert end_run['timeout_count'] == 1
    assert end_run['warning_count'] >= 1
