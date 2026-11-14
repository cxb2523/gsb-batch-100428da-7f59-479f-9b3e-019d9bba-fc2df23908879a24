# Boss 直聘 HR 智能招聘助手（桌面 RPA）

> **English** · A desktop RPA assistant for **BOSS Zhipin (Boss 直聘)** recruiting, built for HR / recruiters / hiring managers. Preset a job profile → auto-apply filters → scrape candidates in **list mode** → rule-based / AI matching → auto-greet → log to local Excel or Feishu Bitable. Requires **Windows + Python 3.10+ + Microsoft Edge**. All sensitive runtime data (`data/`: login sessions, resumes, API keys, records) is **git-ignored and never committed**.

面向 HR / 招聘专员 / 招聘经理的 Boss 直聘自动化工具：预设岗位画像 → 自动设置筛选 → 列表模式抓取候选人 → 规则/AI 匹配 → 自动打招呼 → 记录到本地 Excel 或飞书多维表。

## 运行环境要求（务必先读）

### 1. 前置登录（Edge 独立配置）

- 本应用使用 **Edge 浏览器 + 独立用户配置目录**（`data/edge_profile`）保持登录态，不会与你日常浏览器混用 Cookie。
- **首次运行前**：应用会弹出 Edge 窗口并打开 `https://www.zhipin.com/`，请用 BOSS 直聘**企业账号**完成扫码 / 密码登录。
- 登录后会话保存在 `data/edge_profile`，之后运行自动复用，无需重复登录。
- 避免多账号混用：一个应用实例只绑定一个企业账号；如需换号，删除 `data/edge_profile` 后重新登录。

### 2. 网络稳定性

- 本应用依赖持续稳定的网络连接，建议延迟 **< 100ms、丢包率 < 0.1%**。
- 若使用公司 VPN 或海外网络，可能出现页面加载超时、元素定位失败等问题。
- **运行期间请勿切换网络或断开 VPN**。

### 3. 列表模式（强制）

- 本流程**只支持「列表模式」**定位候选人。
- 若页面因系统升级恢复为默认卡片模式，请在启动流程前于候选人列表页**点击鼠标右键 → 选择「切换为列表模式」**。
- 应用启动时会自动检测模式：检测到卡片模式会提示并等待你手动切换（最长 90 秒）。
- 若页面结构改版导致定位失败，请按文末「选择器校准」更新 `engine/selectors.py`。

### 4. 安全验证（人机验证）

- Boss 直聘会对自动化环境做客户端检测。本应用已内置对抗措施（隐藏自动化特征、原生窗口、慢动作模拟等），不会再出现「进入即跳回空白页」。
- 首次进入时页面可能停在**安全验证页**（URL 含 `verify`）。这是正常的人机验证流程：**在弹出的窗口中手动完成滑块 / 拖动验证**（几秒钟），应用检测到验证通过后会自动继续，最长等待 3 分钟。
- 请勿在验证期间关闭浏览器窗口。

## 安装

需要：Windows + Python 3.10+ + Edge 浏览器。

```powershell
cd boss-hr-desktop
python -m pip install -r requirements.txt
```

运行：

```powershell
python app.py
```

或双击 `run.bat`。

## 使用说明

### ① 启动参数（岗位画像与筛选）

| 参数 | 说明 |
| --- | --- |
| 岗位名称 | 必填。如「Java开发」「产品经理」，用于切换推荐岗位 |
| 最小 / 最大年龄 | 整数，筛选候选人年龄范围 |
| 活跃度 | 多选：刚刚活跃 / 今日活跃 / 本周活跃 |
| 性别 | 男 / 女 / 不限 |
| 院校要求 | 多选：985 / 211 / 双一流 |
| 经验要求 | 多选：1-3年 / 3-5年 / 5-10年 / 10年以上 |
| 学历要求 | 多选：大专 / 本科 / 硕士 / 博士 |
| 求职意向 | 多选：只看在线 / 只看今日查看 |
| 跳槽频率 | 多选：稳定 / 一般 / 频繁 |
| 新牛人筛选 | 是：走「新牛人」子流程；否：常规流程 |
| 打招呼数量 | 单次运行最大主动沟通人数 |
| 记录方式 | 本地 Excel / 飞书多维表 |

### ② 打招呼与节奏

- **运行模式**：
  - **手动辅助（默认）**：应用登录并打开沟通页后进入待命状态，界面上有三个按钮——「① 点候选人」（依次选中沟通列表中的候选人）、「② 发送常用语」（向当前候选人发送配置的招呼语）、「③ 求简历」（向当前候选人请求简历）。你点哪个，应用才执行哪个，节奏完全由你控制。
  - **推荐牛人自动打招呼**：打开推荐牛人页，按岗位画像筛选后自动打招呼（需已发布岗位）；
  - **沟通页发常用语+求简历**：打开沟通页（`/web/chat/index`），对聊天列表中的候选人依次发送常用语（优先点聊天框「常用语」快捷按钮，找不到则直接发送配置的招呼语），随后点击「求简历」。已处理过的人自动跳过，不会重复。
