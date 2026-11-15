# 基本配置
import numpy as np
import subprocess as sp
import pyperclip as ppc
from time import sleep as _原生睡眠
from time import time as 当前秒
import pyautogui as pag
from jieba import cut
import os, requests, json
import re
import logging
import threading
import traceback
import datetime
import ctypes
import ctypes.wintypes
from contextlib import contextmanager
logging.getLogger('jieba').setLevel(logging.CRITICAL)

显示_ = print
是 = True
否 = False

# ============================================================================
# 脚本执行的暂停 / 继续 / 停止控制
# ----------------------------------------------------------------------------
# 控制方式有两种，互不冲突，可混用：
# 1. 信号文件（跨进程、可被任意程序或资源管理器操作，测试也依赖它）：
#       在“运行记录_<脚本名>”控制目录中新建
#       暂停.信号 -> 暂停；继续.信号 -> 继续；停止.信号 -> 停止
# 2. 全局热键（仅 Windows，入口脚本默认开启）：F7 暂停 / F8 继续 / F9 停止
# 暂停期间所有命令都阻塞在“关卡”处，不会触发任何鼠标、键盘动作；
# 继续后从下一条未执行命令接着跑，已完成的命令不重复执行。
# ============================================================================

_本地 = threading.local()
_分隔符集合 = ('！', '!', '；', ';', '。', '\n', '\r', '，')


class _用户停止(Exception):
	"""收到停止信号时在内部控制流里传播，不当作失败命令记录。"""


def _当前运行器():
	return getattr(_本地, '运行器', None)


@contextmanager
def _运行上下文(运行器):
	旧的 = getattr(_本地, '运行器', None)
	_本地.运行器 = 运行器
	try:
		yield
	finally:
		_本地.运行器 = 旧的


def _安全文件名(名字):
	名字 = os.path.basename(名字 or '交互')
	for 字符 in '\\/:*?"<>|':
		名字 = 名字.replace(字符, '_')
	return 名字 or '交互'


