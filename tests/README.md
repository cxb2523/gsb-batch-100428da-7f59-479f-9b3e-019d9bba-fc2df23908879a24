# 重建链路容错测试

`index.html` 底部脚本把十段混淆数据解码为真实页面，并按原顺序重新执行其中的脚本。
本目录用零第三方依赖的 Node + 无头 Chrome（DevTools Protocol）验证：**单个脚本失败不会阻断其余脚本，且失败可查询、可核对。**

## 被测行为

重建结束后，页面以三种方式暴露同一份执行报告：

1. 全局变量 `window.__rebuildScriptReport__`
2. 隐藏 DOM 节点 `<script type="application/json" id="__rebuild_script_report__">`
3. document 上的 `__rebuildScriptReport__` 自定义事件（每次更新都派发，可监听最终态）

报告结构：

```json
{
  "total": 6,
  "succeeded": 5,
  "failed": 1,
  "failures": [
    { "index": 3, "type": "external", "src": "/ext/missing-404.js",
      "status": "failed", "message": "Failed to load external script: /ext/missing-404.js" }
  ],
  "results": [ /* 每个脚本一条，含 index/type/src/status/message，按执行顺序 */ ]
}
```

- 外链脚本（`type:"external"`）：记录 `src`，失败信息来自 `onerror`；仍等 `onload`/`onerror` 完成后才进入下一个，保持原顺序语义。
- 内联脚本（`type:"inline"`）：`src` 为空字符串，`message` 记录异常（含 message/stack）。
  内联经典脚本的抛错不会从 `appendChild` 同步抛出，而是异步派发到 `window` 的 `error` 事件，
  因此重建链用作用域受控的捕获阶段监听抓取该错误，再继续执行下一个。

## 目录

| 路径 | 作用 |
| --- | --- |
| `lib/codec.js` | 与页面一致的 XOR+base64 编解码及十分片拆分 |
| `lib/gen-pages.js` | 以**真实 `index.html` 为外壳**，仅替换十个混淆分片，生成三个样例页到 `samples/` |
| `lib/server.js` | 零依赖静态文件服务器（缺失脚本返回 404） |
| `lib/run-tests.js` | 启动无头 Chrome，经 CDP 打开样例页并断言全部行为 |
| `www/ext/*.js` | 样例引用的外链脚本（向 `window.__order` 写入执行标记） |
| `samples/*.html` | 已生成的混淆样例页，可直接放到任意静态服务器手动打开 |

## 三个场景

1. **all-success.html** — 2 个外链 + 2 个内联 + head 中的外链，共 5 个脚本全部成功；
   同时核对 head 白名单（保留 meta/link/base/style，剥离 iframe）与 body 注入。
2. **external-fail.html** — 第 4 个脚本（外链 `/ext/missing-404.js`，服务器返回 404）失败；
   验证其后的外链与内联脚本仍按序执行，报告 `failed:1`、失败明细的序号/类型/地址正确。
3. **inline-throw.html** — 第 3 个脚本（内联）`throw new Error('boom-inline-xyz')`；
   验证异常被捕获、后续脚本仍执行、计数与错误信息正确，且浏览器不再将其报为 Uncaught。

测试还通过 `git show HEAD:index.html` 取**改造前的原始外壳**生成对照页，
确认旧链路在相同失败场景下不暴露任何报告/失败记录（失败不可见）。

## 运行

需要本机安装 Chrome 或 Edge（可用环境变量 `CHROME_BIN` 指定可执行文件路径）。

```bash
node tests/lib/gen-pages.js   # 从当前 index.html 重新生成样例页
node tests/lib/run-tests.js   # 端到端测试（或 npm test）
```

手动查看样例（样例页引用的外链脚本在 `www/ext/` 下，需把样例页与 `www` 放在同一根目录；
自动化测试 `run-tests.js` 会在系统临时目录自动组装这个根）：

```bash
mkdir -p /tmp/manual-root && cp tests/samples/*.html /tmp/manual-root/ && cp -r tests/www/ext /tmp/manual-root/
node tests/lib/server.js /tmp/manual-root
# 浏览器打开 http://127.0.0.1:<端口>/external-fail.html，
# 控制台执行 window.__rebuildScriptReport__ 即可看到报告
```

> Windows 沙箱环境注意：Chromium 要求能解析默认 User Data 目录，否则会拒绝远程调试
> （报 `DevTools remote debugging requires a non-default data directory`）。
> `run-tests.js` 会幂等创建 `%USERPROFILE%\AppData\Local|Roaming` 来规避该环境限制。
