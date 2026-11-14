# DynamicProxyPool 使用指南

## 🚀 快速开始

### 1️⃣ 一键启动（前台运行，推荐）

```bash
# 获取订阅 + 检测节点 + 启动代理（前台运行）
python start_proxy.py up --fetch --check -f
```

**特点：**
- ✅ 终端保持运行，实时查看状态
- ✅ 按 `Ctrl+C` 自动停止代理，确保清理干净
- ✅ 避免后台残留进程

**输出示例：**
```
🔄 正在获取订阅...
  ✅ 获取到 40 个节点
✅ 已生成配置文件
启动 Mihomo...
控制端已就绪: http://127.0.0.1:19090

💡 前台运行模式：按 Ctrl+C 停止代理
🔥 代理已启动，保持运行中...
📡 代理地址: http://127.0.0.1:17890
🎛️  控制面板: http://127.0.0.1:19090
```

---

### 2️⃣ 后台运行

```bash
# 启动后立即返回，代理在后台运行
python start_proxy.py start

# 停止代理
python start_proxy.py stop
```

---

## 📖 详细命令

### 订阅管理

```bash
# 拉取订阅节点
python start_proxy.py fetch

# 生成配置文件
python start_proxy.py generate
```

### 代理控制

```bash
# 后台启动
python start_proxy.py start

# 前台启动（按 Ctrl+C 停止）
python start_proxy.py start -f

# 停止代理
python start_proxy.py stop

# 查看状态
python start_proxy.py status
```

### 节点检测

```bash
# 测试所有节点延迟和可用性
python start_proxy.py check
```

### 组合命令

```bash
# 一键：拉取订阅 + 生成配置 + 启动代理
python start_proxy.py up --fetch

# 一键：拉取订阅 + 启动 + 检测节点（后台）
python start_proxy.py up --fetch --check

# 一键：拉取订阅 + 启动 + 检测节点（前台）
python start_proxy.py up --fetch --check -f
```

---

## 🔧 配置信息

### 端口设置

| 用途 | 地址 |
|------|------|
| 代理端口 | `http://127.0.0.1:17890` |
| SOCKS端口 | `127.0.0.1:17891` |
| API控制端口 | `http://127.0.0.1:19090` |

### 文件位置

```
DynamicProxyPool/
├── bin/mihomo/           # 内核二进制文件
├── proxies/             
│   ├── raw_nodes.yaml   # 原始节点数据
│   ├── clash_profile.yaml # 生成的配置文件
│   └── proxy_test_results.json # 节点测试结果
└── .runtime/
    ├── mihomo_home/     # 内核工作目录
    ├── mihomo.pid       # 进程ID
    └── mihomo.log       # 运行日志
```

---

## 💻 程序中使用代理

### Python

```python
import requests

proxies = {
    'http': 'http://127.0.0.1:17890',
    'https': 'http://127.0.0.1:17890'
}

# 使用代理请求
response = requests.get('https://www.google.com', proxies=proxies)
print(response.status_code)

# 检查IP地址
response = requests.get('http://ip-api.com/json', proxies=proxies)
print(response.json())
```

### curl

```bash
# HTTP代理
curl -x http://127.0.0.1:17890 https://www.google.com

# 检查代理IP
curl -x http://127.0.0.1:17890 http://ip-api.com/json
```

### Selenium

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

chrome_options = Options()
chrome_options.add_argument('--proxy-server=http://127.0.0.1:17890')

driver = webdriver.Chrome(options=chrome_options)
driver.get('https://www.google.com')
```

---

## 🎯 使用场景

### 场景1：临时使用（推荐前台模式）

```bash
# 启动代理（前台）
python start_proxy.py up --fetch -f

# 在另一个终端使用代理
curl -x http://127.0.0.1:17890 https://www.google.com

# 用完按 Ctrl+C，自动清理
```

### 场景2：长期运行（后台模式）

```bash
# 启动代理（后台）
python start_proxy.py up --fetch

# 代理在后台运行，终端可关闭
# 需要停止时：
python start_proxy.py stop
```

### 场景3：定时更新节点

```bash
# 重新获取订阅并重载配置（不中断服务）
python start_proxy.py fetch
python start_proxy.py generate
python start_proxy.py start --reload
```

---

## ❓ 常见问题

### Q1: 端口被占用怎么办？

修改 `src/generate_clash_profile.py` 中的端口配置：

```python
profile = {
    "mixed-port": 17890,  # 改为其他端口
    "external-controller": "127.0.0.1:19090",  # 改为其他端口
}
```

### Q2: 如何查看日志？

```bash
tail -f .runtime/mihomo.log
```

### Q3: 启动失败怎么办？

```bash
# 1. 检查状态
python start_proxy.py status

# 2. 查看日志
cat .runtime/mihomo.log

# 3. 环境检查
python start_proxy.py doctor --check-api
```

### Q4: 如何更换订阅链接？

编辑 `src/fetch_proxies.py` 文件中的 `subscriptions` 列表：

```python
subscriptions = [
    'https://your-subscription-link',
    # 可以添加多个订阅
]
```

---

## 🎊 最佳实践

1. **首次使用**：使用 `-f` 前台模式，观察是否正常
2. **开发调试**：前台模式，方便随时停止
3. **生产环境**：后台模式，配合系统服务管理
4. **定期检测**：运行 `check` 命令，更新可用节点列表

---

## 🔐 安全提示

- 订阅链接包含敏感信息，请勿公开分享代码仓库
- 建议将 `src/fetch_proxies.py` 加入 `.gitignore`
- 代理仅监听本地回环地址（127.0.0.1），外部无法访问
