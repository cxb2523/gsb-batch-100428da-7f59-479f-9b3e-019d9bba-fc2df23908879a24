import logging
from typing import TypedDict
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag
from requests import Session

from .SSO_error import (BackendError, UnionAuthError, UnknownPageError,
                        LoginFormNotFoundError, LoginFormFieldMissingError)
from .http_retry import get_with_retry, DEFAULT_MAX_ATTEMPTS, DEFAULT_RETRY_DELAY
from .sso_rsa import extract_login_page_rsa_public_key, rsa_encrypt_username_password

ua = '''Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'''
# target = '''https://pass.neu.edu.cn/tpass/login?service=https%3A%2F%2Fipgw.neu.edu.cn%2Fsrun_cas.php%3Fac_id%3D1'''
target = '''https://pass.neu.edu.cn/tpass/login?service=http%3A%2F%2Fipgw.neu.edu.cn%2Fsrun_portal_sso%3Fac_id%3D1'''


class PageTitle:
    UnionAuth = "智慧东大--统一身份认证"
    SystemHint = "系统提示"
    EmailVerification = "智慧东大"  # 出现这个标题意味着用户需要进行邮箱认证
    IPGWControlGateway = "IP控制网关"


class SSOPage(TypedDict):
    form_lt_string: str
    form_execution: str
    form_destination: str
    rsa_public_key: str


# 从页面中提取一小段可定位问题的线索：优先页面标题，否则取正文前 200 个字符。
def _page_clue(page_soup: BeautifulSoup, page_text: str) -> str:
    title_soup = page_soup.find("title")
    if title_soup and title_soup.text.strip():
        return f"页面标题为“{title_soup.text.strip()}”"
    return f"页面内容片段：{page_text.strip()[:200]!r}"


# 准备一个SSO内容，里面有
def SSO_prepare(session: Session, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                retry_delay: float = DEFAULT_RETRY_DELAY) -> SSOPage:
    response = get_with_retry(session, target, "获取统一身份认证登录页面",
                              max_attempts=max_attempts, retry_delay=retry_delay)
    page_text = response.text
    page_soup = BeautifulSoup(page_text, "html.parser")
    form: Tag = page_soup.find("form", {'id': 'loginForm'})
    if form is None:
        raise LoginFormNotFoundError(clue=_page_clue(page_soup, page_text))

    lt_input = form.find("input", {'id': 'lt'})
    if lt_input is None or "value" not in lt_input.attrs:
        raise LoginFormFieldMissingError("lt", clue=f"表单内容片段：{str(form)[:200]}")
    execution_input = form.find("input", {'name': 'execution'})
    if execution_input is None or "value" not in execution_input.attrs:
        raise LoginFormFieldMissingError("execution", clue=f"表单内容片段：{str(form)[:200]}")
    form_action = form.attrs.get("action")
    if not form_action:
        raise LoginFormFieldMissingError("action", clue=f"表单内容片段：{str(form)[:200]}")

    sso_form_data = {
        "form_lt_string": lt_input.attrs["value"],
        "form_destination": form_action,
        "form_execution": execution_input.attrs["value"],
        "rsa_public_key": extract_login_page_rsa_public_key(session, page_soup,
                                                            max_attempts=max_attempts,
                                                            retry_delay=retry_delay)
    }
    logging.debug(f"sso_form_data: {sso_form_data}")
    return sso_form_data


# 请求SSO和认证SSO两个操作合并到一个API接口中，直接操作。返回一个SSO ticket。这个函数会触发异常
def SSO_login(session: Session, page: SSOPage, username, password, ac_id) -> str:
    form_data = {
        'rsa': rsa_encrypt_username_password(username, password, page["rsa_public_key"]),
        'ul': len(username),
        'pl': len(password),
        'lt': page['form_lt_string'],
        'execution': page['form_execution'],
        '_eventId': 'submit'
    }
    login_first_result = session.post("https://pass.neu.edu.cn" + page['form_destination'], data=form_data, allow_redirects=True)  # 允许跳转
    login_first_result_soup = BeautifulSoup(login_first_result.text, "html.parser")
    title_soup = login_first_result_soup.find("title")
    logging.debug("page_title: " + title_soup.text if title_soup else "<no data>")
    if not title_soup:
        # 问题出现！ 这个问题可能是后端返回了一个redis错误的页面
        raise BackendError(login_first_result_soup.text)
    else:
        response_page_title = title_soup.text
        if response_page_title == PageTitle.IPGWControlGateway:
            # 登录成功！
            return parse_qs(urlparse(login_first_result.url).query)['ticket'][0]
        elif response_page_title == PageTitle.UnionAuth:
            # 用户名或密码错误
            raise UnionAuthError()
        elif response_page_title == PageTitle.EmailVerification:
            # 这个页面可能会出现，因为目标未绑定邮箱。我们这里的处理方法就是简单跳过。
            logging.warning("帐号未绑定邮箱")
            login_skip_email_result = session.get(
                f"https://pass.neu.edu.cn/tpass/login?service=http%3A%2F%2Fipgw.neu.edu.cn%2Fsrun_portal_sso%3Fac_id%3D{ac_id}", allow_redirects=True)
            logging.debug(f"skip_email")
            return parse_qs(urlparse(login_skip_email_result.url).query)['ticket'][0]
        else:
            raise UnknownPageError(login_first_result_soup)
