# ⚡ 快速开始指南

## 5 分钟上手

⚠️ 第一次使用前先填订阅并放好内核。  

1) **填订阅**：编辑 `src/fetch_proxies.py` 的 `subscriptions` 列表，写入你的机场订阅。  
   （带“订阅”字样的占位符会被自动忽略，必须改成真实 URL）  
2) **准备内核（可选，使用内置核心时）**：将官方资产解压到 `bin/mihomo/<平台>/`，或执行  
   ```bash
   # 按平台推测资产名（推荐提供 version 或直接给 url）
   python start_proxy.py download --version v1.18.9
   # 或直接指定直链
   python start_proxy.py download --url <你的发行版直链>
   ```
3) **一键拉起代理**：  
   ```bash
   python start_proxy.py up --fetch --check   # 拉订阅 + 生成配置 + 启动内核 + 可用性检测
   python start_proxy.py status               # 查看状态
   python start_proxy.py stop                 # 用完即停，不改系统代理
   python start_proxy.py doctor --check-api   # 环境体检
   ```
   （若已拉过订阅，可省略 `--fetch`；默认启动后会切到 global 模式。）

4) **运行爬虫/访问任务**：  
   ```bash
   python run_selenium.py
   ```
   任务配置在 `src/selenium_with_proxy.py` 的 `CONFIG`。

## 常用调试
- 智能诊断 & IP 切换测试：`python src/test_ip_switch_smart.py`
- 并发检测节点：`python start_proxy.py check`
- 手动切换测试：`python src/test_ip_switch_manual.py`

## 关键端口（默认）
- API: `http://127.0.0.1:9090`
- 代理：`http://127.0.0.1:7892`
