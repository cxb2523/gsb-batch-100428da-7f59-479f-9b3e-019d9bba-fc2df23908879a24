# deepwiki-get

<p>
  <a href="README.md">简体中文</a> | <a href="README-EN.md">English</a>
</p>
快速地将deepwiki站点转换为md文件。

## 📖 工作原理

DeepWiki 使用 Next.js 构建，站点的 RSC 响应中已经包含了完整的 Markdown 内容。

项目通过拦截 RSC 请求的方式直接获取站点 md 文本。因此速度很快。

**RSC 拦截**

- 拦截 RSC 请求
- 提取 Markdown 内容

**爬虫爬取**（对比）

- 访问网页
- 下载 HTML
- 解析 DOM
- HTML 转换为 Markdown
- 路由
- ...

---

## 🚀 使用说明

### 安装依赖

```bash
# 安装 Python 依赖
uv sync
# 安装 Playwright 浏览器
python -m playwright install chromium
```

### 基本用法

```bash
python -m src.application.interface.cli wiki \
	"https://deepwiki.com/username/repository" \
	-o "/path/to/output"
```

### 参数说明

| 参数       | 类型   | 必需 | 说明              |
| ---------- | ------ | ---- | ----------------- |
| `-o` | string | ✅   | 输出目录路径      |

### 输出约定

输出目录固定为 `<输出目录>/wiki/<组织>/<仓库>`，其中：

- 每个导航页面都会得到一个明确结局：`saved`（已保存）、`title_mismatch`（标题匹配失败）或 `content_missing`（内容缺失）。
- 只有成功保存的页面才会写入文件；匹配不到内容的页面不会产生空文件或错页文件，其标题与原因会在运行结束时汇总打印。
- `index.md` 只列出实际保存成功的页面，链接文件名与磁盘文件一致。
- 页面与内容的匹配优先使用页面 ID / URL 路径等稳定标识，标题匹配仅作为兜底，且同一份内容不会被多个页面重复占用。

### 运行测试

```bash
uv run pytest
```

测试仅使用本地假数据，不依赖真实网络请求。

---

## 🤝 贡献指南

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add some amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 开启 Pull Request