class 脚本运行器:
	"""带暂停/继续/停止与逐条结果记录的脚本执行器。"""

	def __init__(self, 脚本路径=None, 控制目录=None, 启用热键=False, 输出=None):
		self.脚本路径 = 脚本路径
		self.锁 = threading.RLock()
		self.暂停标志 = threading.Event()   # set 表示处于暂停
		self.停止标志 = threading.Event()
		self.结果们 = []
		self.序号 = 0
		self.总数 = 0
		self.状态 = '就绪'
		self.当前原文 = ''
		self.执行中 = False
		self.输出 = 输出 or 显示_
		if 控制目录 is None:
			父目录 = os.path.dirname(os.path.abspath(脚本路径)) if 脚本路径 else os.getcwd()
			控制目录 = os.path.join(父目录, '运行记录_' + _安全文件名(脚本路径))
		self.控制目录 = os.path.abspath(控制目录)
		os.makedirs(self.控制目录, exist_ok=True)
		self.状态文件 = os.path.join(self.控制目录, '运行状态.json')
		self.报告文件_json = os.path.join(self.控制目录, '执行报告.json')
		self.报告文件_txt = os.path.join(self.控制目录, '执行报告.txt')
		# 清掉上一次遗留的信号，避免一启动就暂停
		for 名字 in ('暂停.信号', '继续.信号', '停止.信号'):
			try:
				os.remove(os.path.join(self.控制目录, 名字))
			except OSError:
				pass
		self.热键线程 = None
		if 启用热键:
			self._启动热键()
		self._写状态()

	# ---------------- 对外控制 API（热键也调这三个） ----------------
	def 暂停(self):
		self.暂停标志.set()
		self._写状态()

	def 继续(self):
		self.暂停标志.clear()
		暂停信号 = os.path.join(self.控制目录, '暂停.信号')
		try:
			os.remove(暂停信号)
		except OSError:
			pass
		self._写状态()

	def 停止(self):
		self.停止标志.set()

	# ---------------- 信号文件 ----------------
	def _信号路径(self, 名字):
		return os.path.join(self.控制目录, 名字)

	def _读取信号(self):
		if os.path.exists(self._信号路径('停止.信号')):
			self.停止标志.set()
		if os.path.exists(self._信号路径('暂停.信号')):
			self.暂停标志.set()
		if os.path.exists(self._信号路径('继续.信号')):
			self.暂停标志.clear()
			for 名字 in ('继续.信号', '暂停.信号'):
				try:
					os.remove(self._信号路径(名字))
				except OSError:
					pass

	def _关卡(self):
		"""每条命令执行前必经此处。暂停时就地等待，期间不执行任何命令。"""
		while True:
			self._读取信号()
			if self.停止标志.is_set():
				raise _用户停止()
			if not self.暂停标志.is_set():
				return
			if self.状态 != '已暂停':
				self.状态 = '已暂停'
				self._写状态()
				self.输出('【已暂停】放入“继续.信号”或按 F8 继续，放入“停止.信号”或按 F9 停止。')
			_原生睡眠(0.1)

	# ---------------- 热键（F7/F8/F9） ----------------
	def _启动热键(self):
		if not hasattr(ctypes, 'windll'):
			return
		映射 = [(1, 0x76), (2, 0x77), (3, 0x78)]  # F7 F8 F9 的虚拟键码

		def 消息循环():
			user32 = None
			try:
				user32 = ctypes.windll.user32
				for 编号, 虚拟键 in 映射:
					if user32.RegisterHotKey(None, 编号, 0, 虚拟键) != 1:
						return
				消息 = ctypes.wintypes.MSG()
				动作 = {1: self.暂停, 2: self.继续, 3: self.停止}
				while user32.GetMessageW(ctypes.byref(消息), None, 0, 0) != 0:
					if 消息.message == 0x0312:  # WM_HOTKEY
						动作.get(int(消息.wParam), lambda: None)()
			except Exception:
				pass
			finally:
				if user32 is not None:
					for 编号, _ in 映射:
						try:
							user32.UnregisterHotKey(None, 编号)
						except Exception:
							pass

		self.热键线程 = threading.Thread(target=消息循环, daemon=True)
		self.热键线程.start()

	# ---------------- 状态与报告 ----------------
	def _写状态(self):
		if self.执行中:
			命令位置 = '当前命令：%s（等待类命令暂停即时生效，其余键鼠命令在其结束后生效）' % self.当前原文
		else:
			命令位置 = '下一条待执行：%s' % (self.当前原文 or '(无)')
		数据 = {
			'状态': self.状态,
			'暂停中': self.暂停标志.is_set(),
			'已执行条数': self.序号,
			'命令总条数': self.总数,
			'命令位置': 命令位置,
			'控制目录': self.控制目录,
			'更新时间': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
		}
		try:
			临时 = self.状态文件 + '.tmp'
			with open(临时, 'w', encoding='utf-8') as f:
				json.dump(数据, f, ensure_ascii=False, indent=2)
			os.replace(临时, self.状态文件)
		except OSError:
			pass

	def 汇总(self):
		成功数 = sum(1 for r in self.结果们 if r['状态'] == '成功')
		return {
			'最终状态': self.状态,
			'已执行条数': len(self.结果们),
			'成功条数': 成功数,
			'失败条数': len(self.结果们) - 成功数,
		}

	def _写报告(self):
		汇总 = self.汇总()
		报告 = {
			'脚本路径': self.脚本路径,
			'控制目录': self.控制目录,
			'开始时间': getattr(self, '开始时间文本', ''),
			'结束时间': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
			'最终状态': self.状态,
			'统计': 汇总,
			'明细': self.结果们,
		}
		try:
			临时 = self.报告文件_json + '.tmp'
			with open(临时, 'w', encoding='utf-8') as f:
				json.dump(报告, f, ensure_ascii=False, indent=2)
			os.replace(临时, self.报告文件_json)
			行 = [
				'火小兔脚本执行报告',
				'脚本路径：%s' % (self.脚本路径 or '(交互输入)'),
				'控制目录：%s' % self.控制目录,
				'开始时间：%s' % getattr(self, '开始时间文本', ''),
				'结束时间：%s' % 报告['结束时间'],
				'最终状态：%s' % self.状态,
				'统计：共执行 %(已执行条数)d 条，成功 %(成功条数)d 条，失败 %(失败条数)d 条' % 汇总,
				'',
				'明细：',
			]
			for r in self.结果们:
				行.append('第%d条 [%s] %s' % (r['序号'], r['状态'], r['原文']))
				if r['状态'] == '失败':
					行.append('        错误信息：%s' % r['错误信息'])
			with open(self.报告文件_txt, 'w', encoding='utf-8') as f:
				f.write('\n'.join(行) + '\n')
		except OSError as e:
			self.输出('【警告】报告写入失败：%s' % e)

	# ---------------- 单条调度 ----------------
	def _调度(self, 原文, 任务):
		# 关卡：暂停时停在这里，绝不触发任何键鼠动作
		self._关卡()
		with self.锁:
			self.序号 += 1
			序号 = self.序号
			self.当前原文 = 原文
		self.状态 = '运行中'
		self._写状态()
		起点 = 当前秒()
		self.执行中 = True
		try:
			_直接执行(任务)
		except _用户停止:
			raise
		except Exception as e:
			错误信息 = '%s: %s' % (type(e).__name__, e)
			结果 = {
				'序号': 序号,
				'原文': 原文,
				'展开后的调用': 任务,
				'状态': '失败',
				'错误信息': 错误信息,
				'错误追溯': traceback.format_exc(),
				'耗时秒': round(当前秒() - 起点, 4),
				'时间': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
			}
			with self.锁:
				self.结果们.append(结果)
			self.输出('第%d条 [失败] %s' % (序号, 原文))
			self.输出('        错误信息：%s' % 错误信息)
		else:
			结果 = {
				'序号': 序号,
				'原文': 原文,
				'展开后的调用': 任务,
				'状态': '成功',
				'错误信息': None,
				'耗时秒': round(当前秒() - 起点, 4),
				'时间': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
			}
			with self.锁:
				self.结果们.append(结果)
			self.输出('第%d条 [成功] %s' % (序号, 原文))
		finally:
			self.执行中 = False
			self._写状态()

	# ---------------- 整段脚本运行 ----------------
	def 运行(self, 脚本文本):
		任务表 = _解析为任务表(脚本文本)
		self.总数 = len(任务表)
		self.开始时间文本 = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		self.状态 = '运行中'
		self.输出('脚本共 %d 条可执行命令，控制目录：%s' % (self.总数, self.控制目录))
		self.输出('暂停：在控制目录新建“暂停.信号”（或按 F7）；继续：新建“继续.信号”（或按 F8）；停止：新建“停止.信号”（或按 F9）。')
		self._写状态()
		with _运行上下文(self):
			try:
				for 原文, 任务 in 任务表:
					self._调度(原文, 任务)
			except _用户停止:
				self.状态 = '已停止'
				self.输出('【已停止】第 %d 条及之后的命令未执行。' % (self.序号 + 1))
			except Exception as e:  # 解析阶段等极少数意外，保证入口不崩
				self.状态 = '异常终止'
				self.输出('【异常终止】%s: %s' % (type(e).__name__, e))
			else:
				self.状态 = '已完成'
			finally:
				汇总 = self.汇总()
				self._写报告()
				self._写状态()
				self.输出('执行结束：%(最终状态)s，共执行 %(已执行条数)d 条，成功 %(成功条数)d 条，失败 %(失败条数)d 条。' % 汇总)
				self.输出('明细报告：%s' % self.报告文件_txt)
		return self.汇总()


