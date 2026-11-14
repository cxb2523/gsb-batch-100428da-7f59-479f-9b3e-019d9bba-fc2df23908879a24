import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { WebSocketServer } from "ws";
import { cdp } from "../lib/cdp.mjs";
import { runBrowserBatch } from "../lib/browser.mjs";

// Локальный стенд вместо настоящего Chrome: HTTP отдаёт /json/version и
// /json/list, WebSocket отвечает на CDP-вызовы. Сценарии «вкладка закрылась
// между шагами», «соединение оборвалось» и «ссылки нет» воспроизводятся
// детерминированно, без браузера и без PowerShell-воркера.

function fakeValue(expression, state) {
  if (expression === "document.readyState") return "complete";
  if (expression === "location.href") return state.href;
  if (expression.startsWith("window.__deskProbeSelector =")) return "ok";
  if (expression.includes("__deskProbeHooked")) return "armed";
  if (expression.includes("window.__deskProbe || []")) return JSON.stringify(state.probe);
  if (expression.includes("scrollIntoView")) {
    return expression.includes("missing") ? "NULL" : JSON.stringify({ x: 10, y: 10 });
  }
  if (expression.includes("pathOf")) return JSON.stringify(state.tree);
  if (expression.includes('querySelectorAll("*")')) {
    return expression.includes("missing") ? "NULL" : JSON.stringify({ root: { tag: "div", text: "root" }, children: [] });
  }
  // Остальное — полезная нагрузка теста: "2" это число 2, "[1,2]" массив и т.д.
  try { return JSON.parse(expression); } catch { return expression; }
}

