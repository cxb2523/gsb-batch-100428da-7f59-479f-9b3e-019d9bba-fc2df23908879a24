#!/usr/bin/env python
#-*- coding: utf-8 -*-

__author__ = 'ZYSzys'

import requests
import re
import os
import sys
import tempfile
import urllib
import getpass
from lxml import etree
from PIL import Image
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# 状态与异常定义
# ---------------------------------------------------------------------------

STATUS_SUCCESS = u'成功'
STATUS_FAILED = u'失败'
STATUS_SKIPPED = u'未执行'

EXIT_SUCCESS = 0
EXIT_SEGMENT_FAILED = 1
EXIT_LOGIN_FAILED = 2


class SpiderError(Exception):
    '''采集过程中所有可预期错误的基类，category 用于区分失败原因类别。'''
    category = u'未知错误'


class LoginExpiredError(SpiderError):
    '''登录失效：会话过期或被服务端踢回登录页。'''
    category = u'登录失效'


class StructureError(SpiderError):
    '''页面结构不符：返回的 HTML 与解析器预期的结构不一致。'''
    category = u'页面结构不符'


class NetworkError(SpiderError):
    '''网络请求异常：连接超时、DNS 失败、连接被重置等 requests 层错误。'''
    category = u'网络请求异常'


class LoginFailedError(SpiderError):
    '''登录阶段失败（账号/密码/验证码错误等）。'''
    category = u'登录失败'


class SegmentResult:
    '''单个采集段的可核对结局。'''

    def __init__(self, name, status, reason=u''):
        self.name = name
        self.status = status
        self.reason = reason

    def format_line(self):
        if self.status == STATUS_SUCCESS:
            return u'[%s] %s' % (self.name, STATUS_SUCCESS)
        if self.status == STATUS_SKIPPED:
            return u'[%s] %s - 原因：%s' % (self.name, STATUS_SKIPPED, self.reason)
        return u'[%s] %s - 失败原因：%s' % (self.name, STATUS_FAILED, self.reason)


class Who:
    def __init__(self, user, pswd):
        self.user = user
        self.pswd = pswd


class Tool:
    rma = re.compile('<a href=.*?>|</a>')
    rmtb = re.compile('<br />|</br>|<br>')
    rmtr = re.compile('<td>|</td>|<tr>|</tr>|<tr class="alt">|<tr class="datelisthead">')
    rmtime1 = re.compile('<td align="Center" width="7%">.*?</td>')
    rmtime2 = re.compile('<td class="noprint" align="Center".*?>.*?</td>')

    def replace(self, x):
        x = re.sub(self.rma, '   ', x)
        x = re.sub(self.rmtb, '---', x)
        x = re.sub(self.rmtr, '  ', x)
        x = re.sub(self.rmtime1, '\n', x)
        x = re.sub(self.rmtime2, '', x)
        return x.strip()


# ---------------------------------------------------------------------------
# 纯解析函数：只负责把 HTML 解析成结构化数据，结构不符时抛 StructureError
# ---------------------------------------------------------------------------

def Getgrade(html):
    soup = BeautifulSoup(html, 'lxml')
    table = soup.find(id="Datagrid1")
    if table is None:
        raise StructureError(u'成绩页面未找到 id="Datagrid1" 的成绩表格')
    trs = table.find_all("tr")
    if not trs:
        raise StructureError(u'成绩表格 Datagrid1 中没有任何行')
    try:
        Grades = []
        keys = []
        tds = trs[0].find_all("td")
        tds = tds[:2] + tds[3:5] + tds[6:9]
        if not tds:
            raise StructureError(u'成绩表格缺少表头单元格')
        for td in tds:
            keys.append(td.string)
        for tr in trs[1:]:
            tds = tr.find_all("td")
            tds = tds[:2] + tds[3:5] + tds[6:9]
            values = []
            for td in tds:
                values.append(td.string)
            one = dict((key, value) for key, value in zip(keys, values))
            Grades.append(one)
        if not Grades:
            raise StructureError(u'成绩表格中没有任何成绩数据行')
        return Grades
    except StructureError:
        raise
    except (AttributeError, IndexError, TypeError) as e:
        raise StructureError(u'成绩表格内容无法解析：%s' % e)