def 运行脚本_(脚本路径, 启用热键=True):
	"""读取脚本文件并用受控运行器执行（火小兔/机关区入口共用）。"""
	with open(脚本路径, encoding='utf-8') as 秘籍:
		咒语 = 秘籍.read()
	运行器 = 脚本运行器(脚本路径=脚本路径, 启用热键=启用热键)
	return 运行器.运行(咒语)


# ============================================================================
# 命令解析（保持历史解析规则不变，仅拆成可复用的小函数）
# ============================================================================

def _拆分原始命令(文本):
	"""按命令分隔符切成原始句段；引号“”与括号（）内的分隔符受保护。"""
	段们 = []
	缓冲 = []
	引号中 = False
	括号深度 = 0
	for 字 in 文本:
		if 字 == '“':
			引号中 = True
		elif 字 == '”':
			引号中 = False
		if not 引号中:
			if 字 == '（':
				括号深度 += 1
			elif 字 == '）':
				括号深度 = max(0, 括号深度 - 1)
		if 字 in _分隔符集合 and not 引号中 and 括号深度 == 0:
			段 = ''.join(缓冲).strip()
			if 段:
				段们.append(段)
			缓冲 = []
		else:
			缓冲.append(字)
	段 = ''.join(缓冲).strip()
	if 段:
		段们.append(段)
	return 段们


def _提取引号(命令):
	引号内容 = []
	while "“" in 命令 and "”" in 命令:
		# 不含引号本身，并把单引号变成双引号
		引号内容.append(命令[命令.find("“") + 1: 命令.find("”")].replace('‘', '“').replace('’', '”'))
		命令 = 命令[:命令.find("“")] + '【引号】' + 命令[命令.find("”") + 1:]  # 不剩下引号
	return 命令, 引号内容