async function startFakeCdp() {
  const state = {
    tabs: [
      { id: "tab-a", type: "page", title: "Alpha", url: "http://alpha.test/" },
      { id: "tab-b", type: "page", title: "Beta", url: "http://beta.test/" },
    ],
    href: "http://alpha.test/",
    probe: [{ matchesTarget: true }],
    tree: [{ tag: "button", selector: "#ok", text: "OK" }],
    connections: 0,
    evaluations: [],
    mouseEvents: [],
    onEvaluated: null, // (expression, ws) после отправки ответа — точка для подлома сценария
  };
  const server = createServer((req, res) => {
    const send = (code, body) => {
      res.writeHead(code, { "content-type": "application/json" });
      res.end(JSON.stringify(body));
    };
    if (req.url.startsWith("/json/version")) return send(200, { Browser: "FakeCDP/1.0" });
    if (req.url.startsWith("/json/list")) {
      return send(200, state.tabs.map((t) => ({
        ...t,
        webSocketDebuggerUrl: `ws://127.0.0.1:${server.address().port}/devtools/page/${t.id}`,
      })));
    }
    return send(404, {});
  });
  const wss = new WebSocketServer({ server });
  wss.on("connection", (ws) => {
    state.connections++;
    ws.on("message", (raw) => {
      const msg = JSON.parse(String(raw));
      if (msg.method === "Runtime.evaluate") {
        const expression = msg.params.expression;
        state.evaluations.push(expression);
        ws.send(JSON.stringify({ id: msg.id, result: { result: { value: fakeValue(expression, state) } } }));
        state.onEvaluated?.(expression, ws);
        return;
      }
      if (msg.method === "Input.dispatchMouseEvent") state.mouseEvents.push(msg.params);
      ws.send(JSON.stringify({ id: msg.id, result: {} }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { state, server, wss, port: server.address().port };
}

async function stopFakeCdp(fake) {
  cdp.close();
  for (const ws of fake.wss.clients) ws.terminate();
  await new Promise((r) => fake.wss.close(r));
  await new Promise((r) => fake.server.close(r));
  cdp.setPort(9222);
}

test("вкладка исчезла между шагами: зависимые шаги падают с TabNotFound, а не пропускаются", async () => {
  const fake = await startFakeCdp();
  cdp.setPort(fake.port);
  try {
    fake.state.onEvaluated = (expression) => {
      if (expression === "1") fake.state.tabs = fake.state.tabs.filter((t) => t.id !== "tab-a");
    };
    const r = await runBrowserBatch([
      { tool: "browser_eval", args: { url: "alpha.test", expression: "1" }, stopOnError: false },
      { tool: "browser_eval", args: { url: "alpha.test", expression: "2" }, stopOnError: false },
      { tool: "browser_eval", args: { url: "alpha.test", expression: "3" }, stopOnError: false },
    ]);
    assert.equal(r.executed, 3);
    assert.equal(r.allOk, false);
    assert.equal(r.steps[0].ok, true);
    assert.equal(r.steps[0].data.value, 1);
    for (const s of r.steps.slice(1)) {
      assert.equal(s.ok, false);
      assert.equal(s.code, "TabNotFound");
      assert.match(s.error, /alpha\.test/);
    }
    // Оценка на странице выполнилась ровно один раз — до исчезновения вкладки.
    assert.deepEqual(fake.state.evaluations, ["1"]);
  } finally {
    await stopFakeCdp(fake);
  }
});

test("stopOnError по умолчанию останавливает пакет на упавшем браузерном шаге", async () => {
  const fake = await startFakeCdp();
  cdp.setPort(fake.port);
  try {
    fake.state.onEvaluated = (expression) => {
      if (expression === "1") fake.state.tabs = fake.state.tabs.filter((t) => t.id !== "tab-a");
    };
    const r = await runBrowserBatch([
      { tool: "browser_eval", args: { url: "alpha.test", expression: "1" } },
      { tool: "browser_eval", args: { url: "alpha.test", expression: "2" } },
      { tool: "browser_eval", args: { url: "alpha.test", expression: "3" } },
    ]);
    assert.equal(r.executed, 2);
    assert.equal(r.stoppedAt, 1);
    assert.equal(r.steps[1].code, "TabNotFound");
    assert.deepEqual(fake.state.evaluations, ["1"]);
  } finally {
    await stopFakeCdp(fake);
  }
});

test("шаг после обрыва CDP переподключается, а не использует мёртвый сокет", async () => {
  const fake = await startFakeCdp();
  cdp.setPort(fake.port);
  try {
    fake.state.onEvaluated = (expression, ws) => {
      if (expression === "1") ws.close(); // браузер уронил соединение между шагами
    };
    const r = await runBrowserBatch([
      { tool: "browser_eval", args: { url: "alpha.test", expression: "1" } },
      { tool: "browser_eval", args: { url: "alpha.test", expression: "2" } },
    ]);
    assert.equal(r.allOk, true);
    assert.deepEqual(r.steps.map((s) => s.data.value), [1, 2]);
    assert.equal(fake.state.connections, 2, "второй шаг обязан открыть новое соединение");
  } finally {
    await stopFakeCdp(fake);
  }
});

test("битая ссылка и ссылка на провалившийся шаг отклоняются, в действие ничего не уходит", async () => {
  const fake = await startFakeCdp();
  cdp.setPort(fake.port);
  try {
    const r = await runBrowserBatch([
      { tool: "browser_eval", args: { url: "alpha.test", expression: "1" }, stopOnError: false },
      { tool: "browser_eval", args: { url: "alpha.test", expression: "${steps.0.missing}" }, stopOnError: false },
      { tool: "browser_eval", args: { url: "gone.test", expression: "5" }, stopOnError: false },
      { tool: "browser_eval", args: { url: "alpha.test", expression: "${steps.2.value}" }, stopOnError: false },
    ]);
    assert.equal(r.executed, 4);
    assert.equal(r.allOk, false);
    assert.equal(r.steps[0].ok, true);
    assert.equal(r.steps[1].code, "InvalidArgument");
    assert.match(r.steps[1].error, /Подстановка не сработала/);
    assert.equal(r.steps[2].code, "TabNotFound");
    assert.equal(r.steps[3].code, "InvalidArgument");
    assert.match(r.steps[3].error, /провалился/);
    // До страницы дошла только первая оценка: отказанные шаги не выполнялись.
    assert.deepEqual(fake.state.evaluations, ["1"]);
  } finally {
    await stopFakeCdp(fake);
  }
});

test("ссылка сохраняет тип: число из eval уходит в clickCount числом, а не строкой", async () => {
  const fake = await startFakeCdp();
  cdp.setPort(fake.port);
  try {
    const r = await runBrowserBatch([
      { tool: "browser_eval", args: { url: "alpha.test", expression: "2" } },
      { tool: "browser_click", args: { url: "alpha.test", selector: "#ok", clickCount: "${steps.0.value}" } },
    ]);
    assert.equal(r.allOk, true);
    assert.equal(r.steps[1].data.verified, true);
    // clickCount=2: две пары pressed/released с нарастающим счётчиком. Строка "2"
    // не прошла бы схему z.number() — шаг упал бы с InvalidArgument.
    assert.deepEqual(fake.state.mouseEvents.map((e) => [e.type, e.clickCount]), [
      ["mousePressed", 1], ["mouseReleased", 1], ["mousePressed", 2], ["mouseReleased", 2],
    ]);
  } finally {
    await stopFakeCdp(fake);
  }
});

test("browser_list и browser_tree идут шагами пакета и отдают данные дальше", async () => {
  const fake = await startFakeCdp();
  cdp.setPort(fake.port);
  try {
    const r = await runBrowserBatch([
      { tool: "computer_browser_list" },
      { tool: "browser_tree", args: { url: "alpha.test" } },
    ]);
    assert.equal(r.allOk, true);
    assert.equal(r.steps[0].data.tabs.length, 2);
    assert.equal(r.steps[1].data.count, 1);
    assert.equal(r.steps[1].data.ready, true);
    assert.equal(r.steps[1].data.elements[0].selector, "#ok");
  } finally {
    await stopFakeCdp(fake);
  }
});
