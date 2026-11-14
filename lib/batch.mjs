const errWith = (message, code) => Object.assign(new Error(message), { code });

const STEP_REF = /\$\{steps\.(\d+)((?:\.[^\s.[\]]+|\[\d+\])*)\}/g;

function resolveStepRef(str, done, where) {
  const misses = [];
  let exact;
  const value = str.replace(STEP_REF, (_, idx, path) => {
    const i = Number(idx);
    const prev = done.find((o) => o.index === i);
    if (!prev) { misses.push(`шаг ${i} ещё не выполнен`); return ""; }
    if (!prev.ok) { misses.push(`шаг ${i} провалился: ${prev.error ?? "?"}`); return ""; }
    let cur = prev.data;
    for (const part of path.match(/[^.[\]]+/g) ?? []) {
      if (cur == null || cur === undefined) { misses.push(`нет ${path} в шаге ${i}`); return ""; }
      if (!Object.hasOwn(Object(cur), part)) { misses.push(`нет ${path} в шаге ${i}`); return ""; }
      cur = cur[part];
    }
    if (cur === undefined || cur === null) { misses.push(`нет ${path} в шаге ${i}`); return ""; }
    if (_ === str) exact = { value: cur };
    return typeof cur === "object" ? JSON.stringify(cur) : String(cur);
  });
  // Молча подставить пустую строку хуже, чем отказать: действие ушло бы не туда.
  // Страховка от кривой ссылки: если после подстановки остался кусок "${steps.",
  // значит шаблон написан не по грамматике, и его текст не должен уехать в действие.
  if (value.includes("${steps.")) misses.push(`ссылка не разобрана: "${value}"`);
  if (misses.length) {
    throw errWith(
      `Подстановка не сработала (${misses.join("; ")}) в аргументе ${where}: "${str}". ` +
        `Ссылка вида ${"${steps.Н.путь}"} читает результат шага Н из этого же пакета.`,
      "InvalidArgument",
    );
  }
  return exact ? exact.value : value;
}

export function resolveArgs(args, done) {
  if (typeof args === "string" && args.includes("${steps.")) return resolveStepRef(args, done, "value");
  if (args === null || typeof args !== "object") return args;
  if (Array.isArray(args)) return args.map((v) => resolveArgs(v, done));
  const out = Object.create(null);
  for (const [k, v] of Object.entries(args)) {
    if (typeof v === "string" && v.includes("${steps.")) out[k] = resolveStepRef(v, done, k);
    else out[k] = resolveArgs(v, done);
  }
  return out;
}

// Общий цикл пакета. Через него идут и UIA-шаги (host.execute ведёт в воркер),
// и браузерные CDP-шаги (host.execute ведёт в lib/browser.mjs), поэтому и
// сервер, и тесты с локальным WS-стендом вместо браузера гоняют один и тот же
// код. Инвариант: каждый шаг заканчивается записью { ok, code? } — «молча
// пропустили» недопустимо, агент строит следующий шаг на результате предыдущего.
//
// host = {
//   budgetMs, signal,
//   resolveTool(given) -> { tool, canonical },   // нормализация имени и алиасы
//   isKnown(tool) -> bool, knownList() -> string,
//   isDisabled(canonical) -> bool,               // DESK_DISABLE_TOOLS
//   schemaFor(canonical) -> zod-схема,           // обязательна: без схемы отказ
//   execute(tool, args, remainingMs) -> Promise<data>,
// }
export async function runBatchSteps(steps, host) {
  const expires = Date.now() + (host.budgetMs ?? 45_000);
  const out = [];
  let stoppedAt = null;
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i] || {};
    const given = String(s.tool ?? "");
    const stopOnError = s.stopOnError !== false;
    const resolved = host.resolveTool(given);
    const reject = (code, error) => {
      out.push({ index: i, tool: given, ok: false, code, error });
      if (stopOnError) stoppedAt = i;
      return stopOnError;
    };
    if (!resolved || !host.isKnown(resolved.tool)) {
      if (reject("InvalidArgument", `Неизвестный инструмент: '${given}'. Доступны: ${host.knownList()}`)) break;
      continue;
    }
    // Отключённый через DESK_DISABLE_TOOLS инструмент batch тоже не выполняет:
    // иначе список отключённых обходится одной пачкой, и защита дырявится.
    if (host.isDisabled?.(resolved.canonical)) {
      if (reject("NotSupported", `Инструмент '${given}' отключён через DESK_DISABLE_TOOLS`)) break;
      continue;
    }
    try {
      if (host.signal?.aborted) throw errWith("Пакет отменён клиентом", "Cancelled");
      const args = resolveArgs(s.args ?? {}, out);
      const schema = host.schemaFor?.(resolved.canonical);
      const parsed = schema?.safeParse(args);
      if (!parsed?.success) {
        throw errWith("Некорректные аргументы " + resolved.canonical + ": " + (parsed?.error?.message ?? "нет схемы"), "InvalidArgument");
      }
      const remaining = expires - Date.now();
      if (remaining <= 0) throw errWith("Бюджет computer_batch исчерпан", "Timeout");
      const data = await host.execute(resolved.tool, parsed.data, remaining);
      out.push({ index: i, tool: given, ok: true, data });
    } catch (e) {
      out.push({ index: i, tool: given, ok: false, code: e.code || "WorkerError", error: e.message });
      if (stopOnError || e.code === "Cancelled" || Date.now() >= expires) { stoppedAt = i; break; }
    }
  }
  return {
    executed: out.length,
    stoppedAt,
    allOk: out.every((x) => x.ok),
    steps: out,
  };
}