- **自动（真实鼠标事件）**：推荐。通过浏览器底层派发真实点击，绕过页面 `isTrusted` 校验。
- **半自动（人工确认）**：高亮按钮后由人工点击，更保守。
- 随机延时（下限 / 上限）：模拟真人节奏，降低风控识别概率。

### ③ 记录方式

- **本地 Excel**：每次成功打招呼写入 `data/招聘记录.xlsx`（UTF-8，Excel 直接打开）。
- **飞书多维表**：在飞书开放平台创建企业自建应用，获取 App ID / App Secret，并在多维表中获取 App Token / Table ID 填入界面。写入失败时自动保留到本地 `data/records.jsonl` 兜底。

## 匹配引擎

- **本地规则（默认，离线）**：筛选条件（性别 / 年龄 / 学历 / 经验 / 关键词）硬性拦截，不满足直接跳过；关键词命中决定匹配分，低于最低分跳过。
- **AI 引擎（可选）**：配置 OpenAI 兼容接口后，由 AI 对「岗位画像 + 候选人简历」打分（JSON 输出 `score` / `reason`）；失败自动回退本地规则。

## 离线演示（不登录也能先跑通）

1. 启动演示服务器：

```powershell
cd boss-hr-desktop
python -m http.server 8123
```

2. 浏览器打开 `http://127.0.0.1:8123/mock/mock-zhipin.html`（模拟 BOSS 直聘列表页，含 `isTrusted` 检测与列表/卡片模式切换）。
3. 运行单元测试与端到端测试：

```powershell
python tests/test_matcher.py
python tests/e2e_mock.py
```

## 打包为独立 exe（可选）

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name "BossHR助手" --add-data "engine;engine" app.py
```

打包产物在 `dist/BossHR助手/`。注意：`data/edge_profile` 为运行时生成，不要打进包。

## 选择器校准

BOSS 直聘前端改版频繁，若出现「未找到筛选面板 / 未命中列表行」等日志：

1. 打开目标页面，右键候选人元素 → 检查，找到稳定 class；
2. 修改 `engine/selectors.py` 中对应项；
3. 重新运行应用。

## 风险提示

- Boss 直聘平台条款禁止批量自动化操作，官方已上线 AI 风控模型（识别「批量索取简历」「相似话术反复出现」并封禁账号）。
- 请合理控制打招呼数量与节奏（默认单次 30 人、4-8 秒随机延时），封号风险由使用者自行评估。
- 简历数据若走 AI 引擎会发送到所配置的接口，请注意数据合规。

## 目录结构

```
boss-hr-desktop/
├── app.py                  # 入口（tkinter 界面）
├── config.py               # 配置读写 / Edge 路径 / 数据目录
├── engine/
│   ├── rpa.py              # Playwright RPA 主流程（登录/筛选/翻页/抓取/打招呼）
│   ├── selectors.py        # 元素选择器（集中校准）
│   ├── matcher.py          # 匹配引擎（规则 + AI）
│   ├── recorder.py         # 记录（Excel / JSONL）
│   └── feishu.py           # 飞书多维表写入
├── ui/main_window.py       # 启动参数界面 / 日志 / 状态
├── mock/                   # 离线演示页
├── tests/                  # 单元测试与端到端测试
├── config.example.json     # 配置模板（复制为 data/config.json 后填写）
└── data/                   # 运行时生成：配置 / 登录态 / 记录（已被 .gitignore 排除，不入库）
```

## 仓库与隐私说明（提交到 Git / GitHub 前必读）

- **`data/` 目录已被 `.gitignore` 排除**，它包含真实简历（`data/resumes/`）、企业账号登录态（`data/edge_profile*`，**等同于账号密码**）、明文密钥（`data/config.json` 里的飞书 App Secret / AI Key）与历史使用记录 —— 这些**切勿提交到任何远程仓库**。
- 首次使用时，将本仓库的 `config.example.json` 复制为 `data/config.json` 并填入你自己的值：

```powershell
cp config.example.json data/config.json
# 然后用记事本/编辑器填入：岗位、筛选、AI 接口、飞书 App ID/Secret 等
```

- 本项目内置的反检测措施（`engine/stealth.py` 隐藏 `navigator.webdriver`、清理 `cdc_*`、伪造指纹等）会绕过平台风控检测，使用请自行评估平台条款与封号风险。