def _处理括号(命令):
	while '（' in 命令 and '）' in 命令:
		try:
			# 括号内的内容是数字：视为坐标
			if np.array(命令[命令.find('（') + 1: 命令.find('）')].split('，')).astype(float).dtype == np.dtype('float64'):
				命令 = (命令[:命令.find('（')] + '('
						+ 命令[命令.find('（') + 1: 命令.find('）')].replace('，', ',')
						+ ')' + 命令[命令.find('）') + 1:])
		except:
			命令 = 命令.replace(命令[命令.find('（'): 命令.find('）') + 1], '')  # 作为注释被去除
	return 命令


def _生成任务串们(段):
	"""把一个原始句段解析成 0~n 个可 exec 的调用串（历史规则逐字保留）。"""
	命令, 引号内容 = _提取引号(段)
	命令 = _处理括号(命令)
	命令 = (命令.replace('！', "\n").replace("!", "\n").replace("；", '\n')
			.replace(";", '\n').replace("。", '\n').replace('：', '冒号'))
	命令 = 命令.replace('，', "\n")
	任务们 = []
	for 结构 in 命令.split('\n'):
		# 结构化，引号优先级最高，在最开始已经处理
		坐标内容 = []
		数值内容 = []
		while '(' in 结构 and ')' in 结构:
			坐标内容.append(结构[结构.find('(') + 1: 结构.find(')')])  # 填进来的时候不包括括号
			结构 = 结构.replace(结构[结构.find('('): 结构.find(')') + 1], '【坐标】')  # 这种括号目前只有坐标会有
		数据分析 = list(cut(结构))
		for i in range(len(数据分析)):
			if 数据分析[i] and 48 <= ord(数据分析[i][0]) <= 57:
				数值内容.append(数据分析[i])
				数据分析[i] = '【数值】'  # 直接的数值
		结构 = ''.join(数据分析)
		# 格式化数据
		本行数据 = []
		while '【' in 结构:
			标记 = 结构[结构.find('【'):结构.find('】') + 1]
			if 标记 == '【引号】':
				本行数据.append('"' + 引号内容.pop(0) + '"')
				结构 = 结构[:结构.find('【')] + '_' + 结构[结构.find('】') + 1:]
			elif 标记 == '【坐标】':
				本行数据.append(坐标内容.pop(0))
				结构 = 结构[:结构.find('【')] + '_' + 结构[结构.find('】') + 1:]
			elif 标记 == '【数值】':
				本行数据.append(数值内容.pop(0))
				结构 = 结构[:结构.find('【')] + '_' + 结构[结构.find('】') + 1:]
		if 结构 != '':
			任务们.append(结构 + '(' + ', '.join(本行数据) + ')')
	return 任务们


def _解析为任务表(文本):
	表 = []
	for 段 in _拆分原始命令(文本):
		for 任务 in _生成任务串们(段):
			表.append((段, 任务))
	return 表


def _直接执行(任务):
	exec(任务, globals())


# 执行
def 执行_(命令):
	"""历史入口：无运行器时保持“出错即抛”的旧语义；
	有运行器时（脚本运行、嵌套的重复/循环）自动接受暂停/继续与逐条记录。"""
	运行器 = _当前运行器()
	for 原文, 任务 in _解析为任务表(命令):
		if 运行器 is None:
			_直接执行(任务)
		else:
			运行器._调度(原文, 任务)


def 等_秒(秒):
	"""等待若干秒；处于脚本运行中时可被暂停/停止打断，暂停期间不继续计时动作。"""
	运行器 = _当前运行器()
	剩余 = float(秒)
	while 剩余 > 0:
		片 = min(0.1, 剩余)
		_原生睡眠(片)
		剩余 -= 片
		if 运行器 is not None:
			运行器._关卡()


等待_秒 = 等_秒


# 机关区
def 机关区():
	while 是:
		输入 = input('【我】')
		try:
			执行_(输入)
		except:
			显示_('【ToT】：“我怎么被关起来了? ”')

# 把_重复_次
def 把_重复_次(命令, 次数):
	for i in range(次数):
		执行_(命令)

# 指定的秒内一直重复
def 指定_秒内一直重复_(秒数, 命令):
	起点 = 当前秒()
	终点 = 起点
	while 终点-起点<秒数:
		执行_(命令)
		终点 = 当前秒()

# 系统操作
def 休眠():
	sp.run(['PowerShell', '-Command', 'shutdown -h'])
