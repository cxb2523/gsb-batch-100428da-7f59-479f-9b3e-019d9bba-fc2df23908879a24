// Браузерные (CDP) шаги computer_batch и обработчики computer_browser_*.
// Вынесены из server.mjs, чтобы один и тот же код обслуживал прямые вызовы
// инструментов, шаги пакета и тесты с локальным WebSocket-стендом вместо
// настоящего браузера. Каждая ошибка несёт code (TabNotFound,
// ConnectionClosed, EvaluateError, ...): шаг пакета кладёт его в свой code,
// и агент видит, какой именно шаг и почему упал.
import { z } from "zod";
import { cdp } from "./cdp.mjs";
import { runBatchSteps } from "./batch.mjs";

const errWith = (message, code) => Object.assign(new Error(message), { code });

const PROBE_JS = `(() => {
  window.__deskProbe = [];
  if (!window.__deskProbeHooked) {
    window.__deskProbeHooked = true;
    document.addEventListener("click", (e) => {
      const el = e.target;
      window.__deskProbe.push({
        tag: el.tagName,
        text: (el.innerText || el.value || el.getAttribute("aria-label") || "").toString().slice(0, 120),
        id: el.id || null,
        testid: el.getAttribute("data-testid"),
        matchesTarget: !!el.closest(window.__deskProbeSelector),
        trusted: e.isTrusted
      });
    }, true);
  }
  return "armed";
})()`;

const READ_PROBE_JS = `JSON.stringify(window.__deskProbe || [])`;

const TREE_JS = `(() => {
  const SEL = 'a,button,input,select,textarea,summary,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=switch],[role=combobox],[contenteditable=true],[onclick]';
  // a:nth-of-type(9) вне контекста родителя не находит ничего — индекс
  // считается среди соседей конкретного родителя. Поэтому строим абсолютный
  // путь вверх через :nth-child, он работает в document.querySelector.
  const pathOf = (el) => {
    const parts = [];
    let cur = el;
    for (let i = 0; cur && cur.nodeType === 1 && i < 6; i++) {
      const parent = cur.parentElement;
      if (!parent) { parts.unshift(cur.tagName.toLowerCase()); break; }
      const idx = [...parent.children].indexOf(cur) + 1;
      parts.unshift(cur.tagName.toLowerCase() + ":nth-child(" + idx + ")");
      if (cur === document.body) break;
      cur = parent;
    }
    return parts.join(" > ");
  };
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (r.bottom < 0 || r.top > innerHeight) continue;
    const testid = el.getAttribute("data-testid");
    const selector = el.id
      ? "#" + CSS.escape(el.id)
      : (testid ? '[data-testid="' + testid.replace(/"/g, '\\\\"') + '"]' : pathOf(el));
    out.push({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute("role") || null,
      text: (el.innerText || el.value || el.getAttribute("aria-label") || el.title || "").toString().trim().slice(0, 120),
      selector,
      matches: document.querySelectorAll(selector).length,
      id: el.id || null,
      testid: testid || null,
      name: el.getAttribute("name") || null,
      value: (el.value ?? null),
      checked: el.checked ?? null,
      disabled: !!el.disabled,
      rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }
    });
    if (out.length >= 400) break;
  }
  return JSON.stringify(out);
})()`;

const DESCENDANTS_JS = (selector) => `(() => {
  const root = document.querySelector(${JSON.stringify(selector)});
  if (!root) return "NULL";
  const out = [];
  for (const el of root.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    const text = (el.innerText || "").toString().trim().slice(0, 80);
    if (r.width < 2 || r.height < 2) continue;
    out.push({ tag: el.tagName.toLowerCase(), role: el.getAttribute("role") || null, text, rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } });
    if (out.length >= 200) break;
  }
  return JSON.stringify({ root: { tag: root.tagName.toLowerCase(), text: (root.innerText || "").toString().trim().slice(0, 300) }, children: out });
})()`;

// Схемы аргументов — единственный источник: server.mjs регистрирует по ним
// инструменты, а пакет валидирует шаги. Разнобоя «прямой вызов принимает,
// пакет отклоняет» быть не должно.
export const browserInputSchemas = {
  browser_list: {},
  browser_tree: {
    url: z.string().optional().describe("подстрока URL вкладки; пусто — первая"),
    timeoutMs: z.number().int().optional().default(2000).describe("сколько ждать готовности документа перед разбором DOM"),
  },
  browser_descendants: {
    selector: z.string(),
    url: z.string().optional(),
  },
  browser_click: {
    selector: z.string(),
    url: z.string().optional(),
    button: z.enum(["left", "middle", "right"]).optional().default("left"),
    clickCount: z.number().int().min(1).max(3).optional().default(1),
  },
  browser_eval: {
    expression: z.string(),
    url: z.string().optional(),
  },
};

// Инструменты, которые разрешено звать шагами computer_batch. browser_start
// сюда не входит: подъём браузера — отдельная долгая операция со своими
// ветками (уже запущен / не найден exe), а не шаг цепочки «прочитал → нажал».
export const BROWSER_STEPS = new Set(Object.keys(browserInputSchemas));

async function browserList() {
  const [v, t] = [await cdp.version(), await cdp.targets()];
  return { browser: v.Browser, port: cdp.port, tabs: t.map((x) => ({ id: x.id, title: x.title, url: x.url })) };
}

async function browserTree(a) {
  const target = await cdp.pickTarget(a.url);
  const client = await cdp.socket(target);
  const ready = await cdp.waitReady(client, a.timeoutMs ?? 2000);
  const raw = await cdp.evaluate(client, TREE_JS);
  const items = JSON.parse(raw);
  return { url: target.url, title: target.title, count: items.length, ready: ready.ready, elements: items };
}

