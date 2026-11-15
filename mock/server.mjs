#!/usr/bin/env node
/**
 * 数据总览采集页的本地 mock 数据源服务。
 *
 * 用法：
 *   node mock/server.mjs            # 默认 http://127.0.0.1:8787
 *   PORT=9000 node mock/server.mjs  # 自定义端口
 *
 * 然后访问 http://127.0.0.1:8787/dashboard.html
 *
 * 提供的接口（用于演示不同采集结局）：
 *   GET /api/metrics/:shopId      成功返回 6 项指标，可用 ?delay=毫秒 模拟慢响应
 *   GET /api/flaky/:shopId        第一次返回 503，之后成功（用于验证“只重试失败网点”）
 *   GET /api/notfound/:shopId     固定 404
 *   GET /api/badjson/:shopId      固定返回非 JSON 文本
 *   GET /api/drop/:shopId         直接断开连接（模拟网络层失败）
 *   GET /api/slow/:shopId         延迟 9.5 秒才响应（超过页面 8 秒超时上限）
 *   GET /mock/shop-data/*.json     静态指标文件
 *   其余路径                        按仓库根目录提供静态文件（dashboard.html / index.html 等）
 */
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const PORT = Number(process.env.PORT || 8787);
const HOST = process.env.HOST || "127.0.0.1";
const SLOW_DELAY_MS = 9500;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".md": "text/markdown; charset=utf-8"
};

const flakyAttempts = new Map();
const requestLog = [];

function metricsFor(shopId, index) {
  const seed = [...String(shopId)].reduce((sum, ch) => sum + ch.charCodeAt(0), 0);
  const base = 900 + (seed % 700) * 6 + index * 37;
  return {
    visits: base,
    exposure: base * 6 + 1200,
    views: Math.round(base * 1.9),
    paidOrders: 20 + ((seed + index) % 90),
    amount: Number((1200 + ((seed * 13 + index * 97) % 38000) + 0.5).toFixed(2)),
    refunds: (seed + index) % 9
  };
}

function json(res, status, body, headers = {}) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(payload),
    "Cache-Control": "no-store",
    ...headers
  });
  res.end(payload);
}

function successPayload(shopId, index) {
  return {
    shopId,
    shopName: `网点 ${shopId}`,
    capturedAt: new Date().toISOString(),
    metrics: metricsFor(shopId, index)
  };
}

function delayFor(url) {
  const raw = url.searchParams.get("delay");
  if (raw === null) return 0;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? Math.min(value, 60000) : 0;
}

async function sendStatic(res, pathname) {
  // 防目录穿越：规范化后必须仍位于仓库根目录内。
  const decoded = decodeURIComponent(pathname);
  const normalized = normalize(decoded).replace(/^([/\\])+/, "");
  const target = resolve(join(ROOT, normalized));
  if (target !== ROOT && !target.startsWith(ROOT + sep)) {
    json(res, 403, { error: "禁止访问仓库之外的路径" });
    return;
  }
  let filePath = target;
  try {
    const info = await stat(filePath);
    if (info.isDirectory()) filePath = join(filePath, "index.html");
    const data = await readFile(filePath);
    res.writeHead(200, {
      "Content-Type": MIME[extname(filePath).toLowerCase()] || "application/octet-stream",
      "Content-Length": data.length,
      "Cache-Control": "no-store"
    });
    res.end(data);
  } catch {
    json(res, 404, { error: "静态文件不存在：" + decoded });
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url || "/", `http://${req.headers.host || HOST + ":" + PORT}`);
  const pathname = url.pathname;
  const entry = { at: Date.now(), method: req.method, path: pathname + url.search };
  requestLog.push(entry);
  if (requestLog.length > 500) requestLog.shift();
  console.log(`[mock] ${entry.method} ${entry.path}`);

  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Accept");
  res.setHeader("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS");
  res.on("error", () => { /* 客户端提前断开（页面超时 abort）时忽略写入错误 */ });

  if (req.method === "OPTIONS") {
    res.writeHead(204);
    res.end();
    return;
  }
  if (req.method !== "GET" && req.method !== "HEAD") {
    json(res, 405, { error: "仅支持 GET" });
    return;
  }

  try {
    if (pathname === "/api/__log") {
      json(res, 200, { requests: requestLog });
      return;
    }
    if (pathname === "/api/__reset") {
      flakyAttempts.clear();
      requestLog.length = 0;
      json(res, 200, { ok: true });
      return;
    }
    if (pathname === "/api/__log/reset") {
      requestLog.length = 0;
      json(res, 200, { ok: true });
      return;
    }

    let match = pathname.match(/^\/api\/metrics\/([^/]+)$/);
    if (match) {
      const shopId = match[1];
      const delay = delayFor(url);
      const index = Number(url.searchParams.get("index") || 0);
      if (delay) await new Promise((r) => setTimeout(r, delay));
      json(res, 200, successPayload(shopId, index));
      return;
    }

    match = pathname.match(/^\/api\/flaky\/([^/]+)$/);
    if (match) {
      const shopId = match[1];
      const attempt = (flakyAttempts.get(shopId) || 0) + 1;
      flakyAttempts.set(shopId, attempt);
      // 奇数次请求失败、偶数次成功：首轮失败、重试成功、再次整批又失败，便于反复验证。
      if (attempt % 2 === 1) {
        const message = attempt === 1
          ? "网点数据源暂不可用（首次请求模拟失败，可重试）"
          : "网点数据源暂不可用（模拟间歇性失败，可重试）";
        json(res, 503, { error: message });
        return;
      }
      json(res, 200, successPayload(shopId, attempt));
      return;
    }

    match = pathname.match(/^\/api\/notfound\/([^/]+)$/);
    if (match) {
      json(res, 404, { error: "未找到该网点的数据源配置" });
      return;
    }

    match = pathname.match(/^\/api\/badjson\/([^/]+)$/);
    if (match) {
      const body = "<html><body>upstream gateway error, not json</body></html>";
      res.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": Buffer.byteLength(body),
        "Cache-Control": "no-store"
      });
      res.end(body);
      return;
    }

    match = pathname.match(/^\/api\/drop\/([^/]+)$/);
    if (match) {
      req.socket.destroy();
      return;
    }

    match = pathname.match(/^\/api\/slow\/([^/]+)$/);
    if (match) {
      const shopId = match[1];
      const delay = delayFor(url) || SLOW_DELAY_MS;
      await new Promise((r) => setTimeout(r, delay));
      if (res.writableEnded || res.destroyed) return;
      json(res, 200, successPayload(shopId, 99));
      return;
    }

    await sendStatic(res, pathname === "/" ? "/index.html" : pathname);
  } catch (err) {
    console.error("[mock] 处理请求失败：", err);
    if (!res.headersSent) json(res, 500, { error: "mock 服务内部错误" });
    else res.end();
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[mock] 数据总览采集演示已启动：http://${HOST}:${PORT}/dashboard.html`);
  console.log(`[mock] 静态数据示例：http://${HOST}:${PORT}/mock/shop-data/shop-2.json`);
});
