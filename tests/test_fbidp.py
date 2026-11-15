# -*- coding: utf-8 -*-
"""
火小兔（FBIDP）暂停/继续/停止 与 单条失败不中断 的本地验证。
运行方式（在仓库根目录）：
    "C:/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe" -I -X utf8 tests/test_fbidp.py
测试全程使用假的 pyautogui / pyperclip / subprocess，不触发任何真实键鼠动作，不联网。
"""
import io
import os
import sys
import json
import time
import types
import threading
import tempfile
import runpy
import contextlib

# 隔离模式（-I）下 sys.path 没有仓库根目录，显式加入
仓库根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if 仓库根 not in sys.path:
    sys.path.insert(0, 仓库根)


# --------------------------- 假的外部依赖（必须在 import fbidp 前装好） ---------------------------
键鼠动作记录 = []          # 记录所有“本会触发”的键鼠动作，用于断言暂停期间没有新动作
剪贴板 = {'内容': ''}
子进程记录 = []


class 假pyautogui:
    def press(self, key):
        键鼠动作记录.append(('press', key))

    def hotkey(self, *keys):
        键鼠动作记录.append(('hotkey', keys))

    def click(self):
        键鼠动作记录.append(('click',))

    def rightClick(self):
        键鼠动作记录.append(('rightClick',))

    def doubleClick(self, button='left'):
        键鼠动作记录.append(('doubleClick', button))

    def moveTo(self, x, y):
        键鼠动作记录.append(('moveTo', x, y))

    def mouseDown(self):
        键鼠动作记录.append(('mouseDown',))

    def mouseUp(self):
        键鼠动作记录.append(('mouseUp',))

    def position(self):
        return (100, 200)

    def alert(self, msg, title=''):
        键鼠动作记录.append(('alert', msg, title))


class 假pyperclip:
    def paste(self):
        return 剪贴板['内容']

    def copy(self, text):
        剪贴板['内容'] = text


class 假subprocess:
    def run(self, cmd, *a, **kw):
        子进程记录.append(cmd)
        return types.SimpleNamespace(returncode=0)


假模块 = types.ModuleType('pyautogui')
假模块.__dict__.update({k: getattr(假pyautogui(), k) for k in dir(假pyautogui) if not k.startswith('__')})
sys.modules['pyautogui'] = 假模块
假ppc模块 = types.ModuleType('pyperclip')
假ppc模块.paste = pyperclip_paste = 假pyperclip().paste
假ppc模块.copy = pyperclip_copy = 假pyperclip().copy
sys.modules['pyperclip'] = 假ppc模块
假sp模块 = types.ModuleType('subprocess')
假sp模块.run = 假subprocess().run
sys.modules['subprocess'] = 假sp模块

import fbidp  # noqa: E402

# 测试用“打点”命令：不碰键鼠，只留痕，便于核对执行顺序
打点序列 = []


def 打点_(标签):
    打点序列.append(标签)


fbidp.打点_ = 打点_
# 让睡眠在测试里快速结束（0.2 秒 -> 0.02 秒），仍保留“可被暂停打断”的特性
_原等秒 = fbidp.等_秒
fbidp.等_秒 = lambda 秒: _原等秒(秒 * 0.1)
fbidp.等待_秒 = fbidp.等_秒

通过数 = 0
失败数 = 0


def 断言(条件, 说明):
    global 通过数, 失败数
    if 条件:
        通过数 += 1
        print('  [通过] %s' % 说明)
    else:
        失败数 += 1
        print('  [失败] %s' % 说明)


def 写文件(路径, 内容):
    with open(路径, 'w', encoding='utf-8') as f:
        f.write(内容)


def 原子建信号(目录, 名字):
    临时 = os.path.join(目录, 名字 + '.tmp')
    正式 = os.path.join(目录, 名字)
    with open(临时, 'w', encoding='utf-8') as f:
        f.write('')
    os.replace(临时, 正式)


def 等到(条件函数, 秒=5, 间隔=0.02):
    起点 = time.time()
    while time.time() - 起点 < 秒:
        if 条件函数():
            return True
        time.sleep(间隔)
    return 条件函数()


临时根 = tempfile.mkdtemp(prefix='fbidp_test_')
print('临时工作目录：%s' % 临时根)


# ============================================================================
# 测试 1：单条命令必然失败 -> 记录序号/原文/错误信息，后续命令继续，最后有汇总
# ============================================================================
print('\n测试1：单条失败被记录且后续命令继续执行')
脚本1 = os.path.join(临时根, '脚本1.秘籍')
写文件(脚本1,
       '打点“甲”！打点“乙”！召唤不存在的神兽！打点“丙”！打点“丁”！\n')