def Getgradetestresults(trs):
    if not trs:
        raise StructureError(u'等级考试页面未找到 class="datelist" 的成绩表格')
    try:
        results = []
        k = trs[0].xpath('.//td/text()')
        if not k:
            raise StructureError(u'等级考试成绩表格缺少表头')
        trs = trs[1:]
        if not trs:
            raise StructureError(u'等级考试成绩表格中没有任何数据行')
        for tr in trs:
            tds = tr.xpath('.//td/text()')
            v = []
            for td in tds:
                v.append(td)
            one = dict((i, j) for i, j in zip(k, v))
            results.append(one)
        return results
    except StructureError:
        raise
    except (AttributeError, IndexError, TypeError) as e:
        raise StructureError(u'等级考试成绩表格内容无法解析：%s' % e)


# ---------------------------------------------------------------------------
# zhengfang.txt 的分段读写
# ---------------------------------------------------------------------------

SUMMARY_TITLE = u'采集分段结局:'
SECTION_TITLES = {
    u'本学期课表:': u'课表',
    u'历年成绩:': u'历年成绩',
    u'等级考试成绩:': u'等级考试成绩',
}
# 段名、表格标题、采集方法名
SEGMENTS = (
    (u'课表', u'本学期课表:', 'fetch_class'),
    (u'历年成绩', u'历年成绩:', 'fetch_grade'),
    (u'等级考试成绩', u'等级考试成绩:', 'fetch_test_results'),
)


def normalize_block(block):
    '''统一块格式：去掉首尾空行，保证以一个换行结尾。'''
    return block.strip('\r\n') + '\n'