async function browserDescendants(a) {
  const target = await cdp.pickTarget(a.url);
  const client = await cdp.socket(target);
  const raw = await cdp.evaluate(client, DESCENDANTS_JS(a.selector));
  if (raw === "NULL") throw errWith(`Селектор не найден: ${a.selector}`, "ElementNotFound");
  return { url: target.url, ...JSON.parse(raw) };
}

async function browserClick(a) {
  const target = await cdp.pickTarget(a.url);
  const client = await cdp.socket(target);

  const locate = () => cdp.evaluate(client, `(() => {
      const el = document.querySelector(${JSON.stringify(a.selector)});
      if (!el) return "NULL";
      el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
      const r = el.getBoundingClientRect();
      return JSON.stringify({ x: r.x + r.width / 2, y: r.y + r.height / 2 });
    })()`);

  const strike = async () => {
    for (let i = 0; i < a.clickCount; i++) {
      await client.send("Input.dispatchMouseEvent", {
        type: "mousePressed", x: BOX.x, y: BOX.y, button: a.button, clickCount: i + 1,
      });
      await client.send("Input.dispatchMouseEvent", {
        type: "mouseReleased", x: BOX.x, y: BOX.y, button: a.button, clickCount: i + 1,
      });
      if (i + 1 < a.clickCount) await new Promise((r) => setTimeout(r, 60));
    }
  };

  await cdp.evaluate(client, "window.__deskProbeSelector = " + JSON.stringify(a.selector));
  await cdp.evaluate(client, PROBE_JS);
  const before = await cdp.evaluate(client, "location.href");

  // Без bringToFront Chrome отбрасывает синтетический ввод, если вкладка не
  // на переднем плане: Input.dispatchMouseEvent молча уходит в никуда, а
  // модель получает verified:false и гадает почему.
  try {
    await client.send("Page.enable");
    await client.send("Page.bringToFront");
    await new Promise((r) => setTimeout(r, 250));
  } catch { /* браузер может быть в другой вкладке — не критично */ }

  let raw = await locate();
  if (raw === "NULL") throw errWith(`Селектор не найден: ${a.selector}`, "ElementNotFound");
  let BOX = JSON.parse(raw);
  await strike();
  await new Promise((r) => setTimeout(r, 350));
  let hits = JSON.parse(await cdp.evaluate(client, READ_PROBE_JS));
  let after = await cdp.evaluate(client, "location.href");
  let navigated = before !== after;
  let retried = false;

  // Промах по координате после плавной прокрутки — обычное дело: элемент
  // уехал между замером и кликом. Один ретрай с перезамером дешевле, чем
  // выдавать модели ложь verified:false.
  if (hits.length === 0 && !navigated) {
    retried = true;
    raw = await locate();
    if (raw !== "NULL") {
      BOX = JSON.parse(raw);
      await strike();
      await new Promise((r) => setTimeout(r, 400));
      hits = JSON.parse(await cdp.evaluate(client, READ_PROBE_JS));
      after = await cdp.evaluate(client, "location.href");
      navigated = before !== after;
    }
  }

  return {
    ok: true,
    selector: a.selector,
    verified: hits.some((hit) => hit.matchesTarget === true),
    via: hits.some((hit) => hit.matchesTarget === true) ? "probe" : navigated ? "navigation" : "none",
    retried,
    hit: hits.find((hit) => hit.matchesTarget === true) ?? hits[0] ?? null,
    urlBefore: before,
    urlAfter: after,
  };
}

async function browserEval(a) {
  const target = await cdp.pickTarget(a.url);
  const client = await cdp.socket(target);
  return { url: target.url, value: await cdp.evaluate(client, a.expression) };
}

// У каждого отказа обязан быть код: без него шаг пакета получил бы общий
// WorkerError, и «вкладка закрылась» не отличалась бы от «страница бросила
// исключение». Свои коды (TabNotFound, ConnectionClosed, ...) уже проставлены
// в lib/cdp.mjs; сюда доходят только непредвиденные — им даём CDPError.
const withCode = (fn) => async (a) => {
  try {
    return await fn(a ?? {});
  } catch (e) {
    if (!e || typeof e.code !== "string" || !e.code) e.code = "CDPError";
    throw e;
  }
};

export const browserSteps = {
  browser_list: withCode(browserList),
  browser_tree: withCode(browserTree),
  browser_descendants: withCode(browserDescendants),
  browser_click: withCode(browserClick),
  browser_eval: withCode(browserEval),
};

const browserSchemas = new Map(
  Object.entries(browserInputSchemas).map(([tool, shape]) => ["computer_" + tool, z.object(shape)]),
);

// Пакет только из браузерных шагов на том же цикле, что и computer_batch на
// сервере. Нужен тестам: они поднимают локальный WS-стенд вместо Chrome и
// прогоняют цепочки «вкладка исчезла», «соединение оборвалось», «ссылки нет»
// без PowerShell-воркера и без настоящего браузера.
export function runBrowserBatch(steps, budgetMs = 45_000, signal) {
  return runBatchSteps(steps, {
    budgetMs,
    signal,
    resolveTool: (given) => {
      const tool = given.startsWith("computer_") ? given.slice("computer_".length) : given;
      return { tool, canonical: `computer_${tool}` };
    },
    isKnown: (tool) => BROWSER_STEPS.has(tool),
    knownList: () => [...BROWSER_STEPS].join(", "),
    schemaFor: (canonical) => browserSchemas.get(canonical),
    execute: (tool, args) => browserSteps[tool](args),
  });
}
