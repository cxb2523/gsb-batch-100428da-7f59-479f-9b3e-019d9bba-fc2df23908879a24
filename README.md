# 雨课堂视频自动播放器

用一个**真实的浏览器**（Edge / Chrome）把雨课堂课程视频自动播完：自动找没看完的小节、
自动翻页、自动切倍速、播完自动下一个，支持从 0 秒重播、优先清"看了一半"的残留。

> ⚠️ **本项目仅供学习浏览器自动化（Selenium）与个人效率使用。**
> 它不能替你完成作业，也可能违反你所在学校的规定和雨课堂用户协议。
> 使用前请先读文末的[免责声明](#免责声明)。

**不想装 Python？** [Releases](https://github.com/34Avin34/yuketang_autoplayer/releases)
里有打包好的 `YuketangAutoPlayer.exe`，双击即用（详见下面的方式 A）。

---

## 一、30 秒上手（先看这里）

### 方式 A：直接用打包好的 EXE（最省事，不用装 Python）

**1. 下载 exe**
打开 [Releases](https://github.com/34Avin34/yuketang_autoplayer/releases)，找到最新的
`v1.0.0`，在「Assets」里点 `YuketangAutoPlayer.exe` 下载。
（别点 `Source code (zip)`，那是源码；想用源码就走方式 B。）

**2. 放进一个专用文件夹**
比如新建 `D:\yuketang` 再放进去。

- 别放 `C:\Program Files`：那里需要管理员权限，`config.ini` 会写不进去；
- 别留在「下载」文件夹：容易被清理工具顺手删掉。

**3. 先双击运行一次**
会闪一下黑窗口然后自动关闭，**这是正常的** —— 它在自己所在的文件夹里生成了
`config.ini`（所有设置都在这一个文件里）。

**4. 填课程地址**
右键 `config.ini` →「打开方式」→ 记事本。找到这一行：

```ini
; 结构说明（这一行是注释，不用改）
;         └── 课程/班级 id   └── 视频 id      └── 小节 id
course_url = https://www.yuketang.cn/ai-workspace/lms-graph/xxxx/video/xxxxx?node_id=xxxx
```

把等号**右边整段**替换成你从浏览器复制的课程地址：

- 保留 `course_url = ` 前缀和等号，只换右边；
- **不要加引号**，地址直接写；
- 保存后确认文件名还是 `config.ini`（不是 `config.ini.txt`，记事本容易加后缀）。

**5. 再双击一次**
会弹出 Edge 窗口 → 加载课程页 → **用微信或雨课堂 App 扫码登录** → 登录成功后自动开始。

- 旁边那个黑窗口就是日志（能看到"播放中 xx%、实际 2.0x"），**可以最小化，别关**；
- 登录状态会自动写回 `config.ini`，下次不用再扫；
- **别用鼠标去点那个浏览器窗口**，程序正在控制它。

> 这是未签名的 PyInstaller 打包程序，Windows 可能提示：
> - 蓝色「**Windows 已保护你的电脑**」（SmartScreen）→ 点「更多信息 → 仍要运行」；
> - 「**智能应用控制**已阻止可能不安全的应用」（Smart App Control，部分 Win11 默认开启）
>   → **没有放行按钮**，只能改用下面的**方式 B（源码）**，或手动关闭该功能。
>
> 任何一种提示都和"有没有病毒"无关 —— 只是没买代码签名证书。不放心就一律用方式 B。

### 方式 B：用源码运行（想改代码 / 不信任 exe）

**1. 装 Python**（3.9 或更高）
到 [python.org](https://www.python.org/downloads/) 下载安装，**安装时勾选
`Add python.exe to PATH`**。

**2. 装依赖**（在项目文件夹里打开命令行）

```bash
pip install -r requirements.txt
```

**3. 填配置**

把 `config.ini.example` 复制一份、改名成 `config.ini`，用记事本打开，
只填这一行：

```ini
course_url = https://www.yuketang.cn/ai-workspace/lms-graph/xxxx/video/xxxxx?node_id=xxxx
```

（`course_url` 怎么拿见下一节；其他项都有默认值，先不用管。）

**4. 运行**

双击 `启动.bat`，或者命令行 `python main.py`。

### 两种方式共同的后续

弹出的浏览器窗口里**扫一次码登录**，登录成功后它会自动把登录状态存进 `config.ini`，
然后开始一节一节往下播。窗口可以最小化，**但别用鼠标去点那个浏览器**。

> 想先确认"配对了没有"：
> 源码方式跑 `python main.py --list`；
> EXE 方式跑 `YuketangAutoPlayer.exe --list`。
> 都只列清单、不播视频，很安全。

---

## 二、两个信息怎么拿

### ① course_url（必填）

1. 浏览器登录雨课堂，进你要刷的那门课；
2. 点「学习内容」/「学习空间」；
3. **复制地址栏整条 URL**（必须 `https://` 开头）。

各种版本都行：

```
旧版：       https://stu.yuketang.cn/pro/lms/xxxxxxxx/12345678/studycontent
学习空间版： https://www.yuketang.cn/ai-workspace/lms-graph/xxxxxxxx/video/xxxxxxxx?node_id=xxxxxxx
```

新版「学习空间」里直接复制某个视频页的地址也可以 —— 那个页面本身带着完整课程目录。

### ② cookie（可以不填）

**推荐直接留空**：运行时弹二维码，扫一下，脚本会把 `sessionid` 自动写回配置。

想手动填的话：

1. 课程页面按 **F12** → 上方选 **Application**（中文版「应用」）；
2. 左侧 **Cookies** → 点 `https://你的学校.yuketang.cn`；
3. 找到 **sessionid**，复制 Value 贴到 `cookie =` 后面（整串 cookie 也认）。

> `sessionid` 等于你的登录态，**别外传、别提交**。仓库里的 `.gitignore` 已经把
> `config.ini` 排除掉了。

---

## 三、详细配置（`config.ini` 全部选项）

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `headless` | `false` | `true` = 不弹浏览器窗口后台跑；**扫码登录必须 `false`** |
| `browser` | `auto` | `auto` / `chrome` / `edge`。`auto` 优先 Edge（Windows 自带），没有再试 Chrome |
| `course_url` | — | 课程地址，必填，`https://` 开头 |
| `cookie` | 空 | `sessionid` 的值；留空 = 每次运行扫码登录 |
| `playback_rate` | `2` | 播放倍速。`2` 是最稳的档（平台认可）；`4/8/16` 也能跑，走"锁定倍速"，但平台可能不记完成 |
| `restart_from_start` | `true` | `true` = 没看完的视频从 0:00 重播；`false` = 从上次停的位置续播 |
| `implicitly_wait` | `20` | 等页面元素的最长秒数，网络慢可以调大 |
| `max_minutes_per_video` | `180` | 单个视频最多等多少分钟，超时就跳过 |

> 用 EXE 版本时，`config.ini` 就在 exe 旁边（第一次运行自动生成），改完同样要重新运行。

配置改完**必须重启程序**才生效。

### 播放策略（默认行为）

- **先清残留**：右侧是半圆（看了一半）的小节排在最前面，从头播完；再按目录顺序补没开始的。
- **已完成的不播**：对勾图标 / 播放器标题旁写「已完成」的，直接跳过。
- **只播视频**：作业、图文自动忽略。

---

## 四、常用命令

> 用 EXE 版本时，把下表的 `python main.py` 换成 `YuketangAutoPlayer.exe`，参数完全一样。

| 命令 | 作用 |
|---|---|
| `python main.py` | 开始播放 |
| `python main.py --list` | 只列出没看完的视频，不播 |
| `python main.py --login` | 强制重新登录，并把新 sessionid 存回配置 |
| `python main.py --dump` | 保存课程页结构到 `debug/`（提 issue 时附上） |
| `python main.py --self-test` | 只测试浏览器能不能启动 |
| `python main.py --headless` | 后台跑（不能扫码登录） |
| `python main.py --rate 1.5` | 临时指定倍速 |
| `python main.py --url ... --cookie ...` | 临时用命令行里的地址/cookie |

---

## 五、它是怎么工作的（给好奇的人）

| 机制 | 说明 |
|---|---|
| 三种界面自适应 | 运行时自动识别「学习空间」(`leaf-item`)、新版列表(`study-unit`)、旧版列表(`leaf-detail`) |
| 浏览器自适应 | 用 Selenium Manager 自动下载匹配的 driver，不用手动找 chromedriver |
| 倍速 | 先点播放器自己的「2.00X」菜单；点不中就直接锁死 `<video>.playbackRate`，播放器改回去也会被拦下 |
| 从头播 | 把进度拉回 0，并抑制"进度倒退=播完"的误判 |
| 异常自救 | 卡流 → 主动重新加载；源坏了/一直不动 → 重启浏览器重试；列表找不到 → 重新打开课程页 |
| 记账核对 | 每播完一节核对平台「完成数」有没有涨，没涨会提示你（多半是倍速不被认可） |

详细排错见 [使用说明.md](使用说明.md)。

---

## 六、常见问题

| 现象 | 处理 |
|---|---|
| 「没找到课程列表」 | 雨课堂又改版了。跑 `--dump`，把 `debug/page.html`、`debug/diagnose.txt` 提 issue |
| 「当前 cookie 无效」 | 重新运行、扫码登录即可，不用改配置 |
| 一直刷 `实际0.0x` | 卡流。程序会自动重新加载/重启浏览器 |
| `media error 4` | 该节视频地址异常，程序会重启浏览器重试 2 次再跳过 |
| 完成数一直不涨 | 平台不认当前倍速，把 `playback_rate` 改成 `1` 试试 |
| 双击 exe 弹「Windows 已保护你的电脑」 | 这是 SmartScreen（蓝色窗口）。点「更多信息 → 仍要运行」 |
| 双击 exe 弹「**智能应用控制**已阻止可能不安全的应用」 | 这是 Windows 11 的 Smart App Control，未签名程序会被直接拦下且**没有「仍要运行」**。请改用**方式 B（源码）**；或在「设置 → 隐私和安全性 → Windows 安全中心 → 应用和浏览器控制 → 智能应用控制」里关闭它（关掉后想再打开通常要重置系统，谨慎） |

---

## 七、灵感来源与致谢

本项目**不是从零发明**的，思路和早期实现来自下面这些公开资料，特此致谢：

- **[LetMeFly666/YuketangAutoPlayer](https://github.com/LetMeFly666/YuketangAutoPlayer)**
  —— 最初的核心思路（Selenium 驱动真实浏览器、`iframe`/列表定位、暂停就 `play()`、
  识别播完）来自这个项目，本项目在其基础上做了较多重构与适配。
- **CSDN《汕头大学雨课堂自动播放器（YuketangAutoPlayer For STU）》**
  （作者：不想做小丑）—— 把上面那个项目整理成可照做的使用教程，本项目的目标场景由此而来。
- [Selenium](https://www.selenium.dev/) —— 浏览器自动化框架。

相对上游的主要改动：适配 2025 年新版「学习空间」界面、浏览器/驱动自动选择、
倍速锁定、从头播、异常自救（卡流/坏源/重启）、完成数核对等。

### 为什么需要重做一遍

上游那套实现主要面向**较早的课程列表界面**（旧版 `leaf-detail`，以及 `www.yuketang.cn`
上的 `study-unit` 列表）。

2025 年雨课堂启用了新的「学习空间」（`ai-workspace/lms-graph`），改动不小：

- 左侧变成了可折叠的目录树（`nav-item` / `leaf-item`），不再是平铺列表；
- 完成状态从文字变成小图标：**对勾 = 已完成**、**半圆 = 看了一半**、空 = 没开始；
- 播放器换成了 `xt_video_player`，自己维护倍速状态，会把 `video.playbackRate` 改回去；
- 登录页也换成了 `/web` 下的新版扫码页，地址和 DOM 都变了。

这些变化让上游的元素定位、完成判断、倍速设置和登录流程在新界面上**直接失效**。
本项目按新界面重写了上述几块，同时保留对旧界面的兼容 —— 所以它不是简单的 fork，
而是在同一思路下的重做。

---

## 八、AI 辅助说明

本项目**在 AI 编程助手辅助下完成**：代码实现、调试和文档撰写过程中使用了 ZCode
（模型 deepseek-flash）。功能取舍、真实环境测试（包括抓取真实页面结构与日志定位问题）
由作者把关与验证。

特此如实说明，供参考与评估。

---

## 九、免责声明

1. 本项目**仅供学习 Selenium 浏览器自动化技术**，以及个人管理自己已报名课程的使用场景。
2. **它不能替你完成作业、测验、考试**；任何需要你本人完成的学习任务请自行完成。
3. 自动播放课程可能**违反你所在学校/院系的课程要求，以及雨课堂（学堂在线）的用户协议**。
   是否使用、如何使用，由你自行判断并**承担全部后果**（包括但不限于成绩、考勤、账号处罚）。
4. 请勿将本项目用于任何商业用途、代刷服务或批量账号操作。
5. 作者不对因使用本项目造成的任何直接或间接损失负责。
6. `sessionid` 属于你的账号凭证，请勿分享；本项目不会上传你的任何数据。

**如果你不认同以上任何一条，请不要使用本项目。**

---

## 十、许可证

本项目以 **LGPL-2.1** 发布（与上游 `LetMeFly666/YuketangAutoPlayer` 保持一致），
详见 [LICENSE](LICENSE)。