汇总1 = fbidp.运行脚本_(脚本1, 启用热键=False)
断言(打点序列 == ['甲', '乙', '丙', '丁'], '失败前后共 4 条打点命令全部按序执行（实际：%r）' % 打点序列)
断言(汇总1['成功条数'] == 4 and 汇总1['失败条数'] == 1,
     '汇总为成功 4 条、失败 1 条（实际：%r）' % 汇总1)
目录1 = os.path.join(临时根, '运行记录_脚本1.秘籍')
with open(os.path.join(目录1, '执行报告.json'), encoding='utf-8') as f:
    报告1 = json.load(f)
失败项 = [r for r in 报告1['明细'] if r['状态'] == '失败']
断言(len(失败项) == 1, 'JSON 报告中恰有 1 条失败记录')
if 失败项:
    项 = 失败项[0]
    断言(项['序号'] == 3, '失败项序号为 3（实际：%r）' % 项.get('序号'))
    断言(项['原文'] == '召唤不存在的神兽', '失败项原文被完整记录（实际：%r）' % 项.get('原文'))
    断言(bool(项['错误信息']) and 'NameError' in 项['错误信息'],
          '失败项记录了错误类型与信息（实际：%r）' % 项.get('错误信息'))
成功序号 = [r['序号'] for r in 报告1['明细'] if r['状态'] == '成功']
断言(成功序号 == [1, 2, 4, 5], '成功命令序号连续且跳过失败项（实际：%r）' % 成功序号)
断言(os.path.exists(os.path.join(目录1, '执行报告.txt')), '同时生成了可读的 TXT 报告')
断言(os.path.exists(os.path.join(目录1, '运行状态.json')), '生成了运行状态文件')


# ============================================================================
# 测试 2：信号文件暂停 -> 暂停期间无任何新键鼠动作；继续后不重跑已完成命令
# ============================================================================
print('\n测试2：暂停期间无新键鼠动作，继续后接着下一条执行且不重跑')
脚本2 = os.path.join(临时根, '脚本2.秘籍')
写文件(脚本2,
       '打点“一”！打点“二”！等2秒！打点“三”！按下“a”！打点“四”！\n')
目录2 = os.path.join(临时根, '运行记录_脚本2.秘籍')
运行器2 = fbidp.脚本运行器(脚本路径=脚本2, 启用热键=False)
线程2 = threading.Thread(target=运行器2.运行, args=(open(脚本2, encoding='utf-8').read(),))
键鼠动作记录.clear()
打点序列.clear()
线程2.start()

# 等前两条打点完成（第3条“等2秒”进行中），然后暂停
断言(等到(lambda: 打点序列 == ['一', '二']), '暂停前前 2 条命令已完成')
原子建信号(目录2, '暂停.信号')
断言(等到(lambda: 运行器2.状态 == '已暂停'), '运行器进入“已暂停”状态')
暂停时打点 = list(打点序列)
暂停时键鼠 = list(键鼠动作记录)
time.sleep(0.6)  # 暂停保持一段时间
断言(打点序列 == 暂停时打点, '暂停期间没有新命令执行（打点未增加）')
断言(键鼠动作记录 == 暂停时键鼠, '暂停期间没有任何新的鼠标/键盘动作')

# 继续
原子建信号(目录2, '继续.信号')
断言(等到(lambda: not 线程2.is_alive()), '放入继续信号后脚本跑完')
线程2.join(timeout=5)
断言(打点序列 == ['一', '二', '三', '四'],
     '继续后从下一条接着执行，已完成的“一、二”没有重跑（实际：%r）' % 打点序列)
断言(('press', 'a') in 键鼠动作记录, '继续之后的键盘命令正常触发')
断言(not os.path.exists(os.path.join(目录2, '暂停.信号')), '继续信号被消费、暂停信号被清除')
with open(os.path.join(目录2, '运行状态.json'), encoding='utf-8') as f:
    状态2 = json.load(f)
断言(状态2['状态'] == '已完成' and 状态2['已执行条数'] == 6, '最终状态为已完成、6 条全部执行')


