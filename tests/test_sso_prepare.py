# SSO_prepare 与 RSA 公钥提取的可诊断、可重试流程测试。
# 使用 FakeSession 模拟网络层，不发起真实请求。
import pytest
from requests.exceptions import ConnectionError, Timeout

from ipgw.core.api.SSO import SSO_prepare
from ipgw.core.api.SSO_error import (
    LoginFormNotFoundError,
    LoginFormFieldMissingError,
    LoginScriptNotFoundError,
    PublicKeyNotFoundError,
    SSONetworkError,
    SSOPrepareError,
)

LOGIN_JS_URL = "https://pass.neu.edu.cn/tpass/resources/js/login_neu.js"

VALID_PAGE = """
<html><head><title>智慧东大--统一身份认证</title>
<script src="/tpass/resources/js/login_neu.js"></script>
</head><body>
<form id="loginForm" action="/tpass/login;jsessionid=ABC123">
  <input id="lt" name="lt" value="LT-123456"/>
  <input name="execution" value="e1s1"/>
</form>
</body></html>
"""

VALID_LOGIN_JS = 'var publicKeyStr = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA";\nencrypt(publicKeyStr);'

PAGE_NO_FORM = """
<html><head><title>系统提示</title></head>
<body><p>抱歉！您的请求出现了异常，请稍后再试。</p></body></html>
"""

PAGE_NO_LT = """
<html><body>
<form id="loginForm" action="/tpass/login">
  <input name="execution" value="e1s1"/>
</form>
<script src="/tpass/resources/js/login_neu.js"></script>
</body></html>
"""

PAGE_NO_EXECUTION = """
<html><body>
<form id="loginForm" action="/tpass/login">
  <input id="lt" name="lt" value="LT-123456"/>
</form>
<script src="/tpass/resources/js/login_neu.js"></script>
</body></html>
"""

PAGE_NO_ACTION = """
<html><body>
<form id="loginForm">
  <input id="lt" name="lt" value="LT-123456"/>
  <input name="execution" value="e1s1"/>
</form>
<script src="/tpass/resources/js/login_neu.js"></script>
</body></html>
"""

PAGE_NO_SCRIPT = """
<html><body>
<form id="loginForm" action="/tpass/login">
  <input id="lt" name="lt" value="LT-123456"/>
  <input name="execution" value="e1s1"/>
</form>
<script src="/tpass/resources/js/jquery.min.js"></script>
</body></html>
"""

JS_NO_PUBLIC_KEY = "function encrypt() { return 1; }"


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeSession:
    """按 URL 路由返回响应；fail_times 控制前 N 次请求抛出网络异常。"""

    def __init__(self, routes, fail_times=0, fail_with=ConnectionError):
        self.routes = routes
        self.fail_times = fail_times
        self.fail_with = fail_with
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if len(self.calls) <= self.fail_times:
            raise self.fail_with(f"simulated transient failure for {url}")
        for prefix, text in self.routes.items():
            if url.startswith(prefix):
                return FakeResponse(text)
        raise AssertionError(f"unexpected url requested: {url}")


def make_session(page=VALID_PAGE, script=VALID_LOGIN_JS, **kwargs):
    return FakeSession({
        "https://pass.neu.edu.cn/tpass/login": page,
        LOGIN_JS_URL: script,
    }, **kwargs)


# --- 正常流程 ---

def test_prepare_success_extracts_form_and_public_key():
    session = make_session()
    page = SSO_prepare(session, retry_delay=0)
    assert page["form_lt_string"] == "LT-123456"
    assert page["form_execution"] == "e1s1"
    assert page["form_destination"] == "/tpass/login;jsessionid=ABC123"
    assert page["rsa_public_key"] == "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA"


# --- 确定性错误：分类明确、不重试 ---

def test_missing_form_raises_form_not_found():
    session = make_session(page=PAGE_NO_FORM)
    with pytest.raises(LoginFormNotFoundError) as exc_info:
        SSO_prepare(session, retry_delay=0)
    # 保留页面线索（标题）以便定位
    assert "系统提示" in str(exc_info.value)
    # 确定性错误不应重试：只请求了一次页面
    assert len(session.calls) == 1


def test_missing_lt_field_raises_field_missing():
    session = make_session(page=PAGE_NO_LT)
    with pytest.raises(LoginFormFieldMissingError) as exc_info:
        SSO_prepare(session, retry_delay=0)
    assert exc_info.value.field_name == "lt"
    assert len(session.calls) == 1


def test_missing_execution_field_raises_field_missing():
    session = make_session(page=PAGE_NO_EXECUTION)
    with pytest.raises(LoginFormFieldMissingError) as exc_info:
        SSO_prepare(session, retry_delay=0)
    assert exc_info.value.field_name == "execution"


def test_missing_form_action_raises_field_missing():
    session = make_session(page=PAGE_NO_ACTION)
    with pytest.raises(LoginFormFieldMissingError) as exc_info:
        SSO_prepare(session, retry_delay=0)
    assert exc_info.value.field_name == "action"


def test_missing_login_script_raises_script_not_found():
    session = make_session(page=PAGE_NO_SCRIPT)
    with pytest.raises(LoginScriptNotFoundError) as exc_info:
        SSO_prepare(session, retry_delay=0)
    # 线索中列出页面实际引用的脚本，便于发现脚本改名
    assert "jquery.min.js" in str(exc_info.value)


def test_missing_public_key_raises_public_key_not_found():
    session = make_session(script=JS_NO_PUBLIC_KEY)
    with pytest.raises(PublicKeyNotFoundError) as exc_info:
        SSO_prepare(session, retry_delay=0)
    assert LOGIN_JS_URL in str(exc_info.value)


def test_all_prepare_errors_share_common_base():
    for exc_type in (LoginFormNotFoundError, LoginFormFieldMissingError,
                     LoginScriptNotFoundError, PublicKeyNotFoundError, SSONetworkError):
        assert issubclass(exc_type, SSOPrepareError)


# --- 临时性网络失败：有限次重试 ---

def test_transient_failure_succeeds_after_retry():
    session = make_session(fail_times=2, fail_with=Timeout)
    page = SSO_prepare(session, max_attempts=3, retry_delay=0)
    assert page["form_lt_string"] == "LT-123456"
    # 页面请求：两次失败 + 第三次成功；随后还有一次脚本请求
    page_calls = [u for u in session.calls if "tpass/login" in u]
    assert len(page_calls) == 3


def test_transient_failure_on_script_fetch_succeeds_after_retry():
    # 页面第一次就成功，脚本请求前两次失败
    class ScriptFlakySession(FakeSession):
        def get(self, url, **kwargs):
            if url.startswith(LOGIN_JS_URL) and self.calls.count(LOGIN_JS_URL) < 2:
                self.calls.append(url)
                raise ConnectionError("script fetch flaky")
            return super().get(url, **kwargs)

    session = ScriptFlakySession({
        "https://pass.neu.edu.cn/tpass/login": VALID_PAGE,
        LOGIN_JS_URL: VALID_LOGIN_JS,
    })
    page = SSO_prepare(session, max_attempts=3, retry_delay=0)
    assert page["rsa_public_key"] == "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA"


def test_persistent_failure_exhausts_retries_with_clear_reason():
    session = make_session(fail_times=99)
    with pytest.raises(SSONetworkError) as exc_info:
        SSO_prepare(session, max_attempts=3, retry_delay=0)
    err = exc_info.value
    assert err.attempts == 3
    assert isinstance(err.last_error, ConnectionError)
    assert "重试" in str(err)
    # 恰好尝试了 max_attempts 次，没有多余请求
    assert len(session.calls) == 3