def read_sections(path):
    '''读取已有 zhengfang.txt，按表格标题切成 {段名: 块文本}。

    旧的“采集分段结局”区会被丢弃，便于每次运行重新生成。
    文件不存在或无法读取时返回空 dict，不影响本次采集。
    '''
    sections = {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (IOError, OSError):
        return sections

    idx = text.find(SUMMARY_TITLE)
    if idx != -1:
        text = text[:idx]

    current = None
    buf = []
    for line in text.split('\n'):
        name = SECTION_TITLES.get(line.strip())
        if name is not None:
            if current is not None:
                sections[current] = '\n'.join(buf)
            current = name
            buf = [line.strip()]
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = '\n'.join(buf)

    return dict((k, normalize_block(v)) for k, v in sections.items())


def assemble_output(sections, results):
    '''按固定段顺序拼装完整文件内容，段间保留原有空行风格。'''
    blocks = []
    for name, _title, _method in SEGMENTS:
        block = sections.get(name)
        if block:
            blocks.append(normalize_block(block))

    parts = []
    for i, block in enumerate(blocks):
        parts.append(block if i == 0 else '\n\n\n' + block)

    summary = [u'', u'', u'', SUMMARY_TITLE]
    for r in results:
        summary.append(r.format_line())
    if blocks:
        parts.append('\n'.join(summary) + '\n')
    else:
        parts.append('\n'.join(summary[3:]) + '\n')
    return ''.join(parts)


def atomic_write(path, content):
    '''先写同目录临时文件再原子替换，保证任何失败都不会留下半截文件。'''
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(prefix='.zhengfang.', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# 采集主体
# ---------------------------------------------------------------------------

class University:
    # (连接超时, 读取超时)，避免目标不可达时无限挂起
    DEFAULT_TIMEOUT = (10, 30)
    # 命中这些标记说明会话已失效、被踢回了登录页
    LOGIN_PAGE_MARKERS = (
        'id="txtUserName"',
        "id='txtUserName'",
        'name="txtUserName"',
        u'登录超时',
        u'会话已过期',
        u'请重新登录',
    )

    def __init__(self, student, baseurl, session=None,
                 code_path='code.jpg', output_path=None):
        self.student = student
        self.baseurl = baseurl.rstrip('/')
        self.session = session if session is not None else requests.session()
        self.session.headers['User-Agent'] = (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_6) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/59.0.3071.115 Safari/537.36'
        )
        self.code_path = code_path
        self.output_path = output_path or os.path.join(os.getcwd(), 'zhengfang.txt')

    # -- 底层工具 -----------------------------------------------------------

    def _get(self, url, **kwargs):
        kwargs.setdefault('timeout', self.DEFAULT_TIMEOUT)
        try:
            return self.session.get(url, **kwargs)
        except requests.RequestException as e:
            raise NetworkError(u'GET %s 请求失败：%s' % (url, e))

    def _post(self, url, **kwargs):
        kwargs.setdefault('timeout', self.DEFAULT_TIMEOUT)
        try:
            return self.session.post(url, **kwargs)
        except requests.RequestException as e:
            raise NetworkError(u'POST %s 请求失败：%s' % (url, e))

    def _check_session(self, text):
        if text and any(marker in text for marker in self.LOGIN_PAGE_MARKERS):
            raise LoginExpiredError(u'服务端返回登录页，会话已过期，请重新登录')

    # -- 登录 ---------------------------------------------------------------

    def Login(self):
        url = self.baseurl + '/default2.aspx'
        try:
            res = self._get(url)
            selector = etree.HTML(res.content)
            if selector is None:
                raise StructureError(u'登录页 HTML 无法解析')
            viewstate_nodes = selector.xpath('//*[@id="form1"]/input/@value')
            if not viewstate_nodes:
                raise StructureError(u'登录页未找到 __VIEWSTATE 隐藏域')
            __VIEWSTATE = viewstate_nodes[0]

            imgres = self._get(self.baseurl + '/CheckCode.aspx', stream=True)
            with open(self.code_path, 'wb') as f:
                f.write(imgres.content)
            jpg = Image.open(self.code_path)
            jpg.show()
            jpg.close()
            code = input(u'输入验证码：')

            data = {
                "__VIEWSTATE": __VIEWSTATE,
                "txtUserName": self.student.user,
                "TextBox1": self.student.pswd,
                "TextBox2": self.student.pswd,
                "txtSecretCode": code,
                "RadioButtonList1": u"学生",
                "Button1": "",
                "lbLanguage": ""
            }
            loginres = self._post(url, data=data)
            logcont = loginres.text

            pattern = re.compile(
                '<form name="Form1".*?action=(.*?) id="Form1">', re.S)
            action_match = re.findall(pattern, logcont)
            action_ok = False
            try:
                if action_match[0][17:29] == self.student.user:
                    action_ok = True
            except IndexError:
                action_ok = False

            xhxm = re.findall('<span id="xhxm">(.*?)</span>', logcont)
            if not action_ok and not xhxm:
                raise LoginFailedError(
                    u'用户名、密码或验证码错误，或服务端返回了非预期页面')

            name = xhxm[0].replace(u'同学', '') if xhxm else self.student.user
            self.student.urlname = urllib.parse.quote_plus(str(name))
            print('Login succeed!')
            return True
        except (SpiderError, requests.RequestException):
            raise
        except Exception as e:
            raise LoginFailedError(u'%s: %s' % (type(e).__name__, e))

    # -- 段一：本学期课表（只解析、返回完整块文本，不碰文件） ---------------

    def fetch_class(self):
        self.session.headers['Referer'] = self.baseurl + \
            '/xs_main.aspx?xh=' + self.student.user
        kburl = self.baseurl + '/xskbcx.aspx?xh=' + self.student.user + \
            '&xm=' + self.student.urlname + '&gnmkdm=N121603'
        kbresponse = self._get(kburl)
        kbcont = kbresponse.text
        self._check_session(kbcont)

        pattern = re.compile('<td.*?align="Center".*?>(.*?)</td>', re.S)
        contents = re.findall(pattern, kbcont)
        if not contents:
            raise StructureError(
                u'课表页面未找到 align="Center" 的课程表格单元格')

        tool = Tool()
        lines = [u'本学期课表:']
        cnt = 1
        weekdays = [u'周一', u'周二', u'周三', u'周四', u'周五', u'周六', u'周日']
        found_any = False
        for day in weekdays:
            for i in contents:
                if u'星期' in i:
                    continue
                elif u'第' in i:
                    if day in i:
                        con = tool.replace(i)
                        lines.append('%d:\t%s' % (cnt, con))
                        cnt += 1
                        found_any = True
            lines.append('')
        if not found_any:
            raise StructureError(u'课表表格中未解析到任何课程条目')
        return '\n'.join(lines)

    # -- 段二：历年成绩 -----------------------------------------------------

    def fetch_grade(self):
        self.session.headers['Referer'] = self.baseurl + \
            '/xs_main.aspx?xh=' + self.student.user
        gradeurl = self.baseurl + '/xscjcx.aspx?xh=' + self.student.user + \
            '&xm=' + self.student.urlname + '&gnmkdm=N121605'
        graderesponse = self._get(gradeurl)
        self._check_session(graderesponse.text)

        soup = BeautifulSoup(graderesponse.content, 'lxml')
        inputs = soup.find_all(name="input")
        if len(inputs) <= 2 or not inputs[2].has_attr("value"):
            raise StructureError(u'成绩页面缺少 __VIEWSTATE 隐藏域')
        __VIEWSTATE = inputs[2]["value"]

        self.session.headers['Referer'] = gradeurl
        data = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": __VIEWSTATE,
            "hidLanguage": "",
            "ddlXN": "",
            "ddlXQ": "",
            "ddl_kcxz": "",
            "btn_zcj": u'历年成绩'
        }
        grares = self._post(gradeurl, data=data)
        self._check_session(grares.text)

        grades = Getgrade(grares.content)

        lines = [u'历年成绩:']
        lines.append(''.join('%-13s\t' % i for i in grades[0]))
        totup = 0
        totdown = 0
        try:
            for each in grades:
                lines.append(''.join('%-15s\t' % each[one] for one in each))
                totup = totup + float(each[u'绩点']) * float(each[u'学分'])
                totdown = totdown + float(each[u'学分'])
            if totdown == 0:
                raise StructureError(u'学分数为 0，无法计算平均绩点')
        except StructureError:
            raise
        except (KeyError, TypeError, ValueError) as e:
            raise StructureError(u'成绩表格中的学分/绩点无法计算：%s' % e)

        lines.append('')
        lines.append(
            u'平均绩点: ' + '%.2f\t\t\t' % (totup / totdown) +
            u'总学分绩点: ' + '%.2f\t\t\t' % totup +
            u'总学分: ' + '%.2f' % totdown)
        return '\n'.join(lines)

    # -- 段三：等级考试成绩 -------------------------------------------------

    def fetch_test_results(self):
        self.session.headers['Referer'] = self.baseurl + \
            '/xs_main.aspx?xh=' + self.student.user
        gtrurl = self.baseurl + '/xsdjkscx.aspx?xh=' + self.student.user + \
            '&xm=' + self.student.urlname + '&gnmkdm=N121606'
        gtrresponse = self._get(gtrurl)
        self._check_session(gtrresponse.text)

        gtrhtml = etree.HTML(gtrresponse.content)
        if gtrhtml is None:
            raise StructureError(u'等级考试页面 HTML 无法解析')
        trs = gtrhtml.xpath('//table[@class="datelist"]/tr')
        results = Getgradetestresults(trs)

        lines = [u'等级考试成绩:']
        lines.append(''.join('%-10s\t' % one for one in results[0]))
        for each in results:
            lines.append(''.join('%-10s\t' % each[one] for one in each))
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 分段执行调度
# ---------------------------------------------------------------------------