# ============================================================================
# 测试 3：进程内 API（运行器.暂停/继续）与停止信号
# ============================================================================
print('\n测试3：进程内 API 暂停/继续 与 停止信号')
脚本3 = os.path.join(临时根, '脚本3.秘籍')
# 命令之间插入短等待，给外部控制留出可观察的检查点
写文件(脚本3, '打点“甲”！等1秒！打点“乙”！等1秒！打点“丙”！等1秒！打点“丁”！打点“戊”！\n')
目录3 = os.path.join(临时根, '运行记录_脚本3.秘籍')
运行器3 = fbidp.脚本运行器(脚本路径=脚本3, 启用热键=False)
# 运行前就放好暂停信号：第 1 条命令执行前必然进入暂停，消除时序竞态
原子建信号(目录3, '暂停.信号')
线程3 = threading.Thread(target=运行器3.运行, args=(open(脚本3, encoding='utf-8').read(),))
打点序列.clear()
线程3.start()
断言(等到(lambda: 运行器3.状态 == '已暂停'), '启动后即被信号文件暂停')
断言(打点序列 == [], '暂停时第 1 条命令都尚未执行')
time.sleep(0.4)
断言(打点序列 == [], '暂停保持期间无任何命令执行')
运行器3.继续()  # 进程内 API 继续
断言(等到(lambda: 打点序列 == ['甲']), '继续后从第 1 条开始顺序执行（实际：%r）' % 打点序列)
# 确定性地暂停：放信号文件后，等待命令“等1秒”内部会在 0.1 秒内进入关卡
原子建信号(目录3, '暂停.信号')
断言(等到(lambda: 运行器3.状态 == '已暂停' and 打点序列 == ['甲']),
     '运行器在“乙”之前暂停（实际序列：%r）' % 打点序列)
time.sleep(0.3)
断言(打点序列 == ['甲'], '暂停期间命令不再推进（实际：%r）' % 打点序列)
运行器3.继续()  # 进程内 API 继续
断言(等到(lambda: 打点序列 == ['甲', '乙']), '继续后接着下一条执行（实际：%r）' % 打点序列)
# 停止：放停止信号，在后续关卡处生效
原子建信号(目录3, '停止.信号')
断言(等到(lambda: not 线程3.is_alive()), '停止信号使脚本提前结束')
线程3.join(timeout=5)
断言(打点序列 == ['甲', '乙'], '停止后剩余命令不再执行（实际：%r）' % 打点序列)
with open(os.path.join(目录3, '执行报告.json'), encoding='utf-8') as f:
    报告3 = json.load(f)
断言(报告3['最终状态'] == '已停止', '报告最终状态为“已停止”')


# ============================================================================
# 测试 4：机关区 REPL 行为保持不变（正常命令执行，坏命令不崩、提示【ToT】）
# ============================================================================
print('\n测试4：机关区交互（原有交互方式）保持可用')
输出缓冲 = io.StringIO()
fbidp.是 = True
输入序列 = iter(['打点“互动一”', '这条一定出错的坏命令', '打点“互动二”'])


def 假输入(_提示=''):
    try:
        return next(输入序列)
    except StopIteration:
        fbidp.是 = False
        raise EOFError


fbidp.input = 假输入
打点序列.clear()
try:
    with contextlib.redirect_stdout(输出缓冲):
        # 与 fbidp.机关区() 同样的循环体，仅替换输入来源以便自动喂数据
        while fbidp.是:
            try:
                行 = fbidp.input('【我】')
            except EOFError:
                break
            try:
                fbidp.执行_(行)
            except Exception:
                fbidp.显示_('【ToT】：“我怎么被关起来了? ”')
finally:
    fbidp.是 = True
文本 = 输出缓冲.getvalue()
断言(打点序列 == ['互动一', '互动二'], '机关区中坏命令前后的正常命令都执行了（实际：%r）' % 打点序列)
断言('【ToT】' in 文本, '坏命令触发原有【ToT】提示且 REPL 未崩溃')


# ============================================================================
# 测试 5：火小兔入口（FireBunny.py）仍按“读脚本文件”的方式工作，且生成报告
# ============================================================================
print('\n测试5：火小兔入口读取脚本文件的方式保持可用（端到端）')
脚本5 = os.path.join(临时根, '脚本5.秘籍')
写文件(脚本5, '打点“入口一”！召唤不存在的神兽！打点“入口二”！\n')
打点序列.clear()
旧argv = sys.argv
sys.argv = ['FireBunny.py', 脚本5]
输出缓冲5 = io.StringIO()
try:
    with contextlib.redirect_stdout(输出缓冲5):
        runpy.run_path(os.path.join(仓库根, 'FireBunny.py'), run_name='__main__')
finally:
    sys.argv = 旧argv
断言(打点序列 == ['入口一', '入口二'], '火小兔入口执行了失败项前后的命令（实际：%r）' % 打点序列)
目录5 = os.path.join(临时根, '运行记录_脚本5.秘籍')
with open(os.path.join(目录5, '执行报告.json'), encoding='utf-8') as f:
    报告5 = json.load(f)
