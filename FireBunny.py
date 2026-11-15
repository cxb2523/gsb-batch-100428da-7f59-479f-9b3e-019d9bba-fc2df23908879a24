from fbidp import *
import sys

if len(sys.argv)==2:
	with open(sys.argv[1], encoding='utf-8') as 秘籍:
		咒语 = 秘籍.read()
	try:
		运行器 = 脚本运行器(脚本路径=sys.argv[1], 启用热键=True)
		运行器.运行(咒语)
	except:
		pag.alert('【ToT】', '火小兔')
	秘籍.close()
else:

	sys.exit()
