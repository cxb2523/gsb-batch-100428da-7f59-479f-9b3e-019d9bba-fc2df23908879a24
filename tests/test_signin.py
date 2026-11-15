import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from ehentai import config, signin


def response(html, set_cookies=()):
    headers = SimpleNamespace(getlist=lambda name: list(set_cookies))
    return SimpleNamespace(text=html, raw=SimpleNamespace(headers=headers))


def test_cookie_update_persists_and_notifies(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    path.write_text(
        "# keep comment\nE_COOKIE='ipb_member_id=123; event=old; other=value'\n"
        "Notify='{\"GOTIFY_URL\":\"https://example.com\",\"GOTIFY_TOKEN\":\"token\"}'\n",
        encoding='utf-8',
    )
    cookies = [
        'event=new; Path=/',
        'sk=abc; expires=Wed, 21 Oct 2037 07:28:00 GMT; Path=/',
    ]
    send = Mock()
    monkeypatch.setattr(signin, 'read_env', lambda: config.read_env(path))
    monkeypatch.setattr(signin, 'save_cookie', lambda value: config.save_cookie(value, path))
    monkeypatch.setattr(signin, 'request_page', lambda: response('<div id="eventpane"><p>Reward</p></div>', cookies))
    monkeypatch.setattr(signin.notify, 'send', send)
    with patch.dict(os.environ, {}, clear=True):
        assert signin.scrape() == ['Reward']
        saved = config.read_env(path)
        assert saved['E_COOKIE'] == 'ipb_member_id=123; event=new; other=value; sk=abc'
        assert '# keep comment' in path.read_text(encoding='utf-8')
        assert send.call_count == 2
        assert 'sk 过期时间' in send.call_args_list[0].args[1]
        assert send.call_args_list[1].args[1] == '签到成功！\nReward'
        signin.init_config()
        assert signin.headers['Cookie'] == saved['E_COOKIE']


@pytest.mark.parametrize(('html', 'expected', 'message'), [
    ('<html></html>', '没有事件，已经签到了！', '没有事件，已经签到了！'),
    ('<div id="eventpane">Random Encounter</div>', 'Random Encounter', 'Random Encounter'),
    ('<div id="eventpane"><p>Prize</p></div>', ['Prize'], '签到成功！'),
])
def test_signin_results(html, expected, message, monkeypatch):
    send = Mock()
    monkeypatch.setattr(signin, 'init_config', Mock())
    monkeypatch.setattr(signin, 'request_page', lambda: response(html))
    monkeypatch.setattr(signin, 'send_notify', send)
    assert signin.scrape() == expected
    assert message in send.call_args.args[1]



@pytest.mark.parametrize('reward_html', [
    'You gain <strong>131</strong> EXP, <strong>397</strong> Credits!',
    'You gain<span>131</span><b>EXP</b>,<span>397</span><b>Credits</b>!',
])
def test_reward_text_keeps_spaces_around_inline_values(reward_html, monkeypatch):
    html = (
        '<div id="eventpane"><p>It is the dawn of a new day!</p>'
        '<p>Reflecting on your journey so far, you find that you are a little wiser.</p>'
        f'<p>{reward_html}</p></div>'
    )
    send = Mock()
    monkeypatch.setattr(signin, 'init_config', Mock())
    monkeypatch.setattr(signin, 'request_page', lambda: response(html))
    monkeypatch.setattr(signin, 'send_notify', send)
    lines = signin.scrape()
    assert lines[-1] == 'You gain 131 EXP, 397 Credits!'
    assert send.call_args.args[1] == '签到成功！\n' + '\n'.join(lines)


def test_request_errors_notify_and_propagate(monkeypatch):
    send = Mock()
    monkeypatch.setattr(signin, 'init_config', Mock())
    monkeypatch.setattr(signin, 'request_page', Mock(side_effect=requests.ConnectionError('failed')))
    monkeypatch.setattr(signin, 'send_notify', send)
    with pytest.raises(requests.ConnectionError):
        signin.scrape()
    assert send.call_args.args[0] == '请求错误'


def test_cookie_save_failure_keeps_signin_result_and_notifies(tmp_path, monkeypatch):
    path = tmp_path / '.env'
    original_cookie = 'ipb_member_id=123456789; event=oldevent1'
    path.write_text(
        f"E_COOKIE='{original_cookie}'\n"
        "Notify='{\"GOTIFY_URL\":\"https://example.com\",\"GOTIFY_TOKEN\":\"token\"}'\n",
        encoding='utf-8',
    )
    cookies = [
        'event=newevent2; Path=/',
        'sk=secretkey987; expires=Wed, 21 Oct 2037 07:28:00 GMT; Path=/',
    ]
    send = Mock()
    monkeypatch.setattr(signin, 'read_env', lambda: config.read_env(path))
    monkeypatch.setattr(signin, 'save_cookie', Mock(side_effect=PermissionError('access denied')))
    monkeypatch.setattr(signin, 'request_page',
                        lambda: response('<div id="eventpane"><p>Reward</p></div>', cookies))
    monkeypatch.setattr(signin.notify, 'send', send)
    with patch.dict(os.environ, {}, clear=True):
        # 写回失败不抛出，签到结果照常返回。
        assert signin.scrape() == ['Reward']

        # .env 与内存中的 Cookie 均保持原样。
        assert config.read_env(path)['E_COOKIE'] == original_cookie
        assert os.environ['E_COOKIE'] == original_cookie
        assert signin.headers['Cookie'] == original_cookie

        titles = [call.args[0] for call in send.call_args_list]
        assert titles == ['Ehentai SignIn - Cookie 更新失败', 'Ehentai SignIn - 签到结果']

        failure_body = send.call_args_list[0].args[1]
        assert 'PermissionError: access denied' in failure_body
        # 失败字段名保留，值必须脱敏，明文不得出现在通知中。
        assert '- event=' in failure_body
        assert '- sk=' in failure_body
        assert 'newevent2' not in failure_body
        assert 'secretkey987' not in failure_body
        assert 'new***nt2' in failure_body
        assert 'sec******987' in failure_body
        # 写回失败时不再发送过期时间提醒。
        assert '过期时间' not in failure_body

        assert send.call_args_list[1].args[1] == '签到成功！\nReward'


def test_cookie_save_failure_reports_changed_fields(monkeypatch):
    monkeypatch.setattr(signin, 'send_notify', Mock())
    monkeypatch.setattr(signin, 'save_cookie', Mock(side_effect=OSError('disk full')))
    with patch.dict(os.environ, {'E_COOKIE': 'event=old; other=keep'}, clear=True):
        with pytest.raises(signin.CookieSaveError) as exc_info:
            signin.update_cookie({'event': 'newvalue', 'sk': 'skvalue'}, [])
        error = exc_info.value
        assert 'OSError: disk full' == error.reason
        assert dict(error.fields) == {'event': 'newvalue', 'sk': 'skvalue'}
        # 失败后内存 Cookie 不变。
        assert os.environ['E_COOKIE'] == 'event=old; other=keep'


def test_no_set_cookie_path_does_not_report_save_failure(monkeypatch):
    send = Mock()
    monkeypatch.setattr(signin, 'init_config', Mock())
    monkeypatch.setattr(signin, 'request_page',
                        lambda: response('<div id="eventpane"><p>Reward</p></div>'))
    save_cookie = Mock()
    monkeypatch.setattr(signin, 'save_cookie', save_cookie)
    monkeypatch.setattr(signin, 'send_notify', send)
    assert signin.scrape() == ['Reward']
    save_cookie.assert_not_called()
    assert send.call_count == 1
    assert send.call_args.args[0] == '签到结果'


@pytest.mark.parametrize(('value', 'expected'), [
    ('', {}), ('{}', {}), ('{"HITOKOTO":false}', {'HITOKOTO': 'false'}),
])
def test_notification_configuration(value, expected):
    assert signin.parse_notify_config(value) == expected


@pytest.mark.parametrize('value', ['bad-json', '[]', '{"unknown":1}', '{"HITOKOTO":null}'])
def test_notification_configuration_validation(value):
    with pytest.raises(ValueError):
        signin.parse_notify_config(value)
