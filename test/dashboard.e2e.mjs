#!/usr/bin/env node
/**
 * dashboard.html 端到端验证脚本（无第三方依赖）。
 *
 * 通过 Chrome DevTools Protocol 驱动本机 Chrome，验证：
 *   1. 初次打开：8 张卡片 / 8 条结局、均为待采集
 *   2. 连点“开始采集”不产生重复请求、不产生重复卡片
 *   3. 单网点失败/超时不影响其他网点；失败原因与卡片、结局列表一致
 *   4. 超时有 8 秒上限并归入失败结局
 *   5. 仅重试失败/超时网点，已成功网点不重复请求
 *   6. 重试期间按钮展示正在进行的网点范围
 *   7. 重试完成后结局列表与卡片同步更新
 *
 * 前置：先启动 mock 服务 `node mock/server.mjs`（默认 127.0.0.1:8787）。
 * 用法：node test/dashboard.e2e.mjs
 * 环境变量：DASHBOARD_BASE_URL / CHROME_PATH / CDP_PORT
 */
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.env.DASHBOARD_BASE_URL || "http://127.0.0.1:8787";
const CDP_PORT = Number(process.env.CDP_PORT || 9333);
const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
].filter(Boolean);

const results = [];
function check(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"} | ${name}${detail ? " | " + detail : ""}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findBrowser() {
  const found = CHROME_CANDIDATES.find((p) => existsSync(p));
  if (!found) throw new Error("未找到 Chrome/Edge，可用 CHROME_PATH 指定");
  return found;
}

// Chrome 在 Windows 上只接受正斜杠或双反斜杠的 --user-data-dir。
function toBrowserPath(path) {
  return path.replace(/\\/g, "/");
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    this.pageErrors = [];
    this.resourceErrors = [];
    this.requests = [];
    ws.addEventListener("message", (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message));
        else resolve(msg.result);
        return;
      }
      if (msg.method === "Runtime.exceptionThrown") {
        const details = msg.params?.exceptionDetails || {};
        this.pageErrors.push(details.exception?.description || details.text || "未知异常");
      } else if (msg.method === "Log.entryAdded" && msg.params?.entry?.level === "error") {
        // mock 场景里 404/503/连接失败是刻意制造的，这里单独记录，不算脚本错误。
        this.resourceErrors.push(msg.params.entry.text);
      } else if (msg.method === "Network.requestWillBeSent") {
        this.requests.push({ url: msg.params?.request?.url || "", at: Date.now() });
      }
    });
  }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve, { once: true });
      ws.addEventListener("error", () => reject(new Error("CDP WebSocket 连接失败")), { once: true });
    });
    return new Cdp(ws);
  }
  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  close() {
    try { this.ws.close(); } catch { /* 已关闭 */ }
  }
}

