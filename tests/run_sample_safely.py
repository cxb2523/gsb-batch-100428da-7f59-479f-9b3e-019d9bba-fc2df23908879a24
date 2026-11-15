# -*- coding: utf-8 -*-
"""用假键鼠模块运行仓库交付的样例脚本，检查实际生成的报告。仅供本地验证，不属测试框架。"""
import os
import sys
import types
import json

仓库根 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, 仓库根)

键鼠动作记录 = []
剪贴板 = {'内容': ''}


class 假pyautogui:
    def press(self, key): 键鼠动作记录.append(('press', key))
    def hotkey(self, *keys): 键鼠动作记录.append(('hotkey', keys))
    def click(self): 键鼠动作记录.append(('click',))
    def rightClick(self): 键鼠动作记录.append(('rightClick',))
    def doubleClick(self, button='left'): 键鼠动作记录.append(('doubleClick', button))
    def moveTo(self, x, y): 键鼠动作记录.append(('moveTo', x, y))
    def mouseDown(self): 键鼠动作记录.append(('mouseDown',))
    def mouseUp(self): 键鼠动作记录.append(('mouseUp',))
    def position(self): return (100, 200)
    def alert(self, msg, title=''): 键鼠动作记录.append(('alert', msg, title))


class 假pyperclip:
    def paste(self): return 剪贴板['内容']
    def copy(self, text): 剪贴板['内容'] = text


假pag = types.ModuleType('pyautogui')
假pag.__dict__.update({k: getattr(假pyautogui(), k) for k in dir(假pyautogui) if not k.startswith('__')})
sys.modules['pyautogui'] = 假pag
假ppc = types.ModuleType('pyperclip')
假ppc.paste = 假pyperclip().paste
假ppc.copy = 假pyperclip().copy
sys.modules['pyperclip'] = 假ppc

import fbidp  # noqa: E402

样例 = sys.argv[1]
汇总 = fbidp.运行脚本_(样例, 启用热键=False)
print('--- 汇总：', 汇总)
控制目录 = os.path.join(os.path.dirname(os.path.abspath(样例)),
                        '运行记录_' + os.path.basename(样例))
with open(os.path.join(控制目录, '执行报告.txt'), encoding='utf-8') as f:
    print('--- TXT 报告 ---')
    print(f.read())
print('键鼠动作记录（显示类命令不应产生任何条目）：', 键鼠动作记录)
