import WebSocket from "ws";

// Коды ошибок — часть контракта: computer_batch кладёт их в code каждого шага,
// и по ним агент различает «вкладки больше нет» (TabNotFound), «соединение
// умерло, следующий шаг переподключится» (ConnectionClosed) и «страница
// ответила ошибкой» (CDPHttp/EvaluateError).
const errWith = (message, code) => Object.assign(new Error(message), { code });

export const cdp = {
  port: 9222,
  setPort(port) {
    if (port === this.port) return;
    for (const client of this.sockets.values()) client.ws.close();
    this.sockets.clear();
    this.port = port;
  },
  close() {
    for (const client of this.sockets.values()) client.ws.close();
    this.sockets.clear();
  },
  nextId: 1,
  sockets: new Map(),

  async http(path) {
    let res;
    try {
      res = await fetch(`http://127.0.0.1:${cdp.port}${path}`, { signal: AbortSignal.timeout(4000) });
    } catch (e) {
      // Порт не отвечает: браузер ушёл или ещё не поднялся. Для шага пакета это
      // та же категория, что и оборванный сокет, — переподключение решает.
      const code = e?.name === "TimeoutError" ? "Timeout" : "ConnectionClosed";
      throw errWith(`CDP: браузер не отвечает на порту ${cdp.port} (${e?.message ?? e})`, code);
    }
    if (!res.ok) throw errWith(`CDP: HTTP ${res.status}`, "CDPHttp");
    return res.json();
  },

  async version() {
    return cdp.http("/json/version");
  },

  async targets() {
    const list = await cdp.http("/json/list");
    return list.filter((t) => t.type === "page" && t.webSocketDebuggerUrl);
  },

  // Документ может ещё грузиться, и тогда querySelectorAll вернёт пустоту без
  // всякой ошибки: страница открылась, а дерева ноль. Наблюдалось на свеже
  // поднятом Chrome. Поэтому перед разбором DOM ждём готовности.
  async waitReady(client, timeoutMs) {
    const deadline = Date.now() + Math.max(0, Math.min(10_000, timeoutMs));
    for (;;) {
      let state = "";
      try {
        state = await cdp.evaluate(client, "document.readyState");
      } catch {
        return { ready: false, state: "unreachable" };
      }
      if (state !== "loading") return { ready: true, state: state || "unknown" };
      if (Date.now() >= deadline) return { ready: false, state: state };
      await new Promise((r) => setTimeout(r, 100));
    }
  },

  // Открыть адрес в уже работающей вкладке. Запасной путь для /json/new: если
  // браузер отвечает, но вкладку создать не дал (он умирает прямо на глазах,
  // либо порт занят чужим процессом), навигация первой вкладки всё ещё работает.
  async navigate(url) {    const pages = await cdp.targets();
    if (!pages.length) return null;
    const s = await cdp.socket(pages[0]);
    await s.send("Page.enable", {});
    await s.send("Page.navigate", { url });
    return { id: pages[0].id, title: pages[0].title, url };
  },

  // /json/new требует именно PUT: на GET современные Chrome и Edge отвечают
  // 405. Метод держим здесь, а не в вызывающем коде, потому что забыть про PUT
  // — это молчаливое «вкладка не открылась».
  async newTab(url) {
    let res;
    try {
      res = await fetch(`http://127.0.0.1:${cdp.port}/json/new?${encodeURIComponent(url)}`, {
        method: "PUT",
        signal: AbortSignal.timeout(6000),
      });
    } catch (e) {
      const code = e?.name === "TimeoutError" ? "Timeout" : "ConnectionClosed";
      throw errWith(`CDP: браузер не отвечает на порту ${cdp.port} (${e?.message ?? e})`, code);
    }
    if (!res.ok) throw errWith(`CDP не смог открыть вкладку: HTTP ${res.status} ${await res.text().catch(() => "")}`, "CDPHttp");
    return res.json();
  },

  async socket(target) {
    const key = `${cdp.port}|${target.webSocketDebuggerUrl}`;
    const cached = cdp.sockets.get(key);
    if (cached) {
      if (cached.ws.readyState === WebSocket.OPEN) return cached;
      // Сокет умер между шагами (вкладку закрыли, браузер уронил соединение), а
      // событие close ещё не добежало до очистки кэша. Переиспользовать такой
      // сокет нельзя: следующий шаг обязан уйти по свежему соединению.
      cdp.sockets.delete(key);
      try { cached.ws.close(); } catch { /* уже закрыт */ }
    }
    const ws = new WebSocket(target.webSocketDebuggerUrl, { handshakeTimeout: 4000 });
    const client = { ws, pending: new Map(), send: null };
    const closePending = () => {
      if (cdp.sockets.get(key) === client) cdp.sockets.delete(key);
      for (const entry of client.pending.values()) entry.reject(errWith("CDP: соединение закрыто", "ConnectionClosed"));
      client.pending.clear();
    };
    ws.addEventListener("close", closePending);
    ws.addEventListener("error", closePending);
    ws.addEventListener("message", (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      const entry = client.pending.get(msg.id);
      if (!entry) return;
      client.pending.delete(msg.id);
      if (msg.error) entry.reject(errWith(msg.error.message + " (" + msg.error.code + ")", "CDPError"));
      else entry.resolve(msg.result);
    });
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve, { once: true });
      ws.addEventListener("error", () => reject(errWith("Не удалось подключиться к CDP", "ConnectionClosed")), { once: true });
      ws.addEventListener("close", () => reject(errWith("CDP закрыл соединение при запуске", "ConnectionClosed")), { once: true });
    });
    client.send = (method, params) => cdp.send(client, method, params);
    cdp.sockets.set(key, client);
    return client;
  },

  send(client, method, params = {}) {
    if (client.ws.readyState !== WebSocket.OPEN) {
      // Выкинуть мёртвый сокет из кэша, чтобы следующий шаг пакета
      // переподключился, а не споткнулся о тот же труп ещё раз.
      for (const [k, v] of cdp.sockets) if (v === client) cdp.sockets.delete(k);
      return Promise.reject(errWith("CDP: соединение закрыто", "ConnectionClosed"));
    }
    const id = cdp.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        client.pending.delete(id);
        reject(errWith(`CDP: ${method} не ответил за 15 с`, "Timeout"));
      }, 15_000);
      client.pending.set(id, {
        resolve: (v) => {
          clearTimeout(timer);
          resolve(v);
        },
        reject: (e) => {
          clearTimeout(timer);
          reject(e);
        },
      });
      try { client.ws.send(JSON.stringify({ id, method, params })); }
      catch (e) { client.pending.delete(id); clearTimeout(timer); reject(e); }
    });
  },

  async evaluate(client, expression) {
    const r = await client.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (r.exceptionDetails) {
      throw errWith(r.exceptionDetails.exception?.description ?? "JS бросил исключение", "EvaluateError");
    }
    return r.result?.value;
  },

  async pickTarget(urlHint) {
    const targets = await cdp.targets();
    if (!targets.length) throw errWith("Нет ни одной вкладки — сначала computer_browser_start", "TabNotFound");
    if (!urlHint) return targets[0];
    // Ищем и по URL, и по заголовку: параметр называется url, но вызывающий
    // почти всегда знает именно заголовок вкладки. Раньше поиск шёл только по
    // URL, и на подсказку «Вкладка не найдена» выводился список заголовков,
    // в котором нужная вкладка была — выглядит как поломка поиска.
    const lower = String(urlHint).toLowerCase();
    const hit =
      targets.find((t) => t.url.includes(urlHint))
      ?? targets.find((t) => (t.title ?? "").toLowerCase().includes(lower));
    if (!hit) {
      // Вкладка могла закрыться между шагами пакета: это ожидаемый отказ со
      // своим кодом, а не неполадка соединения.
      throw errWith(
        `Вкладка с '${urlHint}' не найдена (ищем по URL и по заголовку). ` +
          `Есть: ${targets.map((t) => `${t.title} <${t.url.slice(0, 60)}>`).join(" | ")}`,
        "TabNotFound",
      );
    }
    return hit;
  },
};