def _describe_error(e):
    return u'%s：%s' % (e.category, e)


def run_segment(univ, method_name, segment_name):
    '''执行单个采集段，任何错误都收敛成分段结局，不向外抛。'''
    try:
        text = getattr(univ, method_name)()
        return SegmentResult(segment_name, STATUS_SUCCESS), text
    except SpiderError as e:
        return SegmentResult(segment_name, STATUS_FAILED,
                             reason=_describe_error(e)), None
    except Exception as e:
        # 兜底：未预期的解析类错误也归为页面结构不符，保证不影响其他段
        return SegmentResult(
            segment_name, STATUS_FAILED,
            reason=u'页面结构不符：未预期的解析错误 %s: %s'
                   % (type(e).__name__, e)), None


def run(univ):
    '''登录 + 三段独立采集 + 统一落盘，返回进程退出码。'''
    try:
        univ.Login()
        login_error = None
    except SpiderError as e:
        login_error = _describe_error(e)
        print(u'Login failed! %s' % login_error)

    # 先读已有文件，失败段直接沿用旧块，保证旧内容不被破坏
    sections = read_sections(univ.output_path)
    results = []

    if login_error is not None:
        for name, _title, _method in SEGMENTS:
            results.append(SegmentResult(
                name, STATUS_SKIPPED,
                reason=u'登录失败，该段未执行（%s）' % login_error))
    else:
        for name, _title, method in SEGMENTS:
            result, block = run_segment(univ, method, name)
            if result.status == STATUS_SUCCESS:
                sections[name] = normalize_block(block)
            results.append(result)
            print(result.format_line())

    atomic_write(univ.output_path, assemble_output(sections, results))

    if login_error is not None:
        return EXIT_LOGIN_FAILED
    if any(r.status == STATUS_FAILED for r in results):
        return EXIT_SEGMENT_FAILED
    return EXIT_SUCCESS


if __name__ == "__main__":
    url = input(u"学校教务网站(如http://115.236.84.162)：")
    user = input(u"学号：")
    pswd = getpass.getpass(u"密码：")
    who = Who(user, pswd)
    univ = University(who, url)
    sys.exit(run(univ))