async function getJson(path, tries = 40) {
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${CDP_PORT}${path}`);
      if (res.ok) return await res.json();
    } catch { /* 浏览器启动中 */ }
    await sleep(250);
  }
  throw new Error("无法连接 CDP 端点 " + path);
}
async function evaluate(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true
  });
  if (result.exceptionDetails) {
    throw new Error("页面脚本异常：" + JSON.stringify(result.exceptionDetails.exception?.description || result.exceptionDetails.text));
  }
  return result.result.value;
}

async function requestCounts(cdp, fromIndex) {
  // 以浏览器实际发出的网络请求为准（包含连接失败、未到达 mock 的请求）。
  const counts = {};
  for (const entry of cdp.requests.slice(fromIndex)) {
    const m = entry.url.match(/\/(?:api\/(?:metrics|flaky|notfound|badjson|slow)\/|mock\/shop-data\/)([^/?]+)/);
    if (m) {
      const key = m[1].replace(/\.json$/, "");
      counts[key] = (counts[key] || 0) + 1;
    }
  }
  return counts;
}

async function waitFor(cdp, expression, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(cdp, expression)) return true;
    await sleep(150);
  }
  throw new Error(`等待超时（${label}）：${expression}`);
}

async function snapshotDom(cdp) {
  return evaluate(cdp, `(() => {
    const state = window.__dashboardState();
    const cards = [...document.querySelectorAll('.shop-card')].map(card => ({
      id: card.dataset.cardShop,
      status: card.dataset.status,
      badge: card.querySelector('[data-role="badge"]').textContent,
      badgeStatus: card.querySelector('[data-role="badge"]').dataset.status,
      values: [...card.querySelectorAll('.m-value')].map(n => n.textContent),
      reason: card.querySelector('[data-role="card-reason"]').textContent,
      duration: card.querySelector('[data-role="card-duration"]').textContent
    }));
    const rows = [...document.querySelectorAll('#outcomeBody tr')].map(tr => ({
      id: tr.dataset.outcomeShop,
      status: tr.dataset.status,
      statusText: tr.querySelector('[data-role="outcome-status"]').textContent,
      source: tr.querySelector('[data-role="outcome-source"]').textContent,
      duration: tr.querySelector('[data-role="outcome-duration"]').textContent,
      reason: tr.querySelector('[data-role="outcome-reason"]').textContent
    }));
    return {
      batchRunning: state.batchRunning,
      hasCompleted: state.hasCompleted,
      lastRun: state.lastRun,
      shops: state.shops,
      startText: document.getElementById('startBtn').textContent,
      startDisabled: document.getElementById('startBtn').disabled,
      retryHidden: document.getElementById('retryBtn').hidden,
      retryText: document.getElementById('retryBtn').textContent,
      retryDisabled: document.getElementById('retryBtn').disabled,
      resetDisabled: document.getElementById('resetConfigBtn').disabled,
      batchStatus: document.getElementById('batchStatus').textContent,
      progressWidth: document.getElementById('progressBar').style.width,
      cards,
      rows
    };
  })()`);
}

function statuses(snap) {
  return Object.fromEntries(snap.shops.map((s) => [s.id, s.status]));
}

function assertCardRowConsistency(label, snap) {
  const cardById = Object.fromEntries(snap.cards.map((c) => [c.id, c]));
  const rowById = Object.fromEntries(snap.rows.map((r) => [r.id, r]));
  const problems = [];
  for (const shop of snap.shops) {
    const card = cardById[shop.id];
    const row = rowById[shop.id];
    if (!card || !row) {
      problems.push(`${shop.id} 缺少卡片或结局行`);
      continue;
    }
    if (card.status !== shop.status) problems.push(`${shop.id} 卡片状态 ${card.status} != 内部 ${shop.status}`);
    if (row.status !== shop.status) problems.push(`${shop.id} 结局状态 ${row.status} != 内部 ${shop.status}`);
    if (card.badgeStatus !== shop.status) problems.push(`${shop.id} 徽标状态不同步`);
    if (shop.status !== "success" && card.values.some((v) => v !== "—")) {
      problems.push(`${shop.id} 非成功状态仍渲染了指标 ${card.values.join(",")}`);
    }
    if (shop.status === "success" && card.values.every((v) => v === "—")) {
      problems.push(`${shop.id} 成功状态没有渲染指标`);
    }
    if (shop.status === "success" && !/^¥/.test(card.values[4])) {
      problems.push(`${shop.id} 金额未按金额格式渲染`);
    }
    if (shop.status !== "running" && !card.reason.trim()) problems.push(`${shop.id} 卡片原因为空`);
    if (!row.reason.trim()) problems.push(`${shop.id} 结局原因为空`);
  }
  check(`卡片与结局列表一致（${label}）`, problems.length === 0, problems.join("; "));
}

async function main() {
  const mockUp = await fetch(BASE + "/api/metrics/probe").then((r) => r.ok).catch(() => false);
  if (!mockUp) throw new Error("mock 服务未启动，请先运行 node mock/server.mjs");
  await fetch(BASE + "/api/__reset");

  // 端口已被占用说明有残留浏览器实例：连上去会读到旧页面状态，必须直接报错而不是误判。
  const stale = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`).then(() => true).catch(() => false);
  if (stale) {
    throw new Error(`CDP 端口 ${CDP_PORT} 已被占用，请先结束残留的测试浏览器或设置 CDP_PORT 换端口`);
  }

  const profileDir = await mkdtemp(join(tmpdir(), "dashboard-e2e-"));
  const chrome = spawn(findBrowser(), [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-extensions",
    "--window-size=1400,1200",
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${toBrowserPath(profileDir)}`,
    "about:blank"
  ], { stdio: ["ignore", "pipe", "pipe"] });
  const chromeLogs = [];
  chrome.stdout.on("data", (d) => chromeLogs.push(String(d)));
  chrome.stderr.on("data", (d) => chromeLogs.push(String(d)));

  let cdp;
  let failed = false;
  try {
    await getJson("/json/version");
    const page = await (async () => {
      for (let i = 0; i < 60; i++) {
        const list = await getJson("/json/list");
        const found = list.find((t) => t.type === "page");
        if (found) return found;
        await sleep(250);
      }
      throw new Error("未找到浏览器页面目标");
    })();
    cdp = await Cdp.connect(page.webSocketDebuggerUrl);
    await cdp.send("Runtime.enable");
    await cdp.send("Log.enable");
    await cdp.send("Network.enable");
    await cdp.send("Page.enable");
    const loadedAt = cdp.requests.length;
    await cdp.send("Page.navigate", { url: BASE + "/dashboard.html" });
    await waitFor(cdp, `document.readyState === 'complete' && typeof window.__dashboardState === 'function'`, 15000, "页面加载");

    // 1. 初始状态
    let snap = await snapshotDom(cdp);
    check("初始渲染 8 张网点卡片", snap.cards.length === 8, `实际 ${snap.cards.length}`);
    check("初始渲染 8 条结局行", snap.rows.length === 8, `实际 ${snap.rows.length}`);
    check("初始全部为待采集", Object.values(statuses(snap)).every((s) => s === "idle"));
    check("初始进度条为 0%", snap.progressWidth === "0%", snap.progressWidth);
    check("初始只有开始采集按钮可见", snap.retryHidden === true && snap.startDisabled === false);

    // 2. 连点开始采集：不能产生重复请求 / 重复卡片
    await evaluate(cdp, `document.getElementById('startBtn').click(); document.getElementById('startBtn').click(); document.getElementById('startBtn').click();`);
    await sleep(300);
    snap = await snapshotDom(cdp);
    check("采集开始后按钮禁用", snap.startDisabled === true);
    check("采集开始后出现进行中状态", /采集/.test(snap.batchStatus) && Object.values(statuses(snap)).includes("running"), snap.batchStatus);
    check("连点 3 次仍只有 8 张卡片", snap.cards.length === 8, `实际 ${snap.cards.length}`);
    check("连点 3 次仍只有 8 条结局", snap.rows.length === 8, `实际 ${snap.rows.length}`);

    await waitFor(cdp, `window.__dashboardState().batchRunning === false && window.__dashboardState().hasCompleted === true`, 20000, "整批采集结束");
    await sleep(200);
    const countsAfterFirst = await requestCounts(cdp, loadedAt);
    check("每个网点恰好请求 1 次（连点未产生重复请求）",
      Object.keys(countsAfterFirst).length === 8 && Object.values(countsAfterFirst).every((n) => n === 1),
      JSON.stringify(countsAfterFirst));

    snap = await snapshotDom(cdp);
    const firstStatuses = statuses(snap);
    const firstMetrics = Object.fromEntries(snap.shops.map((s) => [s.id, s.metrics]));
    check("成功网点：shop-1 / shop-2 / shop-8", ["shop-1", "shop-2", "shop-8"].every((id) => firstStatuses[id] === "success"), JSON.stringify(firstStatuses));
    check("HTTP 5xx 网点记为失败（shop-3）", firstStatuses["shop-3"] === "error");
    check("HTTP 404 网点记为失败（shop-4）", firstStatuses["shop-4"] === "error");
    check("非 JSON 响应记为失败（shop-5）", firstStatuses["shop-5"] === "error");
    check("网络连接失败记为失败（shop-6）", firstStatuses["shop-6"] === "error");
    check("慢响应网点记为超时（shop-7）", firstStatuses["shop-7"] === "timeout");
    check("单网点失败未中断整批（8 个网点均有结局）", Object.values(firstStatuses).every((s) => s === "success" || s === "error" || s === "timeout"));
    check("失败原因包含 HTTP 状态码", /HTTP 503/.test(snap.shops.find((s) => s.id === "shop-3").reason), snap.shops.find((s) => s.id === "shop-3").reason);
    check("超时原因说明 8 秒上限", /8 秒/.test(snap.shops.find((s) => s.id === "shop-7").reason), snap.shops.find((s) => s.id === "shop-7").reason);

    const timeoutShop = snap.shops.find((s) => s.id === "shop-7");
    check("超时耗时被限制在 8 秒附近（<= 9.5s）",
      timeoutShop.durationMs >= 7500 && timeoutShop.durationMs <= 9500,
      `${Math.round(timeoutShop.durationMs)} ms`);
    check("成功卡片渲染 6 项指标", snap.cards.find((c) => c.id === "shop-1").values.filter((v) => v !== "—").length === 6);
    check("失败卡片不渲染指标", snap.cards.find((c) => c.id === "shop-4").values.every((v) => v === "—"));
    check("整批结束后出现重试按钮并计数", snap.retryHidden === false && /5/.test(snap.retryText), snap.retryText);
    check("开始按钮变为重新采集", /重新采集/.test(snap.startText), snap.startText);
    check("进度条到达 100%", snap.progressWidth === "100%", snap.progressWidth);
    assertCardRowConsistency("首轮结束", snap);

    // 3. 只重试失败/超时网点
    await fetch(BASE + "/api/__log/reset");
    const retryStartIndex = cdp.requests.length;
    await evaluate(cdp, `document.getElementById('retryBtn').click()`);
    await sleep(400);
    snap = await snapshotDom(cdp);
    check("重试期间按钮展示正在进行的网点范围",
      /重试/.test(snap.retryText) && /广州分店/.test(snap.retryText) &&
      /成都分店/.test(snap.retryText) && snap.batchStatus.includes("正在重试"),
      snap.retryText + " | " + snap.batchStatus);
    check("重试期间开始按钮与配置禁用", snap.startDisabled === true && snap.retryDisabled === true && snap.resetDisabled === true);
    check("重试期间重试按钮仍可见并展示范围", snap.retryHidden === false && /重试范围/.test(snap.retryText), snap.retryText);

    await waitFor(cdp, `window.__dashboardState().batchRunning === false`, 20000, "重试结束");
    await sleep(200);
    const countsAfterRetry = await requestCounts(cdp, retryStartIndex);
    check("重试只请求失败/超时网点（5 个）",
      Object.keys(countsAfterRetry).sort().join(",") === "shop-3,shop-4,shop-5,shop-6,shop-7",
      JSON.stringify(Object.keys(countsAfterRetry).sort()));
    check("已成功网点未重复请求",
      !countsAfterRetry["shop-1"] && !countsAfterRetry["shop-2"] && !countsAfterRetry["shop-8"]);

    snap = await snapshotDom(cdp);
    const secondStatuses = statuses(snap);
    check("重试后原先失败的 shop-3 变为成功", secondStatuses["shop-3"] === "success", secondStatuses["shop-3"]);
    check("重试后仍失败的网点保持失败", secondStatuses["shop-4"] === "error" && secondStatuses["shop-5"] === "error" && secondStatuses["shop-6"] === "error");
    check("重试后仍超时的网点保持超时", secondStatuses["shop-7"] === "timeout");
    check("已成功网点指标保持不变",
      JSON.stringify(snap.shops.find((s) => s.id === "shop-1").metrics) === JSON.stringify(firstMetrics["shop-1"]));
    check("重试后结局文案更新", /重试完成/.test(snap.batchStatus), snap.batchStatus);
    check("重试后剩余失败数更新为 4", /4/.test(snap.retryText), snap.retryText);
    assertCardRowConsistency("重试结束", snap);

    // 4. 再次整批重新采集
    await fetch(BASE + "/api/__log/reset");
    const fullStartIndex = cdp.requests.length;
    await evaluate(cdp, `document.getElementById('startBtn').click()`);
    await waitFor(cdp, `window.__dashboardState().batchRunning === true`, 3000, "重新采集开始");
    await waitFor(cdp, `window.__dashboardState().batchRunning === false`, 20000, "重新采集结束");
    await sleep(200);
    const countsAfterFull = await requestCounts(cdp, fullStartIndex);
    check("重新采集请求全部 8 个网点且无重复",
      Object.keys(countsAfterFull).length === 8 && Object.values(countsAfterFull).every((n) => n === 1),
      JSON.stringify(countsAfterFull));
    snap = await snapshotDom(cdp);
    check("重新采集后卡片仍为 8 张", snap.cards.length === 8 && snap.rows.length === 8);

    // 5. 汇总页面文案
    check("汇总状态展示成功/失败/超时计数", /成功 3/.test(snap.batchStatus) && /失败 4/.test(snap.batchStatus) && /超时 1/.test(snap.batchStatus), snap.batchStatus);

    // 6. 页面无 JS 报错（只统计脚本异常；mock 刻意制造的 404/503/连接失败不算）
    const errors = cdp.pageErrors;
    check("页面无未捕获脚本错误", !errors || errors.length === 0, JSON.stringify(errors || []));
    if (cdp.resourceErrors.length) {
      console.log(`INFO | 浏览器记录到 ${cdp.resourceErrors.length} 条预期内的资源错误（mock 故障场景）`);
    }
  } catch (err) {
    failed = true;
    console.error("E2E 执行异常：", err.stack || err.message);
  } finally {
    // 只结束本脚本自己启动的浏览器：先用 CDP 正常关闭，再按 PID 兜底，绝不按进程名清理。
    try {
      const version = await getJson("/json/version", 3);
      if (version && version.webSocketDebuggerUrl) {
        const browser = await Cdp.connect(version.webSocketDebuggerUrl);
        await browser.send("Browser.close").catch(() => {});
        browser.close();
      }
    } catch { /* CDP 已不可用，走 PID 兜底 */ }
    if (cdp) cdp.close();
    await new Promise((resolve) => {
      if (chrome.exitCode !== null) return resolve();
      const timer = setTimeout(() => {
        try { chrome.kill("SIGKILL"); } catch { /* 已退出 */ }
        resolve();
      }, 4000);
      chrome.once("exit", () => { clearTimeout(timer); resolve(); });
      try { chrome.kill(); } catch { /* 已退出 */ }
    });
    for (let i = 0; i < 5; i++) {
      try {
        await rm(profileDir, { recursive: true, force: true });
        break;
      } catch {
        await sleep(600);
      }
    }
    if (failed) console.error("Chrome 输出摘要：\n" + chromeLogs.join("").slice(-3000));
  }

  const passed = results.filter((r) => r.pass).length;
  console.log(`\n${passed}/${results.length} 项通过`);
  if (failed || passed !== results.length) {
    process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error(err.stack || err.message);
  process.exitCode = 1;
});