def 关机():
	sp.run(['PowerShell', '-Command', 'shutdown -s -f -t 00'])
def 重启():
	sp.run(['PowerShell', '-Command', 'shutdown -r -f -t 00'])

def 打开程序_(地址):
	sp.run(['PowerShell', '-Command', f'start {地址}'])

def 打开_(程序):
	pag.press('win')
	打出_(程序)
	回车()

def 卸载():
	sp.run(['PowerShell', '-Command', 'start ms-settings:appsfeatures'])

## 启动一个程序，也可以用于快速切换到该活动界面
def 记录步骤():
	sp.run(['cmd', '/c', 'psr'])
教别人如何操作 = 记录步骤
教他人如何操作 = 教别人如何操作

def 记事本():
	打开程序_('notepad')

def QQ邮箱():
	sp.run(['PowerShell', '-Command', 'start "https://mail.qq.com/"'])

def 文件传输助手():
     打开程序_('https://szfilehelper.weixin.qq.com')

## 接入AI
### 360智脑
def 对话冒号_(内容):
  url = "https://api.360.cn/v1/chat/completions"
  payload = json.dumps({
    "model": "360gpt-pro",
    "messages": [
      {
        "role": "user",
        "content": 内容
      }
    ],
    "stream": False,
    "temperature": 0.9,
    "max_tokens": 2048,
    "top_p": 0.5,
    "top_k": 0,
    "repetition_penalty": 1.05,
    "num_beams": 1,
    "user": "andy"
  })
  headers = {
    'Authorization': 'fk1357707141.BSCoDrkjDWq4N2aiATwpNRVnUL7WKimz726d4e68', # Replace it with your api key like this
    'Content-Type': 'application/json'
  }

  response = requests.request("POST", url, headers=headers, data=payload)
  return eval(response.text.replace('null', 'None'))['choices'][0]['message']['content']

# 文件操作

def 复制():
	pag.hotkey('ctrl', 'c')

def 粘贴():
	pag.hotkey('ctrl', 'v')

def 新建():
	pag.hotkey('ctrl', 'n')

def 保存():
	pag.hotkey('ctrl', 's')

def 全选():
	pag.hotkey('ctrl', 'a')

def 打出_(文字=''):
    text = ppc.paste()
    ppc.copy(文字)
    粘贴()
    ppc.copy(text)

def 按下_(键盘):
	pag.press(键盘)

def 外化_(文本):
    ppc.copy(文本)
输出_ = 外化_

def 内化信息():
   return ppc.paste()
吸收信息 = 内化信息

##Excel转置内加工
def 竖变横内加工():
	复制()
	数据 = ppc.paste().replace('\r\n','\t')
	ppc.copy(数据)
def 横变竖内加工():
	复制()
	数据 = ppc.paste().replace('\t','\n')
	ppc.copy(数据)

# 界面操作

def 切换界面():
   pag.hotkey('alt', 'tab')

def 切换桌面():
   pag.hotkey('win', 'tab')

def 查找_(目标):
	pag.hotkey('ctrl', 'f')
	打出_(目标)

## 点击指定位置
def 获取鼠标位置():
	ppc.copy("（{}，{}）".format(*pag.position()))
捕获鼠标位置 = 获取鼠标位置

def 鼠标移到_(长, 宽):
	pag.moveTo(长, 宽)
def 点击_(长, 宽):
	pag.moveTo(长, 宽)
	pag.click()
单击_ = 点击_

def 右击_(长, 宽):
	pag.moveTo(长, 宽)
	pag.rightClick()

## 鼠标从位置1拉到位置2
def 选中_到_(位置1长,位置1宽, 位置2长,位置2宽):
	pag.moveTo(位置1长,位置1宽)
	pag.mouseDown()
	pag.moveTo(位置2长,位置2宽)
	pag.mouseUp()

## 双击
def 左键双击_(长, 宽):
    pag.moveTo(长, 宽)
    pag.doubleClick()

def 右键双击_(长, 宽):
    pag.moveTo(长, 宽)
    pag.doubleClick(button='right')

## 关闭
def 关闭页面():
	pag.hotkey('ctrl','w')

def 关闭程序():
	pag.hotkey('alt','f4')

## 缩放
def 放大():
	pag.hotkey('ctrl', '+')

def 缩小():
	pag.hotkey('ctrl', '-')

def 全屏():
	pag.press('f11')

def 回车():
	pag.press('enter')

def 最大化():

	pag.hotkey('win', 'up')