断言(报告5['统计']['成功条数'] == 2 and 报告5['统计']['失败条数'] == 1,
     '火小兔入口报告统计正确（实际：%r）' % 报告5['统计'])


# ============================================================================
# 测试 6：机关区入口（MachineOperatedZone.py）带参数读文件的方式保持可用
# ============================================================================
print('\n测试6：机关区入口带参数读取脚本文件的方式保持可用（端到端）')
脚本6 = os.path.join(临时根, '脚本6.机关')
写文件(脚本6, '打点“机关一”！打点“机关二”！\n')
打点序列.clear()
旧argv = sys.argv
sys.argv = ['MachineOperatedZone.py', 脚本6]
输出缓冲6 = io.StringIO()
try:
    with contextlib.redirect_stdout(输出缓冲6):
        runpy.run_path(os.path.join(仓库根, 'MachineOperatedZone.py'), run_name='__main__')
finally:
    sys.argv = 旧argv
断言(打点序列 == ['机关一', '机关二'], '机关区入口按原方式读完并执行脚本（实际：%r）' % 打点序列)
目录6 = os.path.join(临时根, '运行记录_脚本6.机关')
断言(os.path.exists(os.path.join(目录6, '执行报告.txt')), '机关区入口同样生成执行报告')


# ============================================================================
# 测试 7：解析与旧行为等价 + 嵌套循环接受同一个运行器的暂停控制与统一编号
# ============================================================================
print('\n测试7：解析兼容性与嵌套命令的统一记录')
表 = fbidp._解析为任务表('显示“你好，世界”！鼠标移到（10，20）！（纯注释）！等3秒！')
调用们 = [t for _, t in 表]
断言(调用们[0] == '显示_("你好，世界")', '引号内逗号受保护、解析与历史规则一致（实际：%r）' % 调用们[0])
断言(调用们[1] == '鼠标移到_(10,20)', '坐标括号正确转换（实际：%r）' % 调用们[1])
断言(all('注释' not in t for t in 调用们), '注释句不产生任务')
断言(调用们[-1] == '等_秒(3)', '等待命令解析正确（实际：%r）' % 调用们[-1])

脚本7 = os.path.join(临时根, '脚本7.秘籍')
写文件(脚本7, '把“打点‘循环’”重复2次！打点“收尾”！\n')
打点序列.clear()
汇总7 = fbidp.运行脚本_(脚本7, 启用热键=False)
断言(打点序列 == ['循环', '循环', '收尾'], '嵌套重复命令按序执行（实际：%r）' % 打点序列)
断言(汇总7['已执行条数'] == 4 and 汇总7['失败条数'] == 0,
     '外层重复命令与嵌套内的每条子命令都被统一编号记录（实际：%r）' % 汇总7)

# ============================================================================
# 测试 8：等待命令进行中暂停 -> 即时停住；暂停时长不挤占等待时长
# ============================================================================
print('\n测试8：等待命令进行中暂停即时生效，且继续后等待照常完成')
脚本8 = os.path.join(临时根, '脚本8.秘籍')
写文件(脚本8, '打点“前”！等10秒！打点“后”！\n')
目录8 = os.path.join(临时根, '运行记录_脚本8.秘籍')
运行器8 = fbidp.脚本运行器(脚本路径=脚本8, 启用热键=False)
线程8 = threading.Thread(target=运行器8.运行, args=(open(脚本8, encoding='utf-8').read(),))
打点序列.clear()
开始时刻 = time.time()
线程8.start()
断言(等到(lambda: 打点序列 == ['前']), '等待命令之前的命令先完成')
time.sleep(0.15)  # 等10秒（测试中加速为 1 秒）刚进入不久
原子建信号(目录8, '暂停.信号')
断言(等到(lambda: 运行器8.状态 == '已暂停'), '等待进行中也能即时暂停')
暂停起点 = time.time()
time.sleep(0.8)  # 暂停保持 0.8 秒，远长于加速后 1 秒的总等待
断言(打点序列 == ['前'], '暂停中等待命令不结束、后续命令不执行')
原子建信号(目录8, '继续.信号')
断言(等到(lambda: 打点序列 == ['前', '后'], 秒=8), '继续后等待命令走完，后续命令执行')
线程8.join(timeout=10)
暂停时长 = time.time() - 暂停起点
断言(暂停时长 >= 0.8, '暂停确实保持了约 %.2f 秒' % 暂停时长)

print('\n================ 测试结果 ================')
print('通过 %d 项，失败 %d 项。' % (通过数, 失败数))
sys.exit(1 if 失败数 else 0)
